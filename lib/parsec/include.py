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

import datetime
import os
import re
import sys
from shutil import copy as shcopy
from copy import copy
from parsec import ParsecError


class IncludeFileNotFoundError(ParsecError):

    def __init__(self, flist):
        """Missing include file error.

        E.g. for [DIR/top.rc, DIR/inc/sub.rc, DIR/inc/gone.rc]
        "Include-file not found: inc/gone.rc via inc/sub.rc from DIR/top.rc"
        """
        rflist = copy(flist)
        top_file = rflist[0]
        top_dir = os.path.dirname(top_file) + '/'
        rflist.reverse()
        self.msg = (
            "Include-file not found: %s" % rflist[0].replace(top_dir, ''))
        for f in rflist[1:-1]:
            self.msg += ' via %s' % f.replace(top_dir, '')
        self.msg += ' from %s' % top_file

done = []
modtimes = {}
backups = {}
newfiles = []
flist = []

include_re = re.compile('\s*%include\s+([\'"]?)(.*?)([\'"]?)\s*$')


def inline(lines, dir, file, for_grep=False, for_edit=False, viewcfg={},
           level=None):
    """Recursive inlining of parsec include-files"""

    global flist
    if level is None:
        # avoid being affected by multiple *different* calls to this function
        flist = [file]
    else:
        flist.append(file)
    single = False
    mark = False
    label = False
    if viewcfg:
        mark = viewcfg['mark']
        single = viewcfg['single']
        label = viewcfg['label']

    global done
    global modtimes

    outf = []
    initial_line_index = 0

    if level is None:
        level = ''
        if for_edit:
            m = re.match('^(#![jJ]inja2)', lines[0])
            if m:
                outf.append(m.groups()[0])
                initial_line_index = 1
            outf.append(
                """# !WARNING! CYLC EDIT INLINED (DO NOT MODIFY THIS LINE).
# !WARNING! This is an inlined parsec config file; include-files are split
# !WARNING! out again on exiting the edit session.  If you are editing
# !WARNING! this file manually then a previous inlined session may have
# !WARNING! crashed; exit now and use 'cylc edit -i' to recover (this
# !WARNING! will split the file up again on exiting).""")

    else:
        if mark:
            level += '!'
        elif for_edit:
            level += ' > '

    if for_edit:
        msg = ' (DO NOT MODIFY THIS LINE!)'
    else:
        msg = ''

    for line in lines[initial_line_index:]:
        m = include_re.match(line)
        if m:
            q1, match, q2 = m.groups()
            if q1 and (q1 != q2):
                raise IncludeFileError("ERROR, mismatched quotes: " + line)
            inc = os.path.join(dir, match)
            if inc not in done:
                if single or for_edit:
                    done.append(inc)
                if for_edit:
                    backup(inc)
                    # store original modtime
                    modtimes[inc] = os.stat(inc).st_mtime
                if os.path.isfile(inc):
                    if for_grep or single or label or for_edit:
                        outf.append(
                            '#++++ START INLINED INCLUDE FILE ' + match + msg)
                    h = open(inc, 'rb')
                    finc = [line.rstrip('\n') for line in h]
                    h.close()
                    # recursive inclusion
                    outf.extend(inline(
                        finc, dir, inc, for_grep, for_edit, viewcfg, level))
                    if for_grep or single or label or for_edit:
                        outf.append(
                            '#++++ END INLINED INCLUDE FILE ' + match + msg)
                else:
                    flist.append(inc)
                    raise IncludeFileNotFoundError(flist)
            else:
                if not for_edit:
                    outf.append(level + line)
                else:
                    outf.append(line)
        else:
            # no match
            if not for_edit:
                outf.append(level + line)
            else:
                outf.append(line)
    return outf


def cleanup(suitedir):
    print 'CLEANUP REQUESTED, deleting:'
    for root, dirs, files in os.walk(suitedir):
        for file in files:
            if re.search('\.EDIT\..*$', file):
                print ' + ' + re.sub(suitedir + '/', '', file)
                os.unlink(os.path.join(root, file))


def backup(src, tag=''):
    if not os.path.exists(src):
        raise SystemExit("File not found: " + src)
    bkp = src + tag + '.EDIT.' + datetime.datetime.now().isoformat()
    global backups
    shcopy(src, bkp)
    backups[src] = bkp


def split_file(dir, lines, file, recovery=False, level=None):
    global modtimes
    global newfiles

    if level is None:
        # config file itself
        level = ''
    else:
        level += ' > '
        # check mod time on the target file
        if not recovery:
            mtime = os.stat(file).st_mtime
            if mtime != modtimes[file]:
                # oops - original file has changed on disk since we started
                # editing
                file += '.EDIT.NEW.' + datetime.datetime.now().isoformat()
        newfiles.append(file)

    inclines = []
    fnew = open(file, 'wb')
    match_on = False
    for line in lines:
        if re.match('^# !WARNING!', line):
            continue
        if not match_on:
            m = re.match(
                '^#\+\+\+\+ START INLINED INCLUDE FILE ' +
                '([\w\/\.\-]+) \(DO NOT MODIFY THIS LINE!\)', line)
            if m:
                match_on = True
                inc_filename = m.groups()[0]
                inc_file = os.path.join(dir, m.groups()[0])
                fnew.write('%include ' + inc_filename + '\n')
            else:
                fnew.write(line)
        elif match_on:
            # match on, go to end of the 'on' include-file
            m = re.match(
                '^#\+\+\+\+ END INLINED INCLUDE FILE ' +
                inc_filename + ' \(DO NOT MODIFY THIS LINE!\)', line)
            if m:
                match_on = False
                # now split this lot, in case of nested inclusions
                split_file(dir, inclines, inc_file, recovery, level)
                # now empty the inclines list ready for the next inclusion in
                # this file
                inclines = []
            else:
                inclines.append(line)
    if match_on:
        for line in inclines:
            fnew.write(line)
        print >> sys.stderr
        print >> sys.stderr, (
            "ERROR: end-of-file reached while matching include-file",
            inc_filename + ".")
        print >> sys.stderr, (
            """This probably means you have corrupted the inlined file by
modifying one of the include-file boundary markers. Fix the backed-
up inlined file, copy it to the original filename and invoke another
inlined edit session split the file up again.""")
        print >> sys.stderr
