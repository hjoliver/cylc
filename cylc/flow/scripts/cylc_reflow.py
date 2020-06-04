#!/usr/bin/env python3

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

"""cylc [control] reflow [OPTIONS] ARGS

Start, query, and stop reflows.

"""

import sys
if '--use-ssh' in sys.argv[1:]:
    sys.argv.remove('--use-ssh')
    from cylc.flow.remote import remrun
    if remrun():
        sys.exit(0)

from cylc.flow.exceptions import ClientError, ClientTimeout
from cylc.flow.network.client import SuiteRuntimeClient
from cylc.flow.option_parsers import CylcOptionParser as COP
from cylc.flow.task_id import TaskID
from cylc.flow.terminal import prompt, cli_function


def get_option_parser():
    parser = COP(
        __doc__, comms=True,
        argdoc=[("REG", "Suite name"),
                ("[FLOW]", """Flow label""")])

    parser.add_option(
        "-s", "--stop",
        help="Stop the flow.",
        action="store_true", default=False, dest="stop")

    return parser


@cli_function(get_option_parser)
def main(parser, options, suite, shutdown_arg=None):
    pclient = SuiteRuntimeClient(
        suite, options.owner, options.host, options.port,
        options.comms_timeout)

    options, args = parser.parse_args()
    flow_label = args[1]
    if options.stop:
        prompt('Stop flow %s' % suite, flow_label)
        pclient('reflow', {'flow_label': flow_label, 'stop': True})

if __name__ == "__main__":
    main()
