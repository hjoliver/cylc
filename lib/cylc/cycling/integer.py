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
"""
Integer cycling by point, interval, and sequence classes.
"""

import re
import unittest

from cylc.cycling import (
    PointBase, IntervalBase, SequenceBase, PointParsingError,
    IntervalParsingError, parse_exclusion
)
from cylc.time_parser import CylcMissingContextPointError

CYCLER_TYPE_INTEGER = "integer"
CYCLER_TYPE_SORT_KEY_INTEGER = "a"

# TODO - abbreviated integer recurrences?

# INTEGER RECURRENCE REGEXES
#
# Intended to be integer analogues of the ISO8601 date time notation.
#
# Unlike ISO8601 time points there is no supported equivalent of a
# truncated point, so e.g. 'T00' has no direct analogue. Instead, we
# can use absolute integer points such as "5" or "10". We also can't
# extrapolate intervals from the date-time truncation information -
# e.g. assuming 'T00/P1D' from 'T00'.
#
# We can also use relative point notation in a similar way to the
# date-time offset notation. For example, we can write "5 after the
# initial cycle point" as '+P5'.
#
# In the following regular expression comments:
#     START and END: either absolute integers such as '1' or '5', or
#         initial-relative (start) or final-relative (end) offsets
#         such as '+P2' or '-P5'.
#     INITIAL and FINAL: the initial cycle point and final cycle point.
#     INTV: an integer interval such as 'P2'.
#     n: an integer denoting the number of repetitions.
#     format_num meanings:
#         1: run n times between START and END
#         3: start at START, keep adding INTV (if n, only for n points)
#         4: start at END, keep subtracting INTV (if n, only for n points)

RE_COMPONENTS = {
    "end": "(?P<end>[^PR/][^/]*)",
    "intv": "(?P<intv>P[^/]*)",
    "reps_1": "R(?P<reps>1)",
    "reps": "R(?P<reps>\d+)",
    "start": "(?P<start>[^PR/][^/]*)"
}

RECURRENCE_FORMAT_RECS = [
    (re.compile(regex % RE_COMPONENTS), format_num)
    for (regex, format_num) in [
        # START (not supported)
        # (r"^%(start)s$", 3),
        # Rn/START/END
        # e.g. R3/0/10
        (r"^%(reps)s/%(start)s/%(end)s$", 1),
        # START/INTV, implies R/START/INTV
        # e.g. +P5/P3, 2/P2
        (r"^%(start)s/%(intv)s/?$", 3),
        # INTV, implies R/INITIAL/INTV
        # e.g. P3, P10
        (r"^%(intv)s$", 3),
        # INTV/END, implies R/INTV/END, count backwards from END
        # e.g. P3/-P1
        (r"^%(intv)s/%(end)s$", 4),
        # Rn/START (not supported)
        # (r"^%(reps)s?/%(start)s/?$", 3),
        # but: R1/START (supported)
        # e.g. R1/5, R1/+P3
        (r"^%(reps_1)s?/%(start)s/?$", 3),
        (r"^%(reps)s?/%(start)s/%(intv)s$", 3),
        # Rn/START/INTV
        # e.g. R2/3/P3
        (r"^%(reps)s?/(?P<start>)/%(intv)s$", 3),
        # Rn/INTV/END
        # e.g. R5/P2/10, R7/P1/+P20
        (r"^%(reps)s?/%(intv)s/%(end)s$", 4),
        # Rn/INTV, implies R/INTV/FINAL
        # e.g. R5/P2, R7/P1
        (r"^%(reps)s?/%(intv)s/?$", 4),
        # Rn//END (not supported)
        # (r"^%(reps_1)s//%(end)s$", 4),
        # R1, run once at INITIAL
        # e.g. R1, R1/
        (r"^%(reps_1)s/?(?P<start>$)", 3),
        # R1//END, run once at END.
        # e.g. R1//-P2
        (r"^%(reps_1)s//%(end)s$", 4)
    ]
]

REC_RELATIVE_POINT = re.compile("^[-+]P\d+$")
REC_INTERVAL = re.compile("^[-+]?P\d+$")


