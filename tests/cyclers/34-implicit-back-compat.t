#!/bin/bash
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
#-------------------------------------------------------------------------------
# Check that implicit cycling is ok in cylc-5 back compat mode.
. $(dirname $0)/test_header
#-------------------------------------------------------------------------------
set_test_number 3
#-------------------------------------------------------------------------------
install_suite $TEST_NAME_BASE $TEST_NAME_BASE
#-------------------------------------------------------------------------------
TEST_NAME=$TEST_NAME_BASE-validate
run_ok $TEST_NAME cylc validate $SUITE_NAME
#-------------------------------------------------------------------------------
TEST_NAME=$TEST_NAME_BASE-cmp
cmp_ok $TEST_NAME_BASE-validate.stderr <<__ERR__
WARNING: pre cylc 6 syntax is deprecated: integer interval: [cylc][reference test]live mode suite timeout = 30
WARNING: pre cylc 6 syntax is deprecated: initial/final cycle point format: CCYYMMDDhh
WARNING: pre cylc 6 syntax is deprecated: [scheduling][[dependencies]][[[00]]]: old-style cycling
WARNING: pre cylc 6 syntax is deprecated: graphnode foo[T-24]: old-style offset
WARNING, foo: not explicitly defined in dependency graphs (deprecated)
__ERR__
#-------------------------------------------------------------------------------
TEST_NAME=$TEST_NAME_BASE-run
suite_run_ok $TEST_NAME cylc run --reference-test --debug $SUITE_NAME
#-------------------------------------------------------------------------------
purge_suite $SUITE_NAME
