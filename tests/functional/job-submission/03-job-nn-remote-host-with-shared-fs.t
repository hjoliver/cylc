#!/usr/bin/env bash
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
# Test job log NN link correctness on reaching 100, remote (with shared fs).
export CYLC_TEST_IS_GENERIC=false
. "$(dirname "$0")/test_header"
require_remote_platform_wsfs
# export CYLC_TEST_PLATFORM which is used by the flow
export CYLC_TEST_PLATFORM="$CYLC_TEST_PLATFORM_WSFS"
set_test_number 2
install_suite "${TEST_NAME_BASE}" "${TEST_NAME_BASE}"

run_ok "${TEST_NAME_BASE}-validate" cylc validate "${SUITE_NAME}"
sqlite3 "${SUITE_RUN_DIR}/.service/db" <'db.sqlite3'
suite_run_ok "${TEST_NAME_BASE}-restart" \
    cylc restart --reference-test --debug --no-detach "${SUITE_NAME}"

purge_suite_platform "${CYLC_TEST_PLATFORM_WSFS}" "${SUITE_NAME}"
purge_suite "${SUITE_NAME}"
exit