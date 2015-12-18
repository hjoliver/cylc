#!/usr/bin/env python

# THIS FILE IS PART OF THE CYLC SUITE ENGINE.
# Copyright (C) 2008-2015 NIWA
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

import re
import sys

"""
Task states, plus dump and reload from the suite state dump file.

Note that 'runahead' is a task state used only in the state summary for suite
monitoring. It indicates a task in the runahead pool - which is technically
'waiting' but inactive.
"""


class TaskStateError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return repr(self.msg)


class task_state(object):

    legal = [
        'waiting',
        'held',
        'queued',
        'ready',
        'expired',
        'submitted',
        'submit-failed',
        'submit-retrying',
        'running',
        'succeeded',
        'failed',
        'retrying',
        'runahead'
    ]

    legal_for_reset = [
        'waiting',
        'held',
        'ready',
        'succeeded',
        'failed',
    ]

    legal_for_trigger = {
        'expire': 'expired',
        'submit': 'submitted',
        'submit-fail': 'submit-failed',
        'start': 'started',
        'succeed': 'succeeded',
        'fail': 'failed'
    }

    legal_for_restricted_monitoring = [
        'expired',
        'submitted',
        'submit-failed',
        'submit-retrying',
        'running',
        'failed',
        'retrying'
    ]

    @classmethod
    def is_legal(cls, state):
        return state in cls.legal

    @classmethod
    def get_legal_trigger_state(cls, str):
        if str in cls.legal_for_trigger.values():
            return str
        elif str in cls.legal_for_trigger.keys():
            return cls.legal_for_trigger[str]
        else:
            raise TaskStateError("Illegal trigger state: %s" % str)

    # GUI button labels
    labels = {
        'waiting': '_waiting',
        'queued': '_queued',
        'ready': 'rea_dy',
        'expired': 'e_xpired',
        'submitted': 'sub_mitted',
        'submit-failed': 'submit-f_ailed',
        'submit-retrying': 'submit-retryin_g',
        'running': '_running',
        'succeeded': '_succeeded',
        'failed': '_failed',
        'retrying': 'retr_ying',
        'held': '_held',
        'runahead': 'r_unahead',
    }
    # terminal monitor color control codes
    ctrl = {
        'waiting': "\033[1;36m",
        'queued': "\033[1;38;44m",
        'ready': "\033[1;32m",
        'expired': "\033[1;37;40m",
        'submitted': "\033[1;33m",
        'submit-failed': "\033[1;34m",
        'submit-retrying': "\033[1;31m",
        'running': "\033[1;37;42m",
        'succeeded': "\033[0m",
        'failed': "\033[1;37;41m",
        'retrying': "\033[1;35m",
        'held': "\033[1;37;43m",
        'runahead': "\033[1;37;44m",
    }

    ctrl_end = "\033[0m"

    def __init__(self, initial_state):

        self.state = {}

        if not initial_state:
            # defaults
            self.state['status'] = 'waiting'
        else:
            # could be a state dump file entry
            # or a raw string ('waiting' etc.)
            self.state = self.parse(initial_state)
            self.check()

    def set_status(self, state):
        if self.__class__.is_legal(state):
            self.state['status'] = state

    def get_status(self):
        return self.state['status']

    def is_currently(self, *states):
        """Return true if current state matches any state in states."""
        return self.state['status'] in states

    # generic set for special dumpable state required by some tasks.
    def set(self, item, value):
        self.state[item] = value

    # generic get for special dumpable state required by some tasks.
    def get(self, item):
        return self.state[item]

    def check(self):
        # check compulsory items have been defined correctly
        if 'status' not in self.state:
            raise TaskStateError('ERROR, run status not defined')
        if not self.__class__.is_legal(self.state['status']):
            raise TaskStateError(
                'ERROR, illegal run status: ' + str(self.state['status']))

    def dump(self):
        # format: 'item1=value1, item2=value2, ...'
        result = ''
        for key in self.state:
            result += key + '=' + str(self.state[key]) + ', '
        result = result.rstrip(', ')
        return result

    def parse(self, input):
        state = {}

        if self.__class__.is_legal(input):
            state['status'] = input

        else:
            # reconstruct state from a dumped state string
            pairs = input.split(', ')
            for pair in pairs:
                [item, value] = pair.split('=')
                if item not in ['status']:
                    raise TaskStateError(
                        'ERROR, illegal task status key: ' + item)
                if item == 'status':
                    if not self.__class__.is_legal(value):
                        raise TaskStateError(
                            'ERROR, illegal task state: ' + value)
                state[item] = value

        return state
