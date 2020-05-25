# THIS FILE IS PART OF THE CYLC SUITE ENGINE.
# Copyright (C) NIWA & British Crown (Met Office) & Contributors.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Wrangle task proxies to manage the workflow.

"""

from fnmatch import fnmatchcase
import json
from time import time
from queue import Queue, Empty
from itertools import chain

from cylc.flow.parsec.OrderedDict import OrderedDict

from cylc.flow import LOG
from cylc.flow.cycling.loader import get_point, standardise_point_string
from cylc.flow.exceptions import SuiteConfigError, PointParsingError
from cylc.flow.suite_status import StopMode
from cylc.flow.task_action_timer import TaskActionTimer
from cylc.flow.task_events_mgr import (
    CustomTaskEventHandlerContext, TaskEventMailContext,
    TaskJobLogsRetrieveContext)
from cylc.flow.task_id import TaskID
from cylc.flow.task_job_logs import get_task_job_id
from cylc.flow.task_proxy import TaskProxy
from cylc.flow.task_state import (
    TASK_STATUSES_ACTIVE, TASK_STATUSES_FAILURE, TASK_STATUSES_NOT_STALLED,
    TASK_STATUSES_SUCCESS, TASK_STATUS_WAITING, TASK_STATUS_EXPIRED,
    TASK_STATUS_QUEUED, TASK_STATUS_READY, TASK_STATUS_SUBMITTED,
    TASK_STATUS_SUBMIT_FAILED, TASK_STATUS_SUBMIT_RETRYING,
    TASK_STATUS_RUNNING, TASK_STATUS_SUCCEEDED, TASK_STATUS_FAILED,
    TASK_STATUS_RETRYING, TASK_OUTPUT_SUCCEEDED, TASK_OUTPUT_FAILED,
    TASK_OUTPUT_EXPIRED)
from cylc.flow.wallclock import get_current_time_string


class TaskPool(object):
    """Task pool of a suite."""

    ERR_PREFIX_TASKID_MATCH = "No matching tasks found: "
    ERR_PREFIX_TASK_NOT_ON_SEQUENCE = "Invalid cycle point for task: "

    def __init__(self, config, suite_db_mgr, task_events_mgr, job_pool):
        self.config = config
        self.stop_point = config.final_point
        self.suite_db_mgr = suite_db_mgr
        self.task_events_mgr = task_events_mgr
        # TODO: a better a better way to spawn off of task events?:
        self.task_events_mgr.spawn_func = self.spawn_and_update_children
        self.job_pool = job_pool

        self.do_reload = False
        self.custom_runahead_limit = self.config.get_custom_runahead_limit()
        self.max_future_offset = None
        self._prev_runahead_base_point = None
        self.max_num_active_cycle_points = (
            self.config.get_max_num_active_cycle_points())
        self._prev_runahead_sequence_points = None

        self.pool = {}
        self.runahead_pool = {}
        self.myq = {}
        self.queues = {}
        self.assign_queues()

        self.pool_list = []
        self.rhpool_list = []
        self.pool_changed = False
        self.rhpool_changed = False
        self.pool_changes = []

        self.is_held = False
        self.hold_point = None
        self.held_future_tasks = []

        self.stop_task_id = None
        self.stop_task_finished = False

        self.orphans = []
        self.task_name_list = self.config.get_task_name_list()

    def set_stop_task(self, task_id):
        """Set stop after a task."""
        name = TaskID.split(task_id)[0]
        if name in self.config.get_task_name_list():
            task_id = TaskID.get_standardised_taskid(task_id)
            LOG.info("Setting stop task: " + task_id)
            self.stop_task_id = task_id
            self.stop_task_finished = False
            self.suite_db_mgr.put_suite_stop_task(task_id)
        else:
            LOG.warning("Requested stop task name does not exist: %s" % name)

    def stop_task_done(self):
        """Return True if stop task has succeeded."""
        if self.stop_task_id is not None and self.stop_task_finished:
            LOG.info("Stop task %s finished" % self.stop_task_id)
            self.stop_task_id = None
            self.stop_task_finished = False
            self.suite_db_mgr.delete_suite_stop_task()
            return True
        else:
            return False

    def assign_queues(self):
        """self.myq[taskname] = qfoo"""
        self.myq.clear()
        for queue, qconfig in self.config.cfg['scheduling']['queues'].items():
            self.myq.update((name, queue) for name in qconfig['members'])

    def add_to_runahead_pool(self, itask, is_new=True):
        """Add a new task to the runahead pool if possible.

        Tasks whose recurrences allow them to spawn beyond the suite
        stop point are added to the pool in the held state, ready to be
        released if the suite stop point is changed.

        """

        # do not add if a task with the same ID already exists
        if self.get_task_by_id(itask.identity) is not None:
            LOG.warning(
                '%s cannot be added to pool: task ID already exists' %
                itask.identity)
            return

        # do not add if an inserted task is beyond its own stop point
        # (note this is not the same as recurrence bounds)
        if itask.stop_point and itask.point > itask.stop_point:
            LOG.info(
                '%s not adding to pool: beyond task stop cycle' %
                itask.identity)
            return

        # add in held state if beyond the suite hold point
        if self.hold_point and itask.point > self.hold_point:
            LOG.info(
                "[%s] -holding (beyond suite hold point) %s",
                itask, self.hold_point)
            itask.state.reset(is_held=True)
        elif (self.stop_point and itask.point <= self.stop_point and
                self.task_has_future_trigger_overrun(itask)):
            LOG.info("[%s] -holding (future trigger beyond stop point)", itask)
            self.held_future_tasks.append(itask.identity)
            itask.state.reset(is_held=True)
        elif (
                self.is_held
                and itask.state(
                    TASK_STATUS_WAITING,
                    is_held=False
                )
        ):
            # Hold newly-spawned tasks in a held suite (e.g. due to manual
            # triggering of a held task).
            itask.state.reset(is_held=True)

        # add to the runahead pool
        self.runahead_pool.setdefault(itask.point, OrderedDict())
        self.runahead_pool[itask.point][itask.identity] = itask
        self.rhpool_changed = True

        # add row to "task_states" table
        if is_new and itask.submit_num == 0:
            self.suite_db_mgr.put_insert_task_states(itask, {
                "time_created": get_current_time_string(),
                "time_updated": get_current_time_string(),
                "status": itask.state.status})
            if itask.state.outputs.has_custom_triggers():
                self.suite_db_mgr.put_insert_task_outputs(itask)
        return itask

    def release_runahead_tasks(self):
        """Restrict the number of active cycle points.

        Return True if any runahead tasks released, else False.

        """
        released = False
        if not self.runahead_pool:
            return released

        # Any finished tasks can be released immediately (this can happen at
        # restart when all tasks are initially loaded into the runahead pool).
        for itask_id_maps in self.runahead_pool.copy().values():
            for itask in itask_id_maps.copy().values():
                if itask.state(
                    TASK_STATUS_FAILED,
                    TASK_STATUS_SUCCEEDED,
                    TASK_STATUS_EXPIRED
                ):
                    self.release_runahead_task(itask)
                    released = True

        limit = self.max_num_active_cycle_points

        points = []
        for point, itasks in sorted(
                self.get_tasks_by_point(incl_runahead=True).items()):
            has_unfinished_itasks = False
            for itask in itasks:
                if not itask.state(
                    TASK_STATUS_FAILED,
                    TASK_STATUS_SUCCEEDED,
                    TASK_STATUS_EXPIRED
                ):
                    has_unfinished_itasks = True
                    break
            if not points and not has_unfinished_itasks:
                # We need to begin with an unfinished cycle point.
                continue
            points.append(point)

        if not points:
            return False

        # Get the earliest point with unfinished tasks.
        runahead_base_point = min(points)

        # Get all cycling points possible after the runahead base point.
        if (self._prev_runahead_base_point is not None and
                runahead_base_point == self._prev_runahead_base_point):
            # Cache for speed.
            sequence_points = self._prev_runahead_sequence_points
        else:
            sequence_points = []
            for sequence in self.config.sequences:
                point = runahead_base_point
                for _ in range(limit):
                    point = sequence.get_next_point(point)
                    if point is None:
                        break
                    sequence_points.append(point)
            sequence_points = set(sequence_points)
            self._prev_runahead_sequence_points = sequence_points
            self._prev_runahead_base_point = runahead_base_point

        points = set(points).union(sequence_points)

        if self.custom_runahead_limit is None:
            # Calculate which tasks to release based on a maximum number of
            # active cycle points (active meaning non-finished tasks).
            latest_allowed_point = sorted(points)[:limit][-1]
            if self.max_future_offset is not None:
                # For the first N points, release their future trigger tasks.
                latest_allowed_point += self.max_future_offset
        else:
            # Calculate which tasks to release based on a maximum duration
            # measured from the oldest non-finished task.
            latest_allowed_point = (
                runahead_base_point + self.custom_runahead_limit)

            if (self._prev_runahead_base_point is None or
                    self._prev_runahead_base_point != runahead_base_point):
                if self.custom_runahead_limit < self.max_future_offset:
                    LOG.warning(
                        ('custom runahead limit of %s is less than ' +
                         'future triggering offset %s: suite may stall.') % (
                            self.custom_runahead_limit,
                            self.max_future_offset
                        )
                    )
            self._prev_runahead_base_point = runahead_base_point
        if self.stop_point and latest_allowed_point > self.stop_point:
            latest_allowed_point = self.stop_point

        for point, itask_id_map in self.runahead_pool.copy().items():
            if point <= latest_allowed_point:
                for itask in itask_id_map.copy().values():
                    self.release_runahead_task(itask)
                    released = True
        return released

    def load_db_task_pool_for_restart(self, row_idx, row):
        """Load a task from previous task pool.

        Output completion status is loaded from the DB, and tasks recorded
        as submitted or running are polled to confirm their true status.

        """
        if row_idx == 0:
            LOG.info("LOADING task proxies")
        # Create a task proxy corresponding to this DB entry.
        (cycle, name, is_late, status, satisfied, parents_finished, is_held,
         submit_num, _, user_at_host, time_submit, time_run, timeout,
         outputs_str) = row
        try:
            itask = TaskProxy(
                self.config.get_taskdef(name),
                self.config.initial_point,
                get_point(cycle),
                is_held=is_held,
                submit_num=submit_num,
                is_late=bool(is_late))
        except SuiteConfigError:
            LOG.exception((
                'ignoring task %s from the suite run database\n'
                '(its task definition has probably been deleted).'
            ) % name)
        except Exception:
            LOG.exception('could not load task %s' % name)
        else:
            if status in (
                    TASK_STATUS_SUBMITTED,
                    TASK_STATUS_RUNNING
            ):
                # update the task proxy with user@host
                try:
                    itask.task_owner, itask.task_host = user_at_host.split(
                        "@", 1)
                except (AttributeError, ValueError):
                    itask.task_owner = None
                    itask.task_host = user_at_host
                if time_submit:
                    itask.set_summary_time('submitted', time_submit)
                if time_run:
                    itask.set_summary_time('started', time_run)
                if timeout is not None:
                    itask.timeout = timeout

            elif status in (
                    TASK_STATUS_QUEUED,
                    TASK_STATUS_READY,
            ):
                # reset to waiting as these had not been submitted yet.
                # SOD: is this necessary?
                status = TASK_STATUS_WAITING

            # Running or finished task can have completed custom outputs.
            if itask.state(
                    TASK_STATUS_RUNNING,
                    TASK_STATUS_FAILED,
                    TASK_STATUS_SUCCEEDED
            ):
                try:
                    for message in json.loads(outputs_str).values():
                        itask.state.outputs.set_completion(message, True)
                except (AttributeError, TypeError, ValueError):
                    # Back compat for <=7.6.X
                    # Each output in separate line as "trigger=message"
                    try:
                        for output in outputs_str.splitlines():
                            itask.state.outputs.set_completion(
                                output.split("=", 1)[1], True)
                    except AttributeError:
                        pass

            if user_at_host:
                itask.summary['job_hosts'][int(submit_num)] = user_at_host
            LOG.info("+ %s.%s %s%s" % (
                name, cycle, status, ' (held)' if is_held else ''))

            # Update prerequisite satisfaction status from DB
            sat = {}
            for k, v in json.loads(satisfied).items():
                sat[tuple(json.loads(k))] = v
            # TODO (from Oliver's PR review):
            #   Wait, what, the keys to a JSON dictionary are themselves JSON
            #     :vomiting_face:!
            #   This should be converted to its own DB table pre-8.0.0.
            for pre in itask.state.prerequisites:
                for k, v in pre.satisfied.items():
                    pre.satisfied[k] = sat[k]

            # Update parents-finished status from DB
            itask.parents_finished.update({
                tuple(json.loads(k)): v
                for k, v in json.loads(parents_finished).items()
            })

            itask.state.reset(status)
            self.add_to_runahead_pool(itask, is_new=False)

    def load_db_task_action_timers(self, row_idx, row):
        """Load a task action timer, e.g. event handlers, retry states."""
        if row_idx == 0:
            LOG.info("LOADING task action timers")
        (cycle, name, ctx_key_raw, ctx_raw, delays_raw, num, delay,
         timeout) = row
        id_ = TaskID.get(name, cycle)
        try:
            # Extract type namedtuple variables from JSON strings
            ctx_key = json.loads(str(ctx_key_raw))
            ctx_data = json.loads(str(ctx_raw))
            for known_cls in [
                    CustomTaskEventHandlerContext,
                    TaskEventMailContext,
                    TaskJobLogsRetrieveContext]:
                if ctx_data and ctx_data[0] == known_cls.__name__:
                    ctx = known_cls(*ctx_data[1])
                    break
            else:
                ctx = ctx_data
                if ctx is not None:
                    ctx = tuple(ctx)
            delays = json.loads(str(delays_raw))
        except ValueError:
            LOG.exception(
                "%(id)s: skip action timer %(ctx_key)s" %
                {"id": id_, "ctx_key": ctx_key_raw})
            return
        if ctx_key == "poll_timer" or ctx_key[0] == "poll_timers":
            # "poll_timers" for back compat with <=7.6.X
            itask = self.get_task_by_id(id_)
            if itask is None:
                LOG.warning("%(id)s: task not found, skip" % {"id": id_})
                return
            itask.poll_timer = TaskActionTimer(
                ctx, delays, num, delay, timeout)
        elif ctx_key[0] == "try_timers":
            itask = self.get_task_by_id(id_)
            if itask is None:
                LOG.warning("%(id)s: task not found, skip" % {"id": id_})
                return
            itask.try_timers[ctx_key[1]] = TaskActionTimer(
                ctx, delays, num, delay, timeout)
        elif ctx:
            key1, submit_num = ctx_key
            # Convert key1 to type tuple - JSON restores as type list
            # and this will not previously have been converted back
            if isinstance(key1, list):
                key1 = tuple(key1)
            key = (key1, cycle, name, submit_num)
            self.task_events_mgr.event_timers[key] = TaskActionTimer(
                ctx, delays, num, delay, timeout)
        else:
            LOG.exception(
                "%(id)s: skip action timer %(ctx_key)s" %
                {"id": id_, "ctx_key": ctx_key_raw})
            return
        LOG.info("+ %s.%s %s" % (name, cycle, ctx_key))

    def release_runahead_task(self, itask):
        """Release itask to the appropriate queue in the active pool.

        Also auto-spawn next instance if:
        - no parents to do it
        - has absolute triggers (these are satisfied already by definition)
        """
        try:
            queue = self.myq[itask.tdef.name]
        except KeyError:
            queue = self.config.Q_DEFAULT
        self.queues.setdefault(queue, OrderedDict())
        self.queues[queue][itask.identity] = itask
        self.pool.setdefault(itask.point, {})
        self.pool[itask.point][itask.identity] = itask
        self.pool_changed = True
        self.pool_changes.append(itask)
        LOG.debug("[%s] -released to the task pool", itask)
        del self.runahead_pool[itask.point][itask.identity]
        if not self.runahead_pool[itask.point]:
            del self.runahead_pool[itask.point]
        self.rhpool_changed = True
        if itask.tdef.max_future_prereq_offset is not None:
            self.set_max_future_offset()
        if itask.tdef.sequential:
            # implicit prev-instance parent
            return
        next_point = itask.next_point()
        if next_point is not None:
            parent_points = itask.tdef.get_parent_points(next_point)
            if (not parent_points or
                    all(x < self.config.start_point for x in parent_points)):
                # Auto-spawn next instance of tasks with no parents at the next
                # point (or with all parents before the suite start point).
                self.spawn_get(itask.tdef.name, next_point)
            else:
                # Auto-spawn next instance of tasks with absolute triggers.
                for trig in itask.tdef.get_abs_triggers(next_point):
                    p_name = trig.task_name
                    p_point = trig.get_point(next_point)
                    c_task = self.spawn_get(itask.tdef.name, next_point)
                    if c_task is not None:
                        # Update downstream prerequisites directly.
                        # TODO: USE PROXY METHOD
                        c_task.state.satisfy_me(
                            set([(p_name, str(p_point), trig.output)]))
                        # TODO: USE PROXY METHOD
                        c_task.parents_finished[(p_name, str(p_point))] = True

    def housekeep_waiting_tasks(self):
        """Remove waiting tasks if their parents are all finished.

        These can't be automatically satisfied anymore, even if they also have
        unsatisfied clock or other external triggers. But avoid removing:
        - held waiting tasks (these may run once released)
        - waiting tasks with no parents (i.e. just clock or external triggers)

        """
        remove_me = []
        removed = False
        for itask in self.get_tasks():
            if (itask.state(TASK_STATUS_WAITING)
                    and not itask.state.is_held
                    and itask.parents_finished
                    and all(itask.parents_finished.values())):
                LOG.info("Removed %s (stuck waiting)", itask.identity)
                remove_me.append(itask)
                removed = True
        for itask in remove_me:
            self.remove(itask)
        return removed

    def db_update_final_task_state(self, itask):
        """Update DB task_states table for final state of this task."""
        self.suite_db_mgr.put_update_task_states(
            itask,
            {
                "time_updated": itask.state.time_updated,
                "status": itask.state.status
            }
        )

    def remove(self, itask, reason=""):
        """Remove a task from the pool."""

        msg = "task proxy removed"
        if reason:
            msg += " (%s)" % reason

        try:
            del self.runahead_pool[itask.point][itask.identity]
        except KeyError:
            # Not in runahead pool.
            pass
        else:
            # In runahead pool.
            if not self.runahead_pool[itask.point]:
                del self.runahead_pool[itask.point]
            self.rhpool_changed = True
            self.db_update_final_task_state(itask)
            LOG.debug("[%s] -%s", itask, msg)
            del itask
            return

        try:
            del self.pool[itask.point][itask.identity]
        except KeyError:
            # Not in main pool (forced spawn uses temporary non-pool tasks)
            return

        # In main pool: remove from pool and queues.
        if itask.tdef.max_future_prereq_offset is not None:
            self.set_max_future_offset()
        if not self.pool[itask.point]:
            del self.pool[itask.point]
        self.pool_changed = True
        if itask.tdef.name in self.myq:  # A reload can remove a task
            del self.queues[self.myq[itask.tdef.name]][itask.identity]
        self.db_update_final_task_state(itask)
        LOG.debug("[%s] -%s", itask, msg)
        del itask

    def get_all_tasks(self):
        """Return a list of all task proxies."""
        return self.get_rh_tasks() + self.get_tasks()

    def get_tasks(self):
        """Return a list of task proxies in the main task pool."""
        if self.pool_changed:
            self.pool_changed = False
            self.pool_list = []
            for itask_id_maps in self.queues.values():
                self.pool_list.extend(list(itask_id_maps.values()))
        return self.pool_list

    def get_rh_tasks(self):
        """Return a list of task proxies in the runahead pool."""
        if self.rhpool_changed:
            self.rhpool_changed = False
            self.rhpool_list = []
            for itask_id_maps in self.runahead_pool.values():
                self.rhpool_list.extend(list(itask_id_maps.values()))
        return self.rhpool_list

    def get_pool_change_tasks(self):
        """Return a list of task proxies that changed pool."""
        results = self.pool_changes
        self.pool_changes = []
        return results

    def get_main_tasks_by_point(self):
        """Return a map of main pool tasks by cycle point."""
        point_itasks = {}
        for point, itask_id_map in self.pool.items():
            point_itasks[point] = list(itask_id_map.values())
        return point_itasks

    def get_rh_tasks_by_point(self):
        """Return a map of runahead pool tasks by cycle point."""
        point_itasks = {}
        for point, itask_id_map in self.runahead_pool.items():
            point_itasks[point] = list(itask_id_map.values())
        return point_itasks

    def get_tasks_by_point(self, incl_runahead):
        """Return a map of task proxies by cycle point."""
        point_itasks = {}
        for point, itask_id_map in self.pool.items():
            point_itasks[point] = list(itask_id_map.values())

        if not incl_runahead:
            return point_itasks

        for point, itask_id_map in self.runahead_pool.items():
            point_itasks.setdefault(point, [])
            point_itasks[point].extend(list(itask_id_map.values()))
        return point_itasks

    def get_task_by_id(self, id_):
        """Return task by ID is in the runahead_pool or pool.

        Return None if task does not exist.
        """
        for itask_ids in (
                list(self.queues.values())
                + list(self.runahead_pool.values())):
            try:
                return itask_ids[id_]
            except KeyError:
                pass

    def get_ready_tasks(self):
        """
        1) queue tasks that are ready to run (prerequisites satisfied,
        clock-trigger time up) or if their manual trigger flag is set.

        2) then submit queued tasks if their queue limit has not been
        reached or their manual trigger flag is set.

        If TASK_STATUS_QUEUED the task will submit as soon as its internal
        queue allows (or immediately if manually triggered first).

        Use of "cylc trigger" sets a task's manual trigger flag. Then,
        below, an unqueued task will be queued whether or not it is
        ready to run; and a queued task will be submitted whether or not
        its queue limit has been reached. The flag is immediately unset
        after use so that two manual trigger ops are required to submit
        an initially unqueued task that is queue-limited.

        Return the tasks that are dequeued.
        """

        now = time()
        ready_tasks = []
        qconfig = self.config.cfg['scheduling']['queues']

        for queue in self.queues:
            # 1) queue unqueued tasks that are ready to run or manually forced
            for itask in list(self.queues[queue].values()):
                if not itask.state(TASK_STATUS_QUEUED):
                    # only need to check that unqueued tasks are ready
                    if itask.is_ready(now):
                        # queue the task
                        itask.state.reset(TASK_STATUS_QUEUED)
                        itask.reset_manual_trigger()
                        # move the task to the back of the queue
                        self.queues[queue][itask.identity] = \
                            self.queues[queue].pop(itask.identity)

            # 2) submit queued tasks if manually forced or not queue-limited
            n_active = 0
            n_release = 0
            n_limit = qconfig[queue]['limit']
            tasks = list(self.queues[queue].values())

            # 2.1) count active tasks and compare to queue limit
            if n_limit:
                for itask in tasks:
                    if itask.state(
                            TASK_STATUS_READY,
                            TASK_STATUS_SUBMITTED,
                            TASK_STATUS_RUNNING,
                            is_held=False
                    ):
                        n_active += 1
                n_release = n_limit - n_active

            # 2.2) release queued tasks if not limited or if manually forced
            for itask in tasks:
                if not itask.state(TASK_STATUS_QUEUED):
                    # (This excludes tasks remaining TASK_STATUS_READY because
                    # job submission has been stopped with 'cylc shutdown').
                    continue
                if itask.manual_trigger or not n_limit or n_release > 0:
                    # manual release, or no limit, or not currently limited
                    n_release -= 1
                    ready_tasks.append(itask)
                    itask.reset_manual_trigger()
                    # (Set to 'ready' is done just before job submission).
                # else leaved queued

        LOG.debug('%d task(s) de-queued' % len(ready_tasks))

        return ready_tasks

    def task_has_future_trigger_overrun(self, itask):
        """Check for future triggers extending beyond the final cycle."""
        if not self.stop_point:
            return False
        for pct in itask.state.prerequisites_get_target_points():
            if pct > self.stop_point:
                return True
        return False

    def get_min_point(self):
        """Return the minimum cycle point currently in the pool."""
        cycles = list(self.pool)
        minc = None
        if cycles:
            minc = min(cycles)
        return minc

    def get_max_point(self):
        """Return the maximum cycle point currently in the pool."""
        cycles = list(self.pool)
        maxc = None
        if cycles:
            maxc = max(cycles)
        return maxc

    def get_max_point_runahead(self):
        """Return the maximum cycle point currently in the runahead pool."""
        cycles = list(self.runahead_pool)
        maxc = None
        if cycles:
            maxc = max(cycles)
        return maxc

    def set_max_future_offset(self):
        """Calculate the latest required future trigger offset."""
        max_offset = None
        for itask in self.get_tasks():
            if (itask.tdef.max_future_prereq_offset is not None and
                    (max_offset is None or
                     itask.tdef.max_future_prereq_offset > max_offset)):
                max_offset = itask.tdef.max_future_prereq_offset
        self.max_future_offset = max_offset

    def set_do_reload(self, config):
        """Set the task pool to reload mode."""
        self.config = config
        if config.options.stopcp:
            self.stop_point = get_point(config.options.stopcp)
        else:
            self.stop_point = config.final_point
        self.do_reload = True

        self.custom_runahead_limit = self.config.get_custom_runahead_limit()
        self.max_num_active_cycle_points = (
            self.config.get_max_num_active_cycle_points())

        # find any old tasks that have been removed from the suite
        old_task_name_list = self.task_name_list
        self.task_name_list = self.config.get_task_name_list()
        for name in old_task_name_list:
            if name not in self.task_name_list:
                self.orphans.append(name)
        for name in self.task_name_list:
            if name in self.orphans:
                self.orphans.remove(name)
        # adjust the new suite config to handle the orphans
        self.config.adopt_orphans(self.orphans)

        # reassign live tasks from the old queues to the new.
        # self.queues[queue][id_] = task
        self.assign_queues()
        new_queues = {}
        for queue in self.queues:
            for id_, itask in self.queues[queue].items():
                if itask.tdef.name not in self.myq:
                    continue
                key = self.myq[itask.tdef.name]
                new_queues.setdefault(key, OrderedDict())
                new_queues[key][id_] = itask
        self.queues = new_queues

    def reload_taskdefs(self):
        """Reload the definitions of task proxies in the pool.

        Orphaned tasks (proxies whose definitions were removed from the suite):
        - remove if not active yet
        - if active, leave them but prevent them from spawning children on
          subsequent outputs
        Otherwise: replace task definitions but copy over existing outputs etc.

        TODO: document for users: beware of reloading graph changes that affect
        current active tasks. Such tasks are active with their original defns -
        including what children they spawn - and it is not possible in general
        to be sure that new defns are compatible with already-active old tasks.
        So active tasks attempt to spawn the children that their (pre-reload)
        defns say they should.
        """
        LOG.info("Reloading task definitions.")
        tasks = self.get_all_tasks()
        # Log tasks orphaned by a reload but not currently in the task pool.
        for name in self.orphans:
            if name not in (itask.tdef.name for itask in tasks):
                LOG.warning("Removed task: '%s'", name)
        for itask in tasks:
            if itask.tdef.name in self.orphans:
                if (
                        itask.state(
                            TASK_STATUS_WAITING,
                            TASK_STATUS_QUEUED,
                            TASK_STATUS_SUBMIT_RETRYING,
                            TASK_STATUS_RETRYING,
                        )
                        or itask.state.is_held
                ):
                    # Remove orphaned task if it hasn't started running yet.
                    self.remove(itask, 'task definition removed')
                else:
                    # Keep active orphaned task, but stop it from spawning.
                    itask.children = {}
                    LOG.warning("[%s] -will not spawn children"
                                " (task definition removed)", itask)
            else:
                self.remove(itask, 'suite definition reload')
                new_task = self.add_to_runahead_pool(TaskProxy(
                    self.config.get_taskdef(itask.tdef.name),
                    self.config.initial_point, itask.point,
                    itask.state.status, stop_point=itask.stop_point,
                    submit_num=itask.submit_num))
                itask.copy_to_reload_successor(new_task)
                LOG.info('[%s] -reloaded task definition', itask)
                if itask.state(*TASK_STATUSES_ACTIVE):
                    LOG.warning(
                        "[%s] -job(%02d) active with pre-reload settings",
                        itask,
                        itask.submit_num)
        LOG.info("Reload completed.")
        self.do_reload = False

    def set_stop_point(self, stop_point):
        """Set the global suite stop point."""
        if self.stop_point == stop_point:
            return
        LOG.info("Setting stop cycle point: %s", stop_point)
        self.stop_point = stop_point
        for itask in self.get_tasks():
            # check cycle stop or hold conditions
            if (
                    self.stop_point
                    and itask.point > self.stop_point
                    and itask.state(
                        TASK_STATUS_WAITING,
                        TASK_STATUS_QUEUED,
                        is_held=False
                    )
            ):
                LOG.warning(
                    "[%s] -not running (beyond suite stop cycle) %s",
                    itask,
                    self.stop_point)
                itask.state.reset(is_held=True)
        return self.stop_point

    def can_stop(self, stop_mode):
        """Return True if suite can stop.

        A task is considered active if:
        * It is in the active state and not marked with a kill failure.
        * It has pending event handlers.
        """
        if stop_mode is None:
            return False
        if stop_mode == StopMode.REQUEST_NOW_NOW:
            return True
        if self.task_events_mgr.event_timers:
            return False
        for itask in self.get_tasks():
            if (
                    stop_mode == StopMode.REQUEST_CLEAN
                    and itask.state(*TASK_STATUSES_ACTIVE)
                    and not itask.state.kill_failed
            ):
                return False
        return True

    def warn_stop_orphans(self):
        """Log (warning) orphaned tasks on suite stop."""
        for itask in self.get_tasks():
            if (
                    itask.state(*TASK_STATUSES_ACTIVE)
                    and itask.state.kill_failed
            ):
                LOG.warning("%s: orphaned task (%s, kill failed)" % (
                    itask.identity, itask.state.status))
            elif itask.state(*TASK_STATUSES_ACTIVE):
                LOG.warning("%s: orphaned task (%s)" % (
                    itask.identity, itask.state.status))
        for key1, point, name, submit_num in self.task_events_mgr.event_timers:
            LOG.warning("%s/%s/%s: incomplete task event handler %s" % (
                point, name, submit_num, key1))

    def is_stalled(self):
        """Return True if the suite is stalled.

        A suite is stalled when:
        * It is not held.
        * It has no active tasks.
        * It has waiting tasks with unmet prerequisites
          (ignoring clock triggers).
        """
        if self.is_held:
            return False
        can_be_stalled = False
        for itask in self.get_tasks():
            if (
                    self.stop_point
                    and itask.point > self.stop_point
                    or itask.state(
                        TASK_STATUS_SUCCEEDED,
                        TASK_STATUS_EXPIRED,
                    )
            ):
                # Ignore: Task beyond stop point.
                # Ignore: Succeeded and expired tasks.
                continue
            if itask.state(*TASK_STATUSES_NOT_STALLED):
                # Pool contains active tasks (or held active tasks)
                # Return "not stalled" immediately.
                return False
            if (
                    itask.state(TASK_STATUS_WAITING)
                    and itask.state.prerequisites_are_all_satisfied()
            ):
                # Waiting tasks with all prerequisites satisfied,
                # probably waiting for clock trigger only.
                # This task can be considered active.
                # Return "not stalled" immediately.
                return False
            # We should be left with (submission) failed tasks and
            # waiting tasks with unsatisfied prerequisites.
            can_be_stalled = True
        return can_be_stalled

    def report_stalled_task_deps(self):
        """Log unmet dependencies on stalled."""
        prereqs_map = {}
        for itask in self.get_tasks():
            if (
                    itask.state(TASK_STATUS_WAITING)
                    and itask.state.prerequisites_are_not_all_satisfied()
            ):
                prereqs_map[itask.identity] = []
                for prereq_str, is_met in itask.state.prerequisites_dump():
                    if not is_met:
                        prereqs_map[itask.identity].append(prereq_str)

        # prune tree to ignore items that are elsewhere in it
        for id_, prereqs in list(prereqs_map.copy().items()):
            for prereq in prereqs:
                prereq_strs = prereq.split()
                if prereq_strs[0] == "LABEL:":
                    unsatisfied_id = prereq_strs[3]
                elif prereq_strs[0] == "CONDITION:":
                    continue
                else:
                    unsatisfied_id = prereq_strs[0]
                # Clear out tasks with dependencies on other waiting tasks
                if unsatisfied_id in prereqs_map:
                    del prereqs_map[id_]
                    break

        for id_, prereqs in prereqs_map.items():
            LOG.warning("Unmet prerequisites for %s:" % id_)
            for prereq in prereqs:
                LOG.warning(" * %s" % prereq)

    def set_hold_point(self, point):
        """Set the point after which tasks must be held."""
        self.hold_point = point
        if point is not None:
            for itask in self.get_all_tasks():
                if itask.point > point:
                    itask.state.reset(is_held=True)

    def hold_tasks(self, items):
        """Hold tasks with IDs matching any item in "ids"."""
        itasks, bad_items = self.filter_task_proxies(items)
        for itask in itasks:
            itask.state.reset(is_held=True)
        return len(bad_items)

    def release_tasks(self, items):
        """Release held tasks with IDs matching any item in "ids"."""
        itasks, bad_items = self.filter_task_proxies(items)
        for itask in itasks:
            itask.state.reset(is_held=False)
        return len(bad_items)

    def hold_all_tasks(self):
        """Hold all tasks."""
        LOG.info("Holding all waiting or queued tasks now")
        self.is_held = True
        for itask in self.get_all_tasks():
            itask.state.reset(is_held=True)

    def release_all_tasks(self):
        """Release all held tasks."""
        self.is_held = False
        self.release_tasks(None)

    def check_abort_on_task_fails(self):
        """Check whether suite should abort on task failure.

        Return True if:
        * There are failed tasks and `abort if any task fails` is specified.
        * There are unexpected failed tasks in a reference test.
        """
        expected_failed_tasks = self.config.get_expected_failed_tasks()
        if expected_failed_tasks is None:
            return False
        return any(
            (
                itask.state.status in TASK_STATUSES_FAILURE
                and itask.identity not in expected_failed_tasks
            )
            for itask in self.get_tasks())

    def spawn_and_update_children(self, itask, output):
        """Spawn children of itask:output if not already spawned.

        Update relevant prerequisites and parent-finished status of children.

        """
        if output in [TASK_OUTPUT_SUCCEEDED, TASK_OUTPUT_EXPIRED,
                      TASK_OUTPUT_FAILED]:
            # Spawn all children, to record parent finished status.
            childrens = itask.children.values()
            children = list(chain(*childrens))
            if (output == TASK_OUTPUT_FAILED and not itask.failure_handled):
                p_finished = False
            else:
                p_finished = True
        else:
            # Spawn children of the specific output.
            p_finished = False
            try:
                children = itask.children[output]
            except KeyError:
                # No children depend on this ouput
                children = []

        p_name = itask.tdef.name
        p_point = itask.point
        if p_finished:
            # Finished tasks can be removed immediately.
            self.remove(itask, 'finished')
            if itask.identity == self.stop_task_id:
                self.stop_task_finished = True

        for c_name, c_point in children:
            self.task_events_mgr.pflag = True  # TODO: still needed in SoD?
            c_task = self.spawn_get(c_name, c_point)
            # Update downstream prerequisites directly.
            # TODO: USE PROXY METHOD
            if c_task is not None:
                c_task.state.satisfy_me(set([(p_name, str(p_point), output)]))
                if p_finished:
                    # TODO: USE PROXY METHOD
                    c_task.parents_finished[(p_name, str(p_point))] = True
 
    def spawn_get(self, name, point):
        """Return proxy name.point after spawning if necessary, or None."""
        # TODO: is it possible for initial_point to not be defined??
        #  (see similar check and log message in scheduler.py as well)
        if self.config.initial_point and point < self.config.initial_point:
            # Only happens on manual trigger prior to FCP
            # Or future triggers like foo[+P1] => bar, with foo at ICP.
            LOG.warning(
                'Not spawning %s.%s: before initial cycle point', name, point)
            return
        elif self.config.final_point and point > self.config.final_point:
            # Only happens on manual trigger beyond FCP
            LOG.warning(
                'Not spawning %s.%s: beyond final cycle point', name, point)
            return

        # Find existing proxy if already spawned.
        for itask in self.get_all_tasks():
            if itask.tdef.name == name and itask.point == point:
                return itask

        # Else spawn a new one if possible.
        for tname in self.config.get_task_name_list():
            if tname == name:
                key = (name, str(point))
                submit_nums = (
                    self.suite_db_mgr.pri_dao.select_submit_nums([key]))
                submit_num = submit_nums.get(key, 0)
                if submit_num != 0:
                    # Conditional reflow prevention!
                    LOG.warning(
                        'Not spawning %s.%s (already spawned)', name, point)
                    return None
                itask = TaskProxy(
                    self.config.get_taskdef(tname),
                    self.config.initial_point, point,
                    submit_num=submit_num)
                LOG.info('Spawned %s.%s', name, point)
                self.add_to_runahead_pool(itask)
                return itask

        LOG.warning('Failed to spawn %s.%s', name, point)
        return None

    def remove_suiciding_tasks(self):
        """Remove tasks that suicide-triggered, return the number removed."""
        num_removed = 0
        for itask in self.get_all_tasks():
            if (itask.state.suicide_prerequisites and
                    itask.state.suicide_prerequisites_are_all_satisfied()):
                if itask.state(
                        TASK_STATUS_READY,
                        TASK_STATUS_SUBMITTED,
                        TASK_STATUS_RUNNING,
                        is_held=False
                ):
                    LOG.warning('[%s] -suiciding while active', itask)
                else:
                    LOG.info('[%s] -suiciding', itask)
                self.remove(itask, 'suicide')
                num_removed += 1
        return num_removed

    def spawn_downstream_tasks(self, items, outputs):
        """Spawn downstream children of given task outputs on user command."""
        # TODO CHANGE NAME force_spawn_children?
        if not outputs:
            outputs = [TASK_OUTPUT_SUCCEEDED]
        n_warnings = 0
        task_items = {}
        for item in items:
            point_str, name_str = self._parse_task_item(item)[:2]
            if point_str is None:
                LOG.warning(
                    "%s: task ID for spawning must contain cycle point" % (item))
                n_warnings += 1
                continue
            try:
                point_str = standardise_point_string(point_str)
            except PointParsingError as exc:
                LOG.warning(
                    self.ERR_PREFIX_TASKID_MATCH + ("%s (%s)" % (item, exc)))
                n_warnings += 1
                continue
            taskdefs = self.config.find_taskdefs(name_str)
            if not taskdefs:
                LOG.warning(self.ERR_PREFIX_TASKID_MATCH + item)
                n_warnings += 1
                continue
            for taskdef in taskdefs:
                task_items[(taskdef.name, point_str)] = taskdef

        for key, taskdef in sorted(task_items.items()):
            # Check that the cycle point is on one of the tasks sequences.
            point = get_point(key[1])
            # Check if cycle point is on the tasks sequence.
            for sequence in taskdef.sequences:
                if sequence.is_on_sequence(point):
                    break
            else:
                LOG.warning("%s%s, %s" % (
                    self.ERR_PREFIX_TASK_NOT_ON_SEQUENCE, taskdef.name,
                    key[1]))
                continue

            # This the upstream target task:
            itask = TaskProxy(taskdef, self.config.initial_point, point)
            # Spawn downstream on selected outputs.
            for trig, out, status in itask.state.outputs.get_all():
                if trig in outputs:
                    LOG.info('Forced spawning on %s:%s', itask.identity, out)
                    self.spawn_and_update_children(itask, out)

    def remove_tasks(self, items):
        """Remove tasks from the pool."""
        itasks, bad_items = self.filter_task_proxies(items)
        for itask in itasks:
            self.remove(itask, 'request')
        return len(bad_items)

    def trigger_tasks(self, items, back_out=False, task_pool=False):
        """Forced task triggering.

        task_pool=False:
           Insert and trigger any abstract task in the workflow.
        task_pool=True:
           Match (potentially by state) and trigger existing tasks in the task
           pool, e.g. to allow retriggering a bunch of unhandled failed tasks.

        These are handled separately because we can't reliably tell which is
        intended from command line arguments alone. Example using one of
        the allowed arg forms:
          - trigger any task: CYCLE-POINT/TASK-NAME-GLOB
          - match and trigger: [CYCLE-POINT-GLOB/]TASK-NAME-GLOB[:TASK-STATE]
          If the cycle point is definite (not a glob pattern) and the optional
          :STATE is not used, we can't tell which is intended.
        TODO: can we unify these somehow?
        """
        if task_pool:
            itasks, bad_items = self.filter_task_proxies(items)
            n_warnings = len(bad_items)
            for itask in itasks:
                if back_out:
                    # (Aborted edit-run, reset for next trigger attempt).
                    try:
                        del itask.summary['job_hosts'][itask.submit_num]
                    except KeyError:
                        pass
                    job_d = get_task_job_id(
                        itask.point, itask.tdef.name, itask.submit_num)
                    self.job_pool.remove_job(job_d)
                    itask.submit_num -= 1
                    itask.summary['submit_num'] = itask.submit_num
                    itask.local_job_file_path = None
                    continue
                if itask.state(*TASK_STATUSES_ACTIVE):
                    LOG.warning('%s: already triggered' % itask.identity)
                    n_warnings += 1
                    continue
                itask.manual_trigger = True
                if not itask.state(
                        TASK_STATUS_QUEUED,
                        is_held=False
                ):
                    itask.state.reset(TASK_STATUS_READY, is_held=False)
        else:
            # TODO COMBINE THE FOLLOWING LOGIC WITH THAT OF spawn_tasks?
            # TODO: edit run back-out for the insert-and-trigger case.
            if back_out:
                LOG.warning("EDIT-RUN BACK-OUT NOT DONE FOR THIS METHOD.")
                return
            n_warnings = 0
            task_items = {}
            select_args = []
            for item in items:
                point_str, name_str = self._parse_task_item(item)[:2]
                if point_str is None:
                    LOG.warning(
                        "%s: trigger task must have a cycle point" % item)
                    n_warnings += 1
                    continue
                try:
                    point_str = standardise_point_string(point_str)
                except PointParsingError as exc:
                    LOG.warning(
                        self.ERR_PREFIX_TASKID_MATCH + (
                            "%s (%s)" % (item, exc)))
                    n_warnings += 1
                    continue
                taskdefs = self.config.find_taskdefs(name_str)
                if not taskdefs:
                    LOG.warning(self.ERR_PREFIX_TASKID_MATCH + item)
                    n_warnings += 1
                    continue
                for taskdef in taskdefs:
                    task_items[(taskdef.name, point_str)] = taskdef
                    select_args.append((taskdef.name, point_str))

            for name, point_str in select_args:
                # Spawn and set ready to trigger.
                itask = self.spawn_get(name, get_point(point_str).standardise())
                if itask is not None:
                    itask.state.reset(TASK_STATUS_WAITING)
                    itask.state.set_prerequisites_all_satisfied()

        return n_warnings

    def sim_time_check(self, message_queue):
        """Simulation mode: simulate task run times and set states."""
        sim_task_state_changed = False
        now = time()
        for itask in self.get_tasks():
            if itask.state.status != TASK_STATUS_RUNNING:
                continue
            # Started time is not set on restart
            if itask.summary['started_time'] is None:
                itask.summary['started_time'] = now
            timeout = (itask.summary['started_time'] +
                       itask.tdef.rtconfig['job']['simulated run length'])
            if now > timeout:
                conf = itask.tdef.rtconfig['simulation']
                job_d = get_task_job_id(
                    itask.point, itask.tdef.name, itask.submit_num)
                now_str = get_current_time_string()
                if (itask.point in conf['fail cycle points'] and
                        (itask.get_try_num() == 1 or
                         not conf['fail try 1 only'])):
                    message_queue.put(
                        (job_d, now_str, 'CRITICAL', TASK_STATUS_FAILED))
                else:
                    # Simulate message outputs.
                    for msg in itask.tdef.rtconfig['outputs'].values():
                        message_queue.put((job_d, now_str, 'INFO', msg))
                    message_queue.put(
                        (job_d, now_str, 'INFO', TASK_STATUS_SUCCEEDED))
                sim_task_state_changed = True
        return sim_task_state_changed

    def set_expired_task(self, itask, now):
        """Check if task has expired. Set state and event handler if so.

        Return True if task has expired.
        """
        if (
                not itask.state(
                    TASK_STATUS_WAITING,
                    is_held=False
                )
                or itask.tdef.expiration_offset is None
        ):
            return False
        if itask.expire_time is None:
            itask.expire_time = (
                itask.get_point_as_seconds() +
                itask.get_offset_as_seconds(itask.tdef.expiration_offset))
        if now > itask.expire_time:
            msg = 'Task expired (skipping job).'
            LOG.warning('[%s] -%s', itask, msg)
            self.task_events_mgr.setup_event_handlers(itask, "expired", msg)
            # TODO succeeded and expired states are useless due to immediate
            # removal under all circumstances (unhandled failed is still used).
            itask.state.reset(TASK_STATUS_EXPIRED, is_held=False)
            self.remove(itask, 'expired')
            return True
        return False

    def task_succeeded(self, id_):
        """Return True if task with id_ is in the succeeded state."""
        for itask in self.get_tasks():
            if (
                    itask.identity == id_
                    and itask.state(TASK_STATUS_SUCCEEDED)
            ):
                return True
        return False

    def ping_task(self, id_, exists_only=False):
        """Return message to indicate if task exists and/or is running."""
        found = False
        running = False
        for itask in self.get_tasks():
            if itask.identity == id_:
                found = True
                if itask.state(TASK_STATUS_RUNNING):
                    running = True
                break
        if found and exists_only:
            return True, "task found"
        elif running:
            return True, "task running"
        elif found:
            return False, "task not running"
        else:
            return False, "task not found"

    def filter_task_proxies(self, items):
        """Return task proxies that match names, points, states in items.

        Return (itasks, bad_items).
        In the new form, the arguments should look like:
        items -- a list of strings for matching task proxies, each with
                 the general form name[.point][:state] or [point/]name[:state]
                 where name is a glob-like pattern for matching a task name or
                 a family name.

        """
        itasks = []
        bad_items = []
        if not items:
            itasks += self.get_all_tasks()
        else:
            for item in items:
                point_str, name_str, status = self._parse_task_item(item)
                if point_str is None:
                    point_str = "*"
                else:
                    try:
                        point_str = standardise_point_string(point_str)
                    except PointParsingError:
                        # point_str may be a glob
                        pass
                tasks_found = False
                for itask in self.get_all_tasks():
                    nss = itask.tdef.namespace_hierarchy
                    if (fnmatchcase(str(itask.point), point_str) and
                            (not status or itask.state.status == status) and
                            (fnmatchcase(itask.tdef.name, name_str) or
                             any(fnmatchcase(ns, name_str) for ns in nss))):
                        itasks.append(itask)
                        tasks_found = True
                if not tasks_found:
                    LOG.warning(self.ERR_PREFIX_TASKID_MATCH + item)
                    bad_items.append(item)
        return itasks, bad_items

    @staticmethod
    def _parse_task_item(item):
        """Parse point/name:state or name.point:state syntax."""
        if ":" in item:
            head, state_str = item.rsplit(":", 1)
        else:
            head, state_str = (item, None)
        if "/" in head:
            point_str, name_str = head.split("/", 1)
        elif "." in head:
            name_str, point_str = head.split(".", 1)
        else:
            name_str, point_str = (head, None)
        return (point_str, name_str, state_str)