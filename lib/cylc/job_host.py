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
"""Manage a remote job host."""

import os
from pipes import quote
from subprocess import Popen, PIPE
from logging import getLogger
import shlex

from cylc.cfgspec.globalcfg import GLOBAL_CFG
from cylc.owner import USER
from cylc.suite_env import CylcSuiteEnv


class RemoteJobHostInitError(Exception):
    """Cannot initialise suite run directory of remote job host."""

    def __str__(self):
        user_at_host, cmd_str, ret_code, out, err = self.args
        ret = (
            # user_at_host
            "%s: initialisation did not complete:\n" +
            # command  # return code
            "COMMAND FAILED (%d): %s\n"
        ) % (user_at_host, ret_code, cmd_str)
        for label, item in ("STDOUT", out), ("STDERR", err):
            if item:
                for line in item.splitlines(True):  # keep newline chars
                    ret += "COMMAND %s: %s" % (label, line)
        return ret


class RemoteJobHostManager(object):
    """Manage a remote job host."""

    _INSTANCE = None

    @classmethod
    def get_inst(cls):
        """Return a singleton instance of this class."""
        if cls._INSTANCE is None:
            cls._INSTANCE = cls()
        return cls._INSTANCE

    def __init__(self):
        self.initialised_hosts = []
        self.single_task_mode = False

    def init_suite_run_dir(self, suite_name, user_at_host):
        """Initialise suite run dir on a user@host.

        Create SUITE_RUN_DIR/log/job/ if necessary.
        Install suite contact environment file.
        Install suite python modules.

        Raise RemoteJobHostInitError if initialisation cannot complete.

        """
        if '@' in user_at_host:
            owner, host = user_at_host.split('@', 1)
        else:
            owner, host = None, user_at_host
        if ((owner, host) in [(None, 'localhost'), (USER, 'localhost')] or
                host in self.initialised_hosts or
                self.single_task_mode):
            return

        suite_run_dir = GLOBAL_CFG.get_derived_host_item(
            suite_name, 'suite run directory')
        sources = [os.path.join(suite_run_dir, CylcSuiteEnv.BASE_NAME)]
        if 'CYLC_SUITE_DEF_PATH' in os.environ:
            sources.append(
                os.path.join(os.getenv('CYLC_SUITE_DEF_PATH'), 'passphrase'))
        suite_run_py = os.path.join(suite_run_dir, 'python')
        if os.path.isdir(suite_run_py):
            sources.append(suite_run_py)
        r_suite_run_dir = GLOBAL_CFG.get_derived_host_item(
            suite_name, 'suite run directory', host, owner)
        r_log_job_dir = GLOBAL_CFG.get_derived_host_item(
            suite_name, 'suite job log directory', host, owner)
        getLogger('log').info('Initialising %s:%s' % (
            user_at_host, r_suite_run_dir))

        ssh_tmpl = GLOBAL_CFG.get_host_item(
            'remote shell template', host, owner).replace(' %s', '')
        scp_tmpl = GLOBAL_CFG.get_host_item(
            'remote copy template', host, owner)

        cmd1 = shlex.split(ssh_tmpl) + [
            "-n", user_at_host,
            'mkdir', '-p', r_suite_run_dir, r_log_job_dir]
        cmd2 = shlex.split(scp_tmpl) + ['-pr'] + sources + [
            user_at_host + ":" + r_suite_run_dir + '/']
        for cmd in [cmd1, cmd2]:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE)
            out, err = proc.communicate()
            if proc.wait():
                raise RemoteJobHostInitError(
                    user_at_host, " ".join([quote(item) for item in cmd]),
                    proc.returncode, out, err)
        self.initialised_hosts.append(user_at_host)
