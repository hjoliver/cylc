#!/bin/bash
# THIS FILE IS PART OF THE CYLC SUITE ENGINE.
# Copyright (C) 2008-2018 NIWA
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
# Basic tests for "cycl nameless".
#-------------------------------------------------------------------------------
. "$(dirname "$0")/test_header"
if ! python -c 'import cherrypy' 2>'/dev/null'; then
    skip_all '"cherrypy" not installed'
fi

set_test_number 61

ROSE_CONF_PATH= cylc_ws_init 'cylc' 'nameless'
if [[ -z "${TEST_CYLC_WS_PORT}" ]]; then
    exit 1
fi

#-------------------------------------------------------------------------------
# Run a quick cylc suite
mkdir -p "${HOME}/cylc-run"
TEST_DIR="$(mktemp -d --tmpdir="${HOME}/cylc-run" "ctb-cylc-nameless-00-XXXXXXXX")"
SUITE_NAME="$(basename "${TEST_DIR}")"
cp -pr "${TEST_SOURCE_DIR}/${TEST_NAME_BASE}/"* "${TEST_DIR}"
export CYLC_CONF_PATH=
cylc register "${SUITE_NAME}" "${TEST_DIR}"
cylc run --no-detach --debug "${SUITE_NAME}" 2>'/dev/null' \
    || cat "${TEST_DIR}/log/suite/err" >&2

#-------------------------------------------------------------------------------
TEST_NAME="${TEST_NAME_BASE}-curl-root"
echo ${TEST_CYLC_WS_URL} >&2
run_ok "${TEST_NAME}" curl -I "${TEST_CYLC_WS_URL}"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 200 OK' "${TEST_NAME}.stdout"

TEST_NAME="${TEST_NAME_BASE}-200-curl-root-json"
run_ok "${TEST_NAME}" curl "${TEST_CYLC_WS_URL}/?form=json"
cylc_ws_json_greps "${TEST_NAME}.stdout" "${TEST_NAME}.stdout" \
    "[('cylc_version',), '$(cylc version | cut -d' ' -f 2)']" \
    "[('title',), 'Cylc Nameless']" \
    "[('host',), '$(hostname)']"

TEST_NAME="${TEST_NAME_BASE}-200-curl-suites"
run_ok "${TEST_NAME}" curl -I "${TEST_CYLC_WS_URL}/suites/${USER}"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 200 OK' "${TEST_NAME}.stdout"

TEST_NAME="${TEST_NAME_BASE}-200-curl-suites-json"
run_ok "${TEST_NAME}" curl "${TEST_CYLC_WS_URL}/suites/${USER}?form=json"
cylc_ws_json_greps "${TEST_NAME}.stdout" "${TEST_NAME}.stdout" \
    "[('cylc_version',), '$(cylc version | cut -d' ' -f 2)']" \
    "[('title',), 'Cylc Nameless']" \
    "[('host',), '$(hostname)']" \
    "[('user',), '${USER}']" \
    "[('entries', {'name': '${SUITE_NAME}'}, 'name',), '${SUITE_NAME}']" \
    "[('entries', {'name': '${SUITE_NAME}'}, 'info', 'project'), 'survey']" \
    "[('entries', {'name': '${SUITE_NAME}'}, 'info', 'title'), 'hms beagle']"

TEST_NAME="${TEST_NAME_BASE}-404-curl-suites"
run_ok "${TEST_NAME}" curl -I "${TEST_CYLC_WS_URL}/suites/no-such-user"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 404 Not Found' "${TEST_NAME}.stdout"

for METHOD in 'cycles' 'jobs'; do
    TEST_NAME="${TEST_NAME_BASE}-200-curl-${METHOD}"
    run_ok "${TEST_NAME}" \
        curl -I "${TEST_CYLC_WS_URL}/${METHOD}/${USER}/${SUITE_NAME}"
    grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 200 OK' "${TEST_NAME}.stdout"

    TEST_NAME="${TEST_NAME_BASE}-404-1-curl-${METHOD}"
    run_ok "${TEST_NAME}" \
        curl -I "${TEST_CYLC_WS_URL}/${METHOD}/no-such-user/${SUITE_NAME}"
    grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 404 Not Found' "${TEST_NAME}.stdout"

    TEST_NAME="${TEST_NAME_BASE}-404-2-curl-${METHOD}"
    run_ok "${TEST_NAME}" \
        curl -I "${TEST_CYLC_WS_URL}/${METHOD}/${USER}/no-such-suite"
    grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 404 Not Found' "${TEST_NAME}.stdout"
done

TEST_NAME="${TEST_NAME_BASE}-200-curl-cycles"
run_ok "${TEST_NAME}" \
    curl "${TEST_CYLC_WS_URL}/cycles/${USER}/${SUITE_NAME}?form=json"
