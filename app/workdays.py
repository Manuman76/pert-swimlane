"""Working-day date arithmetic: supports configurable working days and holidays.

Working days count project task time, end dates never fall on non-working days
or holidays, and a start date entered on a non-working day rolls forward to the
next working day.

Both standalone functions (defaulting to Mon-Fri with no holidays) and the
`WorkdayCalendar` class are provided.
"""

from datetime import timedelta

# date.weekday(): Monday == 0 ... Saturday == 5, Sunday == 6
WEEKEND_WEEKDAYS = (5, 6)
WORKDAYS_PER_WEEK = 5
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_WORKDAYS = {0, 1, 2, 3, 4}


class WorkdayCalendar:
    """Project-specific calendar with configurable working days and holidays."""

    def __init__(self, workdays=None, holidays=None):
        if workdays:
            valid = {int(w) for w in workdays if 0 <= int(w) <= 6}
            self.workdays = valid if valid else set(DEFAULT_WORKDAYS)
        else:
            self.workdays = set(DEFAULT_WORKDAYS)
        self.holidays = set(holidays) if holidays else set()

    def is_workday(self, d):
        """True if `d` falls on an enabled working day and is not a holiday."""
        if not d:
            return False
        return d.weekday() in self.workdays and d not in self.holidays

    def next_workday(self, d):
        """The first working day on or after `d`."""
        if not d:
            return None
        # Safety: ensure workdays has at least 1 day
        if not self.workdays:
            self.workdays = set(DEFAULT_WORKDAYS)
        while not self.is_workday(d):
            d = d + timedelta(days=1)
        return d

    def add_workdays(self, start, days):
        """Return the date `days` working days after `start`, skipping non-working days and holidays.

        `days == 0` simply rolls `start` onto a working day.
        """
        if days < 0:
            raise ValueError("add_workdays() needs a non-negative number of days")
        if not start:
            return None

        d = self.next_workday(start)
        for _ in range(days):
            d = self.next_workday(d + timedelta(days=1))
        return d

    def count_workdays(self, start, end):
        """Number of working days in `[start, end)`.

        Inverse of :func:`add_workdays` for working-day `start`/`end` pairs:
        ``count_workdays(a, add_workdays(a, n)) == n``. Returns 0 for an empty or
        inverted range.
        """
        if start is None or end is None or end <= start:
            return 0

        curr = start
        count = 0
        while curr < end:
            if self.is_workday(curr):
                count += 1
            curr += timedelta(days=1)
        return count

    def diff_workdays(self, start, end):
        """Signed number of working days from `start` to `end`.

        Positive when `end > start` (e.g. +2 workdays delay), negative when
        `end < start` (e.g. -2 workdays ahead of schedule), and 0 when equal or
        if either date is missing.
        """
        if not start or not end:
            return 0
        if end > start:
            return self.count_workdays(start, end)
        elif start > end:
            return -self.count_workdays(end, start)
        return 0


DEFAULT_CALENDAR = WorkdayCalendar()


def is_workday(d, calendar=None):
    """True for configured working days; False for weekends and holidays."""
    cal = calendar or DEFAULT_CALENDAR
    return cal.is_workday(d)


def next_workday(d, calendar=None):
    """The first working day on or after `d`."""
    cal = calendar or DEFAULT_CALENDAR
    return cal.next_workday(d)


def add_workdays(start, days, calendar=None):
    """Return the date `days` working days after `start`."""
    cal = calendar or DEFAULT_CALENDAR
    return cal.add_workdays(start, days)


def count_workdays(start, end, calendar=None):
    """Number of working days in `[start, end)`."""
    cal = calendar or DEFAULT_CALENDAR
    return cal.count_workdays(start, end)


def diff_workdays(start, end, calendar=None):
    """Signed number of working days from `start` to `end`."""
    cal = calendar or DEFAULT_CALENDAR
    return cal.diff_workdays(start, end)

