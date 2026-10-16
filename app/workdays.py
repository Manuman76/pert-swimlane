"""Working-day date arithmetic: the project calendar is a 5-day week.

Saturday and Sunday are not task time, so every date the scheduler produces or
consumes lands on Monday-Friday: durations count working days, end dates never
fall on a weekend, and a start date entered on a Saturday rolls to Monday.

Only these helpers do weekend arithmetic; everything else (``Task.end_date``,
``recalculate_project``, ``project_date_stats``) goes through them so the rule
lives in exactly one place.
"""

from datetime import timedelta

# date.weekday(): Monday == 0 ... Saturday == 5, Sunday == 6
WEEKEND_WEEKDAYS = (5, 6)
WORKDAYS_PER_WEEK = 5


def is_workday(d):
    """True for Monday-Friday, False for Saturday and Sunday."""
    return d.weekday() not in WEEKEND_WEEKDAYS


def next_workday(d):
    """The first working day on or after `d` (Sat -> Mon, Sun -> Mon)."""
    while not is_workday(d):
        d = d + timedelta(days=1)
    return d


def add_workdays(start, days):
    """Return the date `days` working days after `start`, skipping Sat/Sun.

    The offset is counted in working days, not calendar days: 5 working days
    after a Monday is the next Monday, never a Saturday. `days == 0` simply
    rolls `start` onto a working day, so the result is always Mon-Fri.
    """
    if days < 0:
        raise ValueError("add_workdays() needs a non-negative number of days")

    d = next_workday(start)
    weeks, rest = divmod(days, WORKDAYS_PER_WEEK)
    if weeks:
        # Whole weeks keep the weekday, so they can be added in one jump.
        d = d + timedelta(weeks=weeks)
    for _ in range(rest):
        d = next_workday(d + timedelta(days=1))
    return d


def count_workdays(start, end):
    """Number of working days in `[start, end)`.

    Inverse of :func:`add_workdays` for working-day `start`/`end` pairs:
    ``count_workdays(a, add_workdays(a, n)) == n``. Returns 0 for an empty or
    inverted range, so callers never see a negative span.
    """
    if start is None or end is None or end <= start:
        return 0

    total_days = (end - start).days
    weeks, rest = divmod(total_days, 7)
    count = weeks * WORKDAYS_PER_WEEK
    for offset in range(rest):
        if is_workday(start + timedelta(days=offset)):
            count += 1
    return count