class IntegerPoint(PointBase):

    """A single point in an integer sequence."""

    TYPE = CYCLER_TYPE_INTEGER
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_INTEGER

    def __init__(self, value):
        if isinstance(value, int):
            value = str(value)
        super(IntegerPoint, self).__init__(value)

    def add(self, other):
        """Add other.value to self.value as integers."""
        return IntegerPoint(int(self) + int(other))

    def cmp_(self, other):
        """Compare self.value to self.other as integers with 'cmp'."""
        return cmp(int(self), int(other))

    def sub(self, other):
        """Subtract other.value from self.value as integers."""
        if isinstance(other, IntegerPoint):
            return IntegerInterval.from_integer(int(self) - int(other))
        return IntegerPoint(int(self) - int(other))

    def standardise(self):
        """Format self.value into a standard representation and check it."""
        try:
            self.value = str(int(self))
        except (TypeError, ValueError) as exc:
            raise PointParsingError(type(self), self.value, exc)
        return self

    def __int__(self):
        # Provide a nice way to use the string self.value in calculations.
        return int(self.value)

    def __hash__(self):
        return hash(self.value)


class IntegerInterval(IntervalBase):

    """The interval between points in an integer sequence."""

    TYPE = CYCLER_TYPE_INTEGER
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_INTEGER

    @classmethod
    def from_integer(cls, integer):
        """Return an instance of this class using integer."""
        if integer < 0:
            value = "-P" + str(abs(integer))
        else:
            value = "P" + str(integer)
        return IntegerInterval(value)

    @classmethod
    def get_null(cls):
        """Return a null interval."""
        return IntegerInterval("P0")

    @classmethod
    def get_null_offset(cls):
        """Return a null offset."""
        return IntegerInterval("+P0")

    def get_inferred_child(self, string):
        """For a given string, infer the offset given my instance units."""
        try:
            IntegerInterval.from_integer(int(string))
        except (TypeError, ValueError):
            return IntegerInterval(string)

    def __init__(self, value):
        if (not isinstance(value, basestring) or
                not REC_INTERVAL.search(value)):
            raise IntervalParsingError("IntegerInterval", repr(value))
        super(IntegerInterval, self).__init__(value)

    def add(self, other):
        """Add other to self as integers (point or interval)."""
        if isinstance(other, IntegerInterval):
            return IntegerInterval.from_integer(int(self) + int(other))
        return IntegerPoint(int(self) + int(other))

    def cmp_(self, other):
        """Compare other to self as integers."""
        return cmp(int(self), int(other))

    def sub(self, other):
        """Subtract other from self as integers."""
        return IntegerInterval.from_integer(int(self) - int(other))

    def __abs__(self):
        # Return an interval with absolute values for all properties.
        return IntegerInterval.from_integer(abs(int(self)))

    def __int__(self):
        # Provide a nice way to use the string self.value in calculations.
        return int(self.value.replace("P", ""))

    def __mul__(self, factor):
        # Return an interval with all properties multiplied by factor.
        return IntegerInterval.from_integer(int(self) * factor)

    def __nonzero__(self):
        # Return True if the interval has any non-zero properties.
        return bool(int(self))