cylc_ws_json_greps "${TEST_NAME}.stdout" "${TEST_NAME}.stdout" \
    "[('cylc_version',), '$(cylc version | cut -d' ' -f 2)']" \
    "[('title',), 'Cylc Nameless']" \
    "[('host',), '$(hostname)']" \
    "[('user',), '${USER}']" \
    "[('suite',), '${SUITE_NAME}']" \
    "[('info', 'project',), 'survey']" \
    "[('info', 'title',), 'hms beagle']" \
    "[('page',), 1]" \
    "[('n_pages',), 1]" \
    "[('per_page',), 100]" \
    "[('order',), None]" \
    "[('states', 'is_running',), False]" \
    "[('states', 'is_failed',), False]" \
    "[('of_n_entries',), 1]" \
    "[('entries', {'cycle': '20000101T0000Z'}, 'n_states', 'success',), 2]" \
    "[('entries', {'cycle': '20000101T0000Z'}, 'n_states', 'job_success',), 2]"

TEST_NAME="${TEST_NAME_BASE}-200-curl-jobs"
run_ok "${TEST_NAME}" \
    curl "${TEST_CYLC_WS_URL}/taskjobs/${USER}/${SUITE_NAME}?form=json"
FOO0="{'cycle': '20000101T0000Z', 'name': 'foo0', 'submit_num': 1}"
FOO0_JOB='log/job/20000101T0000Z/foo0/01/job'
FOO1="{'cycle': '20000101T0000Z', 'name': 'foo1', 'submit_num': 1}"
FOO1_JOB='log/job/20000101T0000Z/foo1/01/job'
cylc_ws_json_greps "${TEST_NAME}.stdout" "${TEST_NAME}.stdout" \
    "[('cylc_version',), '$(cylc version | cut -d' ' -f 2)']" \
    "[('title',), 'Cylc Nameless']" \
    "[('host',), '$(hostname)']" \
    "[('user',), '${USER}']" \
    "[('suite',), '${SUITE_NAME}']" \
    "[('info', 'project',), 'survey']" \
    "[('info', 'title',), 'hms beagle']" \
    "[('is_option_on',), False]" \
    "[('page',), 1]" \
    "[('n_pages',), 1]" \
    "[('per_page',), 15]" \
    "[('per_page_default',), 15]" \
    "[('per_page_max',), 300]" \
    "[('cycles',), None]" \
    "[('order',), None]" \
    "[('states', 'is_running',), False]" \
    "[('states', 'is_failed',), False]" \
    "[('of_n_entries',), 2]" \
    "[('entries', ${FOO0}, 'task_status',), 'succeeded']" \
    "[('entries', ${FOO0}, 'host',), 'localhost']" \
    "[('entries', ${FOO0}, 'submit_method',), 'background']" \
    "[('entries', ${FOO0}, 'logs', 'job', 'path'), '${FOO0_JOB}']" \
    "[('entries', ${FOO0}, 'logs', 'job.err', 'path'), '${FOO0_JOB}.err']" \
    "[('entries', ${FOO0}, 'logs', 'job.stdout', 'path'), '${FOO0_JOB}.stdout']" \
    "[('entries', ${FOO0}, 'logs', 'job.01.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO0}, 'logs', 'job.05.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO0}, 'logs', 'job.10.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.*.txt', '1'), 'job.01.txt']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.*.txt', '5'), 'job.05.txt']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.*.txt', '10'), 'job.10.txt']" \
    "[('entries', ${FOO0}, 'logs', 'bunch.holly.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO0}, 'logs', 'bunch.iris.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO0}, 'logs', 'bunch.daisy.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'bunch.*.stdout', 'holly'), 'bunch.holly.stdout']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'bunch.*.stdout', 'iris'), 'bunch.iris.stdout']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'bunch.*.stdout', 'daisy'), 'bunch.daisy.stdout']" \
    "[('entries', ${FOO0}, 'logs', 'job.trace.2.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO0}, 'logs', 'job.trace.32.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO0}, 'logs', 'job.trace.256.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.trace.*.html', '2'), 'job.trace.2.html']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.trace.*.html', '32'), 'job.trace.32.html']" \
    "[('entries', ${FOO0}, 'seq_logs_indexes', 'job.trace.*.html', '256'), 'job.trace.256.html']" \
    "[('entries', ${FOO1}, 'task_status',), 'succeeded']" \
    "[('entries', ${FOO1}, 'host',), 'localhost']" \
    "[('entries', ${FOO1}, 'submit_method',), 'background']" \
    "[('entries', ${FOO1}, 'logs', 'job', 'path'), '${FOO1_JOB}']" \
    "[('entries', ${FOO1}, 'logs', 'job.err', 'path'), '${FOO1_JOB}.err']" \
    "[('entries', ${FOO1}, 'logs', 'job.stdout', 'path'), '${FOO1_JOB}.stdout']" \
    "[('entries', ${FOO1}, 'logs', 'job.01.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO1}, 'logs', 'job.05.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO1}, 'logs', 'job.10.txt', 'seq_key'), 'job.*.txt']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.*.txt', '1'), 'job.01.txt']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.*.txt', '5'), 'job.05.txt']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.*.txt', '10'), 'job.10.txt']" \
    "[('entries', ${FOO1}, 'logs', 'bunch.holly.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO1}, 'logs', 'bunch.iris.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO1}, 'logs', 'bunch.daisy.stdout', 'seq_key'), 'bunch.*.stdout']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'bunch.*.stdout', 'holly'), 'bunch.holly.stdout']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'bunch.*.stdout', 'iris'), 'bunch.iris.stdout']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'bunch.*.stdout', 'daisy'), 'bunch.daisy.stdout']" \
    "[('entries', ${FOO1}, 'logs', 'job.trace.2.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO1}, 'logs', 'job.trace.32.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO1}, 'logs', 'job.trace.256.html', 'seq_key'), 'job.trace.*.html']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.trace.*.html', '2'), 'job.trace.2.html']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.trace.*.html', '32'), 'job.trace.32.html']" \
    "[('entries', ${FOO1}, 'seq_logs_indexes', 'job.trace.*.html', '256'), 'job.trace.256.html']"

