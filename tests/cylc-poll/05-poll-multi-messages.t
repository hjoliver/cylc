#!/bin/bash
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
#-------------------------------------------------------------------------------
# Test poll multiple messages
. "$(dirname "$0")/test_header"
set_test_number 7

create_test_globalrc
install_suite "${TEST_NAME_BASE}" "${TEST_NAME_BASE}"

run_ok "${TEST_NAME_BASE}-validate" cylc validate "${SUITE_NAME}"

suite_run_ok "${TEST_NAME_BASE}-run" \
    cylc run --reference-test --debug "${SUITE_NAME}"
grep_ok "speaker1\.1.*hello1 1 (polled)" $SUITE_LOG_DIR/log
grep_ok "speaker1\.1.*hello2 1 (polled)" $SUITE_LOG_DIR/log
grep_ok "speaker1\.1.*started (polled)" $SUITE_LOG_DIR/log
grep_ok "speaker2\.1.*greet 1 (polled)" $SUITE_LOG_DIR/log
grep_ok "speaker2\.1.*started (polled)" $SUITE_LOG_DIR/log

purge_suite "${SUITE_NAME}"
exit