class IntegerSequence(SequenceBase):
    """Integer points at a regular interval."""

    TYPE = CYCLER_TYPE_INTEGER
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_INTEGER

    @classmethod
    def get_async_expr(cls, start_point=None):
        """Express a one-off sequence at the initial cycle point."""
        if start_point is None:
            return "R1"
        return 'R1/' + str(start_point)

    def __init__(self, dep_section, p_context_start, p_context_stop=None):
        """Parse state (start, stop, interval) from a graph section heading.
        The start and stop points are always on-sequence, context points
        might not be. If computed start and stop points are out of bounds,
        they will be set to None. Context is used only initially to define
        the sequence bounds."""

        # start context always exists
        self.p_context_start = IntegerPoint(p_context_start)
        # stop context may exist
        if p_context_stop:
            self.p_context_stop = IntegerPoint(p_context_stop)
        else:
            self.p_context_stop = None

        # state variables: start, stop, and step
        self.p_start = None
        self.p_stop = None
        self.i_step = None

        # offset must be stored to compute the runahead limit
        self.i_offset = IntegerInterval('P0')

        matched_recurrence = False

        expression, exclusion = parse_exclusion(dep_section)

        for rec, format_num in RECURRENCE_FORMAT_RECS:
            results = rec.match(expression)
            if not results:
                continue
            matched_recurrence = True
            reps = results.groupdict().get("reps")
            if reps is not None:
                reps = int(reps)
            start = results.groupdict().get("start")
            stop = results.groupdict().get("end")
            intv = results.groupdict().get("intv")
            if not start:
                start = None
            if not stop:
                stop = None
            if not intv:
                intv = None
            start_required = (format_num in [1, 3])
            end_required = (format_num in [1, 4])
            break

        if not matched_recurrence:
            raise Exception(
                "ERROR, bad integer cycling format: %s" % expression)

        self.p_start = get_point_from_expression(
            start, self.p_context_start, is_required=start_required)
        self.p_stop = get_point_from_expression(
            stop, self.p_context_stop, is_required=end_required)
        if intv:
            self.i_step = IntegerInterval(intv)
        if exclusion:
            self.exclusion = get_point_from_expression(exclusion, None,
                                                       is_required=False)
        else:
            self.exclusion = None

        if format_num == 3:
            # REPEAT/START/PERIOD
            if not intv or reps is not None and reps <= 1:
                # one-off
                self.i_step = None
                self.p_stop = self.p_start
            else:
                self.i_step = IntegerInterval(intv)
                if reps:
                    self.p_stop = self.p_start + self.i_step * (reps - 1)
                elif self.p_context_stop:
                    # stop at the point <= self.p_context_stop
                    # use p_start as an on-sequence reference
                    remainder = (int(self.p_context_stop - self.p_start) %
                                 int(self.i_step))
                    self.p_stop = (
                        self.p_context_stop - IntegerInterval.from_integer(
                            remainder)
                    )
        elif format_num == 1:
            # REPEAT/START/STOP
            if reps == 1:
                # one-off: ignore stop point
                self.i_step = None
                self.p_stop = self.p_start
            else:
                self.i_step = IntegerInterval.from_integer(
                    int(self.p_stop - self.p_start) / (reps - 1)
                )
        else:
            # This means that format_num == 4.
            # REPEAT/PERIOD/STOP
            if reps is not None:
                if reps <= 1:
                    # one-off
                    self.p_start = self.p_stop
                    self.i_step = None
                else:
                    self.i_step = IntegerInterval(intv)
                    self.p_start = (
                        self.p_stop - self.i_step * (reps - 1))
            else:
                remainder = (int(self.p_context_stop - self.p_start) %
                             int(self.i_step))
                self.p_start = (
                    self.p_context_start - IntegerInterval.from_integer(
                        remainder)
                )

        if self.i_step and self.i_step < IntegerInterval.get_null():
            # (TODO - this should be easy to handle but needs testing)
            raise Exception(
                "ERROR, negative intervals not supported yet: %s" %
                self.i_step
            )

        if self.i_step and self.p_start < self.p_context_start:
            # start from first point >= context start
            remainder = (
                int(self.p_context_start - self.p_start) % int(self.i_step))
            self.p_start = (
                self.p_context_start + IntegerInterval.from_integer(
                    remainder)
            )
            # if i_step is None here, points will just be None (out of bounds)

        if (self.i_step and self.p_stop and self.p_context_stop and
                self.p_stop > self.p_context_stop):
            # stop at first point <= context stop
            remainder = (
                int(self.p_context_stop - self.p_start) % int(self.i_step))
            self.p_stop = (
                self.p_context_stop - self.i_step +
                IntegerInterval.from_interval(remainder)
            )
            # if i_step is None here, points will just be None (out of bounds)

    def get_interval(self):
        """Return the cycling interval of this sequence."""
        # interval may be None (a one-off sequence)
        return self.i_step

    def get_offset(self):
        """Deprecated: return the offset used for this sequence."""
        return self.i_offset

    def set_offset(self, i_offset):
        """Deprecated: alter state to offset the entire sequence."""
        if not i_offset.value:
            # no offset
            return
        if not self.i_step:
            # this is a one-off sequence
            self.p_start += i_offset
            self.p_stop += i_offset
            if self.p_start < self.p_context_start:
                self.p_start = self.p_stop = None
            return
        if not int(i_offset) % int(self.i_step):
            # offset is a multiple of step
            return
        # shift to 0 < offset < interval
        i_offset = IntegerInterval.from_integer(
            int(i_offset) % int(self.i_step))
        self.i_offset = i_offset
        self.p_start += i_offset  # can be negative
        if self.p_start < self.p_context_start:
            self.p_start += self.i_step
        self.p_stop += i_offset
        if self.p_stop > self.p_context_stop:
            self.p_stop -= self.i_step

    def is_on_sequence(self, point):
        """Is point on-sequence, disregarding bounds?"""
        if self.exclusion and point == self.exclusion:
            return False
        if self.i_step:
            return int(point - self.p_start) % int(self.i_step) == 0
        else:
            return point == self.p_start

    def _get_point_in_bounds(self, point):
        """Return point, or None if out of bounds."""
        if point >= self.p_start and (
                self.p_stop is None or point <= self.p_stop):
            return point
        else:
            return None

    def is_valid(self, point):
        """Is point on-sequence and in-bounds?"""
        return (self.is_on_sequence(point) and
                point >= self.p_start and
                (self.p_stop is None or
                    point <= self.p_stop))

    def get_prev_point(self, point):
        """Return the previous point < point, or None if out of bounds."""
        # Only used in computing special sequential task prerequisites.
        if not self.i_step:
            # implies a one-off task was declared sequential
            # TODO - check this results in sensible behaviour
            return None
        i = int(point - self.p_start) % int(self.i_step)
        if i:
            prev_point = point - IntegerInterval.from_integer(i)
        else:
            prev_point = point - self.i_step
        ret = self._get_point_in_bounds(prev_point)
        if self.exclusion and ret == self.exclusion:
            return self.get_prev_point(ret)
        return ret

    def get_nearest_prev_point(self, point):
        """Return the largest point < some arbitrary point."""
        if self.is_on_sequence(point):
            return self.get_prev_point(point)
        sequence_point = self._get_point_in_bounds(self.p_start)
        prev_point = None
        while sequence_point is not None:
            if sequence_point > point:
                # Technically, >=, but we already test for this above.
                break
            prev_point = sequence_point
            sequence_point = self.get_next_point(sequence_point)
        if self.exclusion and prev_point == self.exclusion:
            return self.get_nearest_prev_point(prev_point)
        return prev_point

    def get_next_point(self, point):
        """Return the next point > point, or None if out of bounds."""
        if not self.i_step:
            # this is a one-off sequence
            # TODO - is this needed? if so, check it gives sensible behaviour
            if point < self.p_start:
                return self.p_start
            else:
                return None
        i = int(point - self.p_start) % int(self.i_step)
        next_point = point + self.i_step - IntegerInterval.from_integer(i)
        ret = self._get_point_in_bounds(next_point)
        if self.exclusion and ret and ret == self.exclusion:
            return self.get_next_point(ret)
        return ret

    def get_next_point_on_sequence(self, point):
        """Return the next point > point assuming that point is on-sequence,
        or None if out of bounds."""
        # This can be used when working with a single sequence.
        if not self.i_step:
            return None
        next_point = point + self.i_step
        ret = self._get_point_in_bounds(next_point)
        if self.exclusion and ret and ret == self.exclusion:
            return self.get_next_point_on_sequence(ret)
        return ret

    def get_first_point(self, point):
        """Return the first point >= to point, or None if out of bounds."""
        # Used to find the first point >= suite initial cycle point.
        if point <= self.p_start:
            point = self._get_point_in_bounds(self.p_start)
        elif self.is_on_sequence(point):
            point = self._get_point_in_bounds(point)
        else:
            point = self.get_next_point(point)
        if self.exclusion and point == self.exclusion:
            return self.get_next_point_on_sequence(point)
        return point

    def get_start_point(self):
        """Return the first point in this sequence, or None."""
        if self.exclusion and self.p_start == self.exclusion:
            return self.get_next_point_on_sequence(self.p_start)
        return self.p_start

    def get_stop_point(self):
        """Return the last point in this sequence, or None if unbounded."""
        if self.exclusion and self.p_stop == self.exclusion:
            return self.get_prev_point(self.p_stop)
        return self.p_stop

    def __eq__(self, other):
        # Return True if other (sequence) is equal to self.
        if self.i_step and not other.i_step or \
                not self.i_step and other.i_step:
            return False
        else:
            return self.i_step == other.i_step and \
                self.p_start == other.p_start and \
                self.p_stop == other.p_stop and \
                self.exclusion == other.exclusion


