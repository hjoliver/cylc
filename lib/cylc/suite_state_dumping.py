#!/usr/bin/env python

# THIS FILE IS PART OF THE CYLC SUITE ENGINE.
# Copyright (C) 2008-2016 NIWA
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
"""State dump generator."""

import errno
import os
import time
import logging
import cylc.flags
from cylc.cfgspec.globalcfg import GLOBAL_CFG
from cylc.wallclock import get_current_time_string
from cylc.network.suite_broadcast import BroadcastServer


class SuiteStateDumper(object):
    """Generate state dumps."""

    BASE_NAME = 'state'

    def __init__(self, suite, ict=None, stop_point=None):
        self.cts_str = None
        self.set_cts(ict, stop_point)
        self.dir_name = GLOBAL_CFG.get_derived_host_item(
            suite, 'suite state directory')
        self.file_name = os.path.join(self.dir_name, self.BASE_NAME)
        self.arch_len = GLOBAL_CFG.get(['state dump rolling archive length'])
        if not self.arch_len or int(self.arch_len) <= 1:
            self.arch_len = 1
        self.arch_files = []
        self.pool = None
        self.log = logging.getLogger('main')

    def set_cts(self, ict, fct):
        """Set initial and final cycle time strings."""
        self.cts_str = "initial cycle : %s\n" % str(ict)
        self.cts_str += "final cycle : %s\n" % str(fct)

    def dump(self, tasks=None):
        """Dump suite states to disk. Return state file basename on success."""

        wireless = BroadcastServer.get_inst()

        base_name = self.BASE_NAME + "." + get_current_time_string(
            override_use_utc=True, use_basic_format=True,
            display_sub_seconds=True
        )
        file_name = os.path.join(self.dir_name, base_name)

        # write the state dump file, retrying several times in case of:
        # (a) log rolling error when cylc-run contents have been deleted,
        # (b) "[Errno 9] bad file descriptor" at BoM - see github #926.
        max_attempts = 5
        n_attempt = 1
        while True:
            handle = None
            try:
                handle = open(file_name, "wb")

                handle.write('run mode : %s\n' % cylc.flags.run_mode)
                handle.write('time : %s (%d)\n' % (
                    get_current_time_string(), time.time()))

                handle.write(self.cts_str)
                wireless.dump(handle)
                handle.write('Begin task states\n')

                if tasks is None and self.pool is not None:
                    tasks = self.pool.get_all_tasks()
                if tasks is not None:
                    for itask in sorted(tasks, key=lambda t: t.identity):
                        handle.write("%s : status=%s, spawned=%s\n" % (
                            itask.identity, itask.state.status,
                            itask.has_spawned))

                # To generate "OSError [Errno 9] bad file descriptor",
                # close the file with os.close() before calling fsync():
                # # os.close( handle.fileno() )

                os.fsync(handle.fileno())
                handle.close()
            except (IOError, OSError) as exc:
                # No such file or directory? It is likely that the directory
                # has been removed and is not recoverable.
                if exc.errno == errno.ENOENT:
                    raise
                if not exc.filename:
                    exc.filename = file_name
                self.log.warning(
                    'State dumping failed, #%d %s' % (n_attempt, exc))
                if n_attempt >= max_attempts:
                    raise exc
                n_attempt += 1
                if handle is not None:
                    try:
                        handle.close()
                    except (IOError, OSError) as exc:
                        self.log.warning(
                            'State file handle closing failed: %s' % exc)
                time.sleep(0.2)
            else:
                break

        # Point "state" symbolic link to new dated state dump
        try:
            os.unlink(self.file_name)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
        os.symlink(base_name, self.file_name)
        self.arch_files.append(file_name)
        # Remove state dump older than archive length
        while len(self.arch_files) > self.arch_len:
            try:
                os.unlink(self.arch_files.pop(0))
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
        return base_name