# A suite run directory with only a "log/db", and nothing else
TEST_DIR2="$(mktemp -d --tmpdir="${HOME}/cylc-run" "ctb-cylc-nameless-00-XXXXXXXX")"
SUITE_NAME2="$(basename "${TEST_DIR2}")"
cp "${TEST_DIR}/log/db" "${TEST_DIR2}/"
run_ok "${TEST_NAME}-bare" \
    curl "${TEST_CYLC_WS_URL}/taskjobs/${USER}/${SUITE_NAME2}?form=json"
cylc_ws_json_greps "${TEST_NAME}-bare.stdout" "${TEST_NAME}-bare.stdout" \
    "[('suite',), '${SUITE_NAME2}']"

for FILE in \
    'log/suite/log' \
    'log/job/20000101T0000Z/foo0/01/job' \
    'log/job/20000101T0000Z/foo0/01/job.stdout' \
    'log/job/20000101T0000Z/foo1/01/job' \
    'log/job/20000101T0000Z/foo1/01/job.stdout'
do
    TEST_NAME="${TEST_NAME_BASE}-200-curl-view-$(tr '/' '-' <<<"${FILE}")"
    run_ok "${TEST_NAME}" \
        curl -I "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=${FILE}"
    grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 200 OK' "${TEST_NAME}.stdout"
    MODE='&mode=download'
    run_ok "${TEST_NAME}-download" \
        curl "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=${FILE}${MODE}"
    cmp_ok "${TEST_NAME}-download.stdout" \
        "${TEST_NAME}-download.stdout" "${HOME}/cylc-run/${SUITE_NAME}/${FILE}"
done

TEST_NAME="${TEST_NAME_BASE}-404-curl-view-garbage"
run_ok "${TEST_NAME}" \
    curl -I \
    "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=log/of/minus-one"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 404 Not Found' "${TEST_NAME}.stdout"
#-------------------------------------------------------------------------------
# Test the file search feature.
TEST_NAME="${TEST_NAME_BASE}-200-curl-viewsearch"
FILE='log/job/20000101T0000Z/foo1/01/job.stdout'
MODE="&mode=text"
URL="${TEST_CYLC_WS_URL}/viewsearch/${USER}/${SUITE_NAME}?path=${FILE}${MODE}\
&search_mode=TEXT&search_string=Hello%20from"

run_ok "${TEST_NAME}" curl -I "${URL}"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 200 OK' "${TEST_NAME}.stdout"

TEST_NAME="${TEST_NAME_BASE}-200-curl-viewsearch-download"
run_ok "${TEST_NAME}" \
    curl "${URL}"
grep_ok "${TEST_NAME}.stdout" '<span class="highlight">Hello from</span>' \
    "${TEST_NAME}.stdout"
#-------------------------------------------------------------------------------
# Test requesting a file outside of the suite directory tree:
# 1. By absolute path.
TEST_NAME="${TEST_NAME_BASE}-403-curl-view-outside-absolute"
run_ok "${TEST_NAME}" \
    curl -I \
    "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=/dev/null"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 403 Forbidden' "${TEST_NAME}.stdout"
# 2. By absolute path to imaginary suite directory.
TEST_NAME="${TEST_NAME_BASE}-403-curl-view-outside-imag"
IMG_TEST_DIR="$(mktemp -d --tmpdir="${HOME}/cylc-run" \
    "ctb-cylc-nameless-00-XXXXXXXX")"
echo 'Welcome to the imaginery suite.'>"${IMG_TEST_DIR}/welcome.txt"
run_ok "${TEST_NAME}" \
    curl -I \
    "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=${IMG_TEST_DIR}/welcome.txt"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 403 Forbidden' "${TEST_NAME}.stdout"
# 3. By relative path.
TEST_NAME="${TEST_NAME_BASE}-403-curl-view-outside-relative"
run_ok "${TEST_NAME}" \
    curl -I \
    "${TEST_CYLC_WS_URL}/view/${USER}/${SUITE_NAME}?path=../$(basename $IMG_TEST_DIR)/welcome.txt"
grep_ok "${TEST_NAME}.stdout" 'HTTP/.* 403 Forbidden' "${TEST_NAME}.stdout"
#-------------------------------------------------------------------------------
# Tidy up
cylc_ws_kill
rm -fr "${TEST_DIR}" "${TEST_DIR2}" 2>'/dev/null'
exit 0