def init_from_cfg(cfg):
    """Placeholder function required by all cycling modules."""
    pass


def get_point_relative(point_expr, context_point):
    """Create a point from relative_string applied to base_point."""
    if REC_RELATIVE_POINT.search(point_expr):
        # This is a relative point expression e.g. '+P2' or '-P12'.
        return context_point + IntegerInterval(point_expr)
    # This is an absolute point expression e.g. '4'.
    return IntegerPoint(point_expr)


def get_point_from_expression(point_expr, context_point, is_required=False):
    """Return a point from an absolute or relative point_expr."""
    if point_expr is None and context_point is None:
        if is_required:
            raise CylcMissingContextPointError(
                "Missing context cycle point."
            )
        return None
    if point_expr is None:
        return context_point
    return get_point_relative(point_expr, context_point)


class TestIntegerSequence(unittest.TestCase):
    """Contains unit tests for the IntegerSequence class."""

    def test_exclusions_simple(self):
        """Test the generation of points for integer sequences with exclusions.
        """
        sequence = IntegerSequence('R/P1!3', 1, 5)
        output = []
        point = sequence.get_start_point()
        while point:
            output.append(point)
            point = sequence.get_next_point(point)
        self.assertEqual([int(out) for out in output], [1, 2, 4, 5])

    def test_exclusions_extensive(self):
        """Test IntegerSequence methods for sequences with exclusions."""
        point_0 = IntegerPoint(0)
        point_1 = IntegerPoint(1)
        point_2 = IntegerPoint(2)
        point_3 = IntegerPoint(3)
        point_4 = IntegerPoint(4)

        sequence = IntegerSequence('R/P1!3', 1, 5)
        self.assertFalse(sequence.is_on_sequence(point_3))
        self.assertFalse(sequence.is_valid(point_3))
        self.assertEqual(sequence.get_prev_point(point_3), point_2)
        self.assertEqual(sequence.get_prev_point(point_4), point_2)
        self.assertEqual(sequence.get_nearest_prev_point(point_3), point_2)
        self.assertEqual(sequence.get_nearest_prev_point(point_3), point_2)
        self.assertEqual(sequence.get_next_point(point_3), point_4)
        self.assertEqual(sequence.get_next_point(point_2), point_4)
        self.assertEqual(sequence.get_next_point_on_sequence(point_3), point_4)
        self.assertEqual(sequence.get_next_point_on_sequence(point_2), point_4)

        sequence = IntegerSequence('R/P1!1', 1, 5)
        self.assertEqual(sequence.get_first_point(point_1), point_2)
        self.assertEqual(sequence.get_first_point(point_0), point_2)
        self.assertEqual(sequence.get_start_point(), point_2)

        sequence = IntegerSequence('R/P1!5', 1, 5)
        self.assertEqual(sequence.get_stop_point(), point_4)

    def test_simple(self):
        """Run some simple tests for integer cycling."""
        sequence = IntegerSequence('R/1/P3', 1, 10)
        start = sequence.p_start
        stop = sequence.p_stop

        # Test point generation forwards.
        point = start
        output = []
        while point and stop and point <= stop:
            output.append(point)
            point = sequence.get_next_point(point)
        self.assertEqual([int(out) for out in output], [1, 4, 7, 10])

        # Test point generation backwards.
        point = stop
        output = []
        while point and start and point >= start:
            output.append(point)
            point = sequence.get_prev_point(point)
        self.assertEqual([int(out) for out in output], [10, 7, 4, 1])

        # Test sequence comparison
        sequence1 = IntegerSequence('R/1/P2', 1, 10)
        sequence2 = IntegerSequence('R/1/P2', 1, 10)
        self.assertEqual(sequence1, sequence2)
        sequence2.set_offset(IntegerInterval('-P2'))
        self.assertEqual(sequence1, sequence2)
        sequence2.set_offset(IntegerInterval('-P1'))
        self.assertNotEqual(sequence1, sequence2)


if __name__ == '__main__':
    unittest.main()
