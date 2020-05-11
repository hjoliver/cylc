#!/bin/bash
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
#-------------------------------------------------------------------------------
# Test "cylc search" basic usage.
. $(dirname $0)/test_header
#-------------------------------------------------------------------------------
set_test_number 2
install_suite "${TEST_NAME_BASE}" "${TEST_NAME_BASE}"
#-------------------------------------------------------------------------------
TEST_NAME="${TEST_NAME_BASE}"
run_ok "${TEST_NAME}" cylc search "${SUITE_NAME}" 'initial cycle point'
cmp_ok "${TEST_NAME}.stdout" <<__OUT__

FILE: ${PWD}/include/suite-scheduling.rc
   SECTION: [scheduling]
      (2): initial cycle point=20130101
__OUT__
#-------------------------------------------------------------------------------
purge_suite "${SUITE_NAME}"
exit
