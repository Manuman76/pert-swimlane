"""Resource capacity: the person-day compute core (``app/capacity.py``) and its migration.

Most of these are pure — :func:`build_capacity` takes a project (only for its
calendar), tasks and members, so a ``SimpleNamespace`` is enough. The database-backed
ones at the bottom cover what only real models can: the project's own working-day
pattern and holidays, ``display_progress`` feeding remaining effort, and the
``ALTER TABLE`` upgrades for installs that predate capacity.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import create_app
from app.capacity import (
    CAP_STATE_IDLE,
    CAP_STATE_NONE,
    CAP_STATE_NORMAL,
    CAP_STATE_OVER,
    CAP_STATE_TIGHT,
    capacity_percent,
    capacity_state,
    build_capacity,
    find_member_row,
    fmt_capacity_days,
    fmt_capacity_percent,
    week_full_label,
    week_head_label,
    week_label,
    week_start,
    worst_week,
)
from app.db import db
from app.models import Lane, MemberAbsence, Project, Task, TeamMember, Holiday, TASK_KIND_MILESTONE
from app.workdays import WorkdayCalendar

# 1 Jun 2026 is a Monday (ISO 2026-W23); the weeks below run Mon 1, Mon 8, Mon 15 Jun.
MON_1 = date(2026, 6, 1)
MON_8 = date(2026, 6, 8)
MON_15 = date(2026, 6, 15)
MON_22 = date(2026, 6, 22)
FRI_5 = date(2026, 6, 5)
FRI_29_MAY = date(2026, 5, 29)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTask:
    """Just enough of ``Task`` for the capacity reader."""

    _seq = 0

    def __init__(self, code, start, duration=1, responsible=None, allocation=None,
                 progress=0, milestone=False, started=False, done=False):
        type(self)._seq += 1
        self.id = type(self)._seq
        self.code = code
        self.start_date = start
        self.duration_days = 0 if milestone else duration
        self.responsible = responsible
        self.allocation_percent = allocation
        self.progress_percent = progress
        self.is_milestone = milestone
        self.started = started
        self.done = done
        self.kind = TASK_KIND_MILESTONE if milestone else "task"

    @property
    def display_progress(self):
        """Same 100/50/0 reading as the real model when no percent was recorded."""
        if self.progress_percent is not None:
            return max(0, min(100, int(self.progress_percent)))
        if self.done:
            return 100
        if self.started:
            return 50
        return 0


class FakeMember:
    def __init__(self, name, capacity=100, role=None, absences=()):
        self.id = name
        self.name = name
        self.capacity_percent = capacity
        self.role = role
        self.absences = list(absences)


def absence(start, end, reason=None):
    """An absence range: ``end`` is exclusive, like ``Task.end_date``."""
    return SimpleNamespace(start_date=start, end_date=end, reason=reason)


def project(workdays=None, holidays=()):
    """A stand-in project exposing only ``calendar``."""
    return SimpleNamespace(calendar=WorkdayCalendar(workdays=workdays, holidays=set(holidays)))


def capacity_of(*args, **kwargs):
    """Build capacity for the fixture week, pinned so no test depends on today."""
    kwargs.setdefault("weeks", 4)
    kwargs.setdefault("window_start", MON_1)
    return build_capacity(*args, **kwargs)


def cell(caps, name, week=0):
    return find_member_row(caps, name).weeks[week]


# ---------------------------------------------------------------------------
# Week plumbing
# ---------------------------------------------------------------------------

def test_week_start_snaps_every_day_back_to_monday():
    assert week_start(MON_1) == MON_1
    assert week_start(FRI_5) == MON_1
    assert week_start(MON_1 + timedelta(days=6)) == MON_1
    assert week_start(None) is None


def test_week_labels_are_readable_and_unambiguous():
    assert week_label(MON_1) == "W23"
    assert week_full_label(MON_1) == "2026-W23"
    assert week_head_label(MON_1) == "1 Jun"          # no leading zero
    assert week_head_label(date(2026, 12, 28)) == "28 Dec"


def test_formatting_helpers():
    assert fmt_capacity_days(5) == "5d"
    assert fmt_capacity_days(2.5) == "2.5d"
    assert fmt_capacity_days(0) == "0d"
    assert fmt_capacity_days(None) == ""
    assert fmt_capacity_percent(87) == "87%"
    assert fmt_capacity_percent(None) == "—"


# ---------------------------------------------------------------------------
# State bands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "demand,supply,state",
    [
        (0, 0, CAP_STATE_NONE),          # nothing due, nothing available
        (2, 5, CAP_STATE_IDLE),          # below 50%
        (2.5, 5, CAP_STATE_NORMAL),      # 50% boundary is normal
        (4.4, 5, CAP_STATE_NORMAL),      # 88% is busy, not tight
        (4.5, 5, CAP_STATE_TIGHT),       # 90% boundary is tight
        (5, 5, CAP_STATE_TIGHT),         # exactly full is not over
        (5.5, 5, CAP_STATE_OVER),
        (1, 0, CAP_STATE_OVER),          # work with nowhere to go
    ],
)
def test_state_bands(demand, supply, state):
    assert capacity_state(demand, supply) == state


def test_percent_is_none_when_there_is_no_supply():
    assert capacity_percent(3, 0) is None
    assert capacity_percent(0, 0) is None
    assert capacity_percent(3, 4) == 75


def test_zero_capacity_member_reads_none_when_idle_and_over_when_loaded():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada")],
        [FakeMember("Ada", capacity=0), FakeMember("Idle", capacity=0)],
    )
    assert cell(caps, "Ada").state == CAP_STATE_OVER
    assert cell(caps, "Ada").percent is None
    assert cell(caps, "Idle").state == CAP_STATE_NONE


# ---------------------------------------------------------------------------
# Demand: one task
# ---------------------------------------------------------------------------

def test_full_week_task_fills_one_person_exactly():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada")],
        [FakeMember("Ada")],
    )
    first = cell(caps, "Ada")
    assert first.demand == pytest.approx(5.0)
    assert first.supply == pytest.approx(5.0)
    assert first.percent == 100
    assert first.state == CAP_STATE_TIGHT
    assert first.over_days == pytest.approx(0.0)
    assert find_member_row(caps, "Ada").total_demand == pytest.approx(5.0)
    assert caps.members_over == 0


def test_ten_day_task_splits_evenly_across_two_weeks():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 10, responsible="Ada")], [FakeMember("Ada")])
    row = find_member_row(caps, "Ada")
    assert [c.demand for c in row.weeks[:2]] == [pytest.approx(5.0), pytest.approx(5.0)]


def test_twelve_day_task_spreads_three_days_into_the_third_week():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 12, responsible="Ada")], [FakeMember("Ada")], weeks=5)
    row = find_member_row(caps, "Ada")
    assert [c.demand for c in row.weeks[:3]] == [
        pytest.approx(5.0), pytest.approx(5.0), pytest.approx(2.0),
    ]


def test_days_are_counted_exactly_once_across_weeks():
    """The weekly buckets must be a partition of the task, not an approximation of it."""
    caps = build_capacity(
        project(),
        [FakeTask("001", date(2026, 5, 27), 37, responsible="Ada")],
        [FakeMember("Ada")],
        window_start=date(2026, 5, 25),
        weeks=12,
    )
    row = find_member_row(caps, "Ada")
    assert sum(c.demand for c in row.weeks) == pytest.approx(37.0)
    assert row.total_demand == pytest.approx(37.0)


def test_a_task_ending_on_monday_does_not_load_the_following_week():
    """``end_date`` is exclusive: the first day a successor may start is not worked."""
    caps = build_capacity(
        project(),
        # Fri 29 May, 4 working days -> worked Fri 29, Mon 1, Tue 2, Wed 3; ends Thu 4 Jun.
        [FakeTask("001", FRI_29_MAY, 4, responsible="Ada")],
        [FakeMember("Ada")],
        window_start=date(2026, 5, 25),
        weeks=6,
    )
    row = find_member_row(caps, "Ada")
    assert row.weeks[0].demand == pytest.approx(1.0)   # only Fri 29 May
    assert row.weeks[1].demand == pytest.approx(3.0)   # Mon–Wed of W23
    assert row.total_demand == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Demand: allocation, progress, milestones
# ---------------------------------------------------------------------------

def test_allocation_percent_scales_the_demand():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada", allocation=25)],
        [FakeMember("Ada")],
    )
    first = cell(caps, "Ada")
    assert first.demand == pytest.approx(1.25)
    assert first.percent == 25
    assert first.state == CAP_STATE_IDLE


def test_allocation_blank_means_full_time():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada", allocation=None)], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(5.0)


def test_allocation_above_one_hundred_is_clamped_to_one_owner():
    """One task has one owner, so it can never consume more than a whole person."""
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada", allocation=300)], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(5.0)


def test_allocation_zero_loads_nobody_and_shows_no_code():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada", allocation=0)], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(0.0)
    assert cell(caps, "Ada").task_codes == []


def test_remaining_effort_counts_only_what_is_left():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada", progress=60)], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(2.0)


def test_full_plan_mode_counts_the_whole_task_regardless_of_progress():
    task = FakeTask("001", MON_1, 5, responsible="Ada", progress=60)
    caps = capacity_of(project(), [task], [FakeMember("Ada")], remaining_effort=False)
    assert cell(caps, "Ada").demand == pytest.approx(5.0)
    assert caps.mode == "full"


def test_delivered_work_stops_loading_its_owner():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada", progress=100)], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(0.0)
    assert caps.mode == "remaining"


def test_progress_is_inferred_from_the_status_flags():
    """Same 100/50/0 reading as the progress bar when no explicit percent is set."""
    class StatusOnly(FakeTask):
        """A record with no percentage at all: ``display_progress`` falls back to flags."""
        display_progress = None

    started = StatusOnly("001", MON_1, 5, responsible="Ada", progress=None, started=True)
    done = StatusOnly("002", MON_1, 5, responsible="Ada", progress=None, done=True)
    caps = capacity_of(project(), [started, done], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(2.5)      # half of one 5-day task


def test_milestones_never_add_demand():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_1, 0, responsible="Ada", milestone=True)],
        [FakeMember("Ada")],
    )
    assert cell(caps, "Ada").demand == pytest.approx(5.0)


def test_milestones_are_inside_the_window_even_without_duration():
    caps = build_capacity(project(), [FakeTask("001", MON_22, 0, responsible="Ada", milestone=True)], [FakeMember("Ada")], weeks=1)
    assert caps.window_start == MON_22


# ---------------------------------------------------------------------------
# Demand: several tasks, unassigned, ghosts
# ---------------------------------------------------------------------------

def test_overlapping_tasks_stack_into_one_week():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_1, 5, responsible="Ada")],
        [FakeMember("Ada")],
    )
    first = cell(caps, "Ada")
    assert first.demand == pytest.approx(10.0)
    assert first.percent == 200
    assert first.state == CAP_STATE_OVER
    assert first.over_days == pytest.approx(5.0)
    assert first.task_codes == ["001", "002"]      # tooltip content


def test_week_cells_list_their_task_codes_for_the_tooltip():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_8, 5, responsible="Ada")],
        [FakeMember("Ada")],
    )
    row = find_member_row(caps, "Ada")
    assert row.weeks[0].task_codes == ["001"]
    assert row.weeks[1].task_codes == ["002"]


def test_assignee_names_are_matched_ignoring_case_and_padding():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="  ADA ")], [FakeMember("Ada")])
    assert cell(caps, "Ada").demand == pytest.approx(5.0)
    assert caps.ghosts == []


def test_unassigned_work_gets_its_own_row():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_1, 2, responsible=None)],
        [FakeMember("Ada")],
    )
    assert caps.unassigned_row is not None
    first = caps.unassigned_row.weeks[0]
    assert first.demand == pytest.approx(2.0)
    assert first.supply == pytest.approx(5.0)      # one hypothetical full-time person
    assert first.percent == 40                     # two of five days have no owner
    assert caps.unassigned_demand == pytest.approx(2.0)
    assert find_member_row(caps, "Unassigned") is None


def test_team_totals_exclude_the_unassigned_row():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_1, 3, responsible=None)],
        [FakeMember("Ada"), FakeMember("Bob")],
    )
    assert caps.totals[0].demand == pytest.approx(5.0)
    assert caps.totals[0].supply == pytest.approx(10.0)
    assert caps.total_demand == pytest.approx(5.0)


def test_assignees_who_are_not_team_members_are_reported_not_counted():
    """``tasks.responsible`` is free text, so a name can exist without a capacity row."""
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_1, 4, responsible="Bob")],
        [FakeMember("Ada")],
    )
    assert [r.name for r in caps.rows] == ["Ada"]
    assert caps.ghosts == [{"name": "Bob", "demand": pytest.approx(4.0), "codes": ["002"]}]
    assert caps.ghost_demand == pytest.approx(4.0)
    assert cell(caps, "Ada").demand == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Supply: capacity percent, absences, calendars, holidays
# ---------------------------------------------------------------------------

def test_capacity_percent_scales_supply():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 5, responsible="Ada")], [FakeMember("Ada", capacity=50)])
    first = cell(caps, "Ada")
    assert first.supply == pytest.approx(2.5)
    assert first.percent == 200
    assert first.state == CAP_STATE_OVER
    assert caps.members_over == 1


def test_absence_lowers_supply_without_touching_demand():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 3, responsible="Ada")],
        [FakeMember("Ada", absences=[absence(MON_1 + timedelta(days=1), MON_1 + timedelta(days=3))])],  # Tue–Wed
    )
    first = cell(caps, "Ada")
    assert first.supply == pytest.approx(3.0)
    assert first.demand == pytest.approx(3.0)
    assert first.state == CAP_STATE_TIGHT
    assert find_member_row(caps, "Ada").total_supply == pytest.approx(3.0 + 5 + 5 + 5)


def test_absence_over_a_weekend_consumes_nothing():
    caps = capacity_of(
        project(),
        [],
        [FakeMember("Ada", absences=[absence(date(2026, 6, 6), MON_8)])],  # Sat 6 + Sun 7 Jun
    )
    assert cell(caps, "Ada").supply == pytest.approx(5.0)

def test_overlapping_absences_are_not_counted_twice():
    caps = capacity_of(
        project(),
        [],
        [FakeMember("Ada", absences=[absence(MON_1, MON_1 + timedelta(days=2)),        # Mon, Tue
                                     absence(MON_1 + timedelta(days=1), MON_1 + timedelta(days=3))])],  # Tue, Wed
    )
    # Mon + Tue + Wed away — the shared Tuesday counted once, so 4 - 1 rather than 4 - 2.
    assert cell(caps, "Ada").supply == pytest.approx(2.0)


def test_holidays_lower_supply_and_demand_together():
    """A holiday is not worked, so a 5-day task simply runs into the next week."""
    caps = capacity_of(
        project(holidays={date(2026, 6, 3)}),                     # Wed 3 Jun
        [FakeTask("001", MON_1, 5, responsible="Ada")],
        [FakeMember("Ada")],
    )
    row = find_member_row(caps, "Ada")
    assert row.weeks[0].supply == pytest.approx(4.0)
    assert row.weeks[0].demand == pytest.approx(4.0)              # Mon, Tue, Thu, Fri
    assert row.weeks[1].demand == pytest.approx(1.0)              # spills to Mon 8 Jun
    assert row.total_demand == pytest.approx(5.0)


def test_week_with_only_holidays_has_no_supply_and_no_demand():
    holidays = {MON_1 + timedelta(days=n) for n in range(5)}
    caps = capacity_of(project(holidays=holidays), [], [FakeMember("Ada")], weeks=4)
    assert cell(caps, "Ada").state == CAP_STATE_NONE


def test_six_day_work_week_grows_supply():
    caps = capacity_of(project(workdays={0, 1, 2, 3, 4, 5}), [], [FakeMember("Ada")], weeks=4)
    assert cell(caps, "Ada").supply == pytest.approx(6.0)


def test_four_day_work_week_shows_part_time_as_over_allocation():
    """Duration counts the project's own working days, so a full task is 4 days here."""
    caps = capacity_of(
        project(workdays={0, 1, 2, 3}),
        [FakeTask("001", MON_1, 4, responsible="Ada")],
        [FakeMember("Ada", capacity=50)],
    )
    assert cell(caps, "Ada").supply == pytest.approx(2.0)
    assert cell(caps, "Ada").demand == pytest.approx(4.0)
    assert cell(caps, "Ada").percent == 200


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def test_window_defaults_to_the_project_span_rounded_to_whole_weeks():
    caps = build_capacity(
        project(),
        [FakeTask("001", date(2026, 6, 3), 4, responsible="Ada"),
         FakeTask("002", MON_15, 5, responsible="Ada")],
        [FakeMember("Ada")],
        min_weeks=0,
    )
    # The plan runs Wed 3 Jun – Mon 22 Jun (exclusive end); the chart is grown to whole
    # ISO weeks on both sides, which is 3 columns W23–W25: the last day worked is Fri 19.
    assert (caps.span_start, caps.span_end) == (date(2026, 6, 3), MON_22)
    assert caps.window_start == MON_1
    assert caps.window_weeks == 3
    assert caps.window_end == MON_22
    assert [w.label for w in caps.weeks] == ["W23", "W24", "W25"]
    assert caps.window_weeks == 3


def test_short_projects_are_padded_to_the_minimum_window():
    caps = build_capacity(project(), [FakeTask("001", MON_1, 2, responsible="Ada")], [FakeMember("Ada")])
    assert caps.window_weeks == 8
    assert caps.window_truncated is False


def test_padding_gives_a_short_plan_a_useful_horizon():
    """A two-week span still charts eight weeks, so there is room to plan into."""
    caps = build_capacity(
        project(),
        [FakeTask("001", MON_1, 2, responsible="Ada")],
        [FakeMember("Ada")],
        window_start=MON_1,
    )
    assert caps.window_weeks == 8
    assert caps.window_truncated is False


def test_a_window_the_caller_bounded_at_its_end_is_never_padded():
    caps = build_capacity(
        project(),
        [FakeTask("001", MON_1, 2, responsible="Ada")],
        [FakeMember("Ada")],
        window_start=MON_1,
        window_end=MON_8,
    )
    assert caps.window_weeks == 2
    assert caps.window_end == MON_15


def test_explicit_week_count_overrides_the_span_and_skips_padding():
    caps = build_capacity(project(), [FakeTask("001", MON_1, 2, responsible="Ada")], [FakeMember("Ada")], weeks=2)
    assert caps.window_weeks == 2


def test_long_projects_truncate_the_window_and_say_so():
    caps = build_capacity(
        project(),
        [FakeTask("001", MON_1, 200, responsible="Ada")],
        [FakeMember("Ada")],
    )
    assert caps.window_weeks == 26
    assert caps.window_truncated is True


def test_window_start_and_end_restrict_the_chart():
    caps = build_capacity(
        project(),
        [FakeTask("001", MON_1, 30, responsible="Ada")],
        [FakeMember("Ada")],
        window_start=MON_8,
        window_end=MON_22,
    )
    assert caps.window_start == MON_8
    assert caps.window_end == MON_22 + timedelta(weeks=1)   # window_end is inclusive of its week


def test_week_columns_carry_labels_and_working_days():
    caps = build_capacity(
        project(), [], [FakeMember("Ada")],
        window_start=MON_1, weeks=3, today=MON_8,
    )
    past, current, future = caps.weeks
    assert (past.label, past.full_label, past.head, past.month_label) == ("W23", "2026-W23", "1 Jun", "Jun 2026")
    assert (current.label, current.head) == ("W24", "8 Jun")
    assert (future.label, future.head, future.month_label) == ("W25", "15 Jun", "Jun 2026")
    assert past.working_days == 5
    assert (past.is_past, past.is_current, past.is_future) == (True, False, False)
    assert (current.is_past, current.is_current, current.is_future) == (False, True, False)
    assert (future.is_past, future.is_current, future.is_future) == (False, False, True)


def test_empty_inputs_produce_no_rows_and_no_division_errors():
    caps = build_capacity(project(), [], [], weeks=4)
    assert caps.rows == []
    assert caps.unassigned_row is None
    assert caps.has_capacity_data is False
    assert caps.members_total == 0
    assert caps.members_over == 0
    assert caps.peak_percent is None
    assert caps.utilization_percent is None
    assert caps.window_weeks == 4       # falls back to a window anchored on today


def test_a_project_with_no_tasks_still_shows_its_members_as_idle():
    caps = capacity_of(project(), [], [FakeMember("Ada"), FakeMember("Bob")])
    assert [r.name for r in caps.rows] == ["Ada", "Bob"]
    assert caps.rows[0].weeks[0].state == CAP_STATE_IDLE
    assert caps.members_idle == 2
    assert caps.has_work is False


# ---------------------------------------------------------------------------
# Headline figures and lookups
# ---------------------------------------------------------------------------

def test_headline_figures_for_the_kpi_card():
    caps = capacity_of(
        project(),
        [
            FakeTask("001", MON_1, 5, responsible="Ada"),
            FakeTask("002", MON_1, 6, responsible="Ada"),          # 5 of its 6 days land in W23
            FakeTask("003", MON_1, 1, responsible="Bob"),          # 1 of 5 -> 20%
        ],
        [FakeMember("Ada"), FakeMember("Bob")],
    )
    assert caps.members_over == 1
    assert caps.peak_percent == 200
    assert caps.peak_week_index == 0
    row = find_member_row(caps, "Ada")
    assert row.weeks_over == 1
    assert row.first_over_date == MON_1
    assert row.peak_percent == 200
    assert row.total_demand == pytest.approx(11.0)
    assert row.total_over_days == pytest.approx(5.0)   # 10 demand - 5 supply in the hot week
    assert caps.members_idle == 1                      # Bob


def test_row_aggregates_sum_the_window():
    caps = capacity_of(project(), [FakeTask("001", MON_1, 10, responsible="Ada", allocation=50)], [FakeMember("Ada")], weeks=4)
    row = find_member_row(caps, "Ada")
    assert row.total_demand == pytest.approx(5.0)
    assert row.total_supply == pytest.approx(20.0)
    assert row.utilization_percent == 25
    assert row.utilization_str == "25%"


def test_worst_week_finds_the_hottest_week_for_an_assignee():
    caps = capacity_of(
        project(),
        [
            FakeTask("001", MON_1, 5, responsible="Ada"),            # W23: 5 of 5
            FakeTask("002", MON_15, 10, responsible="Ada"),         # W24–W25: 5 of 5
            FakeTask("003", MON_15, 2, responsible="Ada"),          # W24 pushes it over
        ],
        [FakeMember("Ada")],
        weeks=6,
    )
    assert worst_week(caps, "Ada").start == MON_15
    assert worst_week(caps, "Ada").percent == 140
    assert worst_week(caps, "Ada", start=MON_1, end=FRI_5).start == MON_1
    assert worst_week(caps, "Nobody") is None


def test_worst_week_resolves_an_equal_peak_to_the_earliest_week():
    caps = capacity_of(
        project(),
        [FakeTask("001", MON_1, 5, responsible="Ada"), FakeTask("002", MON_15, 5, responsible="Ada")],
        [FakeMember("Ada")],
        weeks=6,
    )
    assert [c.percent for c in find_member_row(caps, "Ada").weeks[:3]] == [100, 0, 100]
    assert worst_week(caps, "Ada").start == MON_1


def test_find_member_row_is_case_insensitive_and_tolerates_blank_names():
    caps = capacity_of(project(), [], [FakeMember("Ada")])
    assert find_member_row(caps, "ADA").name == "Ada"
    assert find_member_row(caps, "") is None
    assert find_member_row(caps, None) is None


# ---------------------------------------------------------------------------
# Real models: the project calendar, and the columns added by _migrate_schema
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Per-module like the rest of the suite — this codebase has no conftest."""
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def seeded(app):
    project_row = Project(name="Capacity", workdays="0,1,2")          # Mon–Wed project
    db.session.add(project_row)
    db.session.flush()
    lane = Lane(project_id=project_row.id, name="Delivery", sort_order=1)
    ada = TeamMember(project_id=project_row.id, name="Ada", role="Tech lead", capacity_percent=60)
    db.session.add_all([lane, ada])
    db.session.flush()
    task = Task(
        project_id=project_row.id, code="001", lane_id=lane.id, name="Build",
        start_date=MON_1, duration_days=3, responsible="Ada", allocation_percent=50,
    )
    milestone = Task(
        project_id=project_row.id, code="002", lane_id=lane.id, name="Gate", kind=TASK_KIND_MILESTONE,
        start_date=MON_1, duration_days=0, responsible="Ada",
    )
    db.session.add_all([task, milestone])
    db.session.add(MemberAbsence(
        project_id=project_row.id, member_id=ada.id,
        start_date=MON_8, end_date=MON_8 + timedelta(days=1), reason="Conference",
    ))
    db.session.commit()
    return project_row


def test_capacity_reads_the_projects_own_working_days(app, seeded):
    caps = build_capacity(seeded, Task.query.filter_by(project_id=seeded.id).all(),
                          TeamMember.query.filter_by(project_id=seeded.id).all(), weeks=2)

    row = caps.rows[0]
    assert row.capacity_percent == 60
    assert row.role == "Tech lead"
    # A Mon–Wed project week at 60% capacity…
    assert row.weeks[0].supply == pytest.approx(3 * 0.6)
    # …and the conference Monday in week 2 takes one of those three days away.
    assert row.weeks[1].supply == pytest.approx(2 * 0.6)
    # Task 001 runs Mon–Wed at half allocation; the milestone loads nobody.
    assert row.weeks[0].demand == pytest.approx(1.5)
    assert row.weeks[1].demand == pytest.approx(0.0)
    assert row.total_demand == pytest.approx(1.5)
    assert row.total_supply == pytest.approx(3.0)
    assert row.utilization_percent == 50          # 1.5 of the 3.0 person-days available
    assert row.peak_percent == 83                 # the week it is actually worked


def test_a_project_holiday_lowers_supply_and_demand_together(app, seeded):
    """Nobody works a holiday, so the 3-day task simply runs into the next week."""
    db.session.add(Holiday(project_id=seeded.id, date=MON_1, name="Open day"))
    db.session.commit()

    caps = build_capacity(seeded, Task.query.filter_by(project_id=seeded.id).all(),
                          TeamMember.query.filter_by(project_id=seeded.id).all(), weeks=2)
    row = caps.rows[0]
    # Mon 1 is a holiday: the task is worked Tue, Wed, then Mon 8 -> 2 days in week 1.
    assert row.weeks[0].demand == pytest.approx(1.0)
    assert row.weeks[1].demand == pytest.approx(0.5)
    assert row.total_demand == pytest.approx(1.5)      # still 3 working days at 50%
    assert row.weeks[0].supply == pytest.approx(2 * 0.6)   # Tue + Wed only
    # Week 2 has three working days minus the conference Monday.
    assert row.weeks[1].supply == pytest.approx(2 * 0.6)


def test_model_defaults_match_the_pre_capacity_behaviour(app, seeded):
    plain = TeamMember(project_id=seeded.id, name="Bob")
    lane = Lane.query.filter_by(project_id=seeded.id).first()
    unallocated = Task(project_id=seeded.id, code="003", lane_id=lane.id, name="Untouched",
                       start_date=MON_1, duration_days=5)
    db.session.add_all([plain, unallocated])
    db.session.commit()

    assert plain.capacity_percent == 100
    assert plain.capacity_fraction == 1.0
    assert unallocated.allocation_percent is None

    caps = build_capacity(seeded, Task.query.filter_by(project_id=seeded.id).all(),
                          TeamMember.query.filter_by(project_id=seeded.id).all(), weeks=2)
    # Bob has no work, and task 003 is unassigned: the unowned row picks it up.
    assert find_member_row(caps, "Bob").total_demand == pytest.approx(0.0)
    assert caps.unassigned_row.total_demand == pytest.approx(5.0)   # not started → all of it


OLD_PROJECTS = """
CREATE TABLE projects (
    id INTEGER NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    created_at DATETIME,
    updated_at DATETIME
)
"""

OLD_TEAM_MEMBERS = """
CREATE TABLE team_members (
    id INTEGER NOT NULL PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at DATETIME,
    CONSTRAINT uq_member_name UNIQUE (project_id, name)
)
"""

OLD_TASKS = """
CREATE TABLE tasks (
    id INTEGER NOT NULL PRIMARY KEY,
    project_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    lane_id INTEGER,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    duration_days INTEGER NOT NULL,
    responsible TEXT,
    predecessors TEXT,
    started BOOLEAN NOT NULL DEFAULT 0,
    done BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_task_code UNIQUE (project_id, code)
)
"""


def test_existing_installs_gain_the_capacity_columns(tmp_path):
    """Old databases upgrade on boot: capacity defaults to exactly the old behaviour."""
    import sqlite3

    db_path = tmp_path / "pert.db"
    conn = sqlite3.connect(db_path)
    for ddl in (OLD_PROJECTS, OLD_TEAM_MEMBERS, OLD_TASKS):
        conn.execute(ddl)
    conn.execute("INSERT INTO projects (id, name) VALUES (1, 'Legacy')")
    conn.execute("INSERT INTO team_members (id, project_id, name) VALUES (1, 1, 'Ada')")
    conn.execute(
        "INSERT INTO tasks (id, project_id, code, lane_id, name, start_date, duration_days, responsible)"
        " VALUES (1, 1, '001', 1, 'Legacy task', '2026-06-01', 5, 'Ada')"
    )
    conn.commit()
    conn.close()

    upgraded = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
    })

    with upgraded.app_context():
        columns = {r[1] for r in db.session.execute(db.text("PRAGMA table_info(team_members)"))}
        assert {"role", "capacity_percent", "cost_per_day"} <= columns
        task_columns = {r[1] for r in db.session.execute(db.text("PRAGMA table_info(tasks)"))}
        assert "allocation_percent" in task_columns
        assert {r[1] for r in db.session.execute(db.text("PRAGMA table_info(projects)"))} >= {
            "description", "baseline_set_at", "workdays",
        }
        tables = {r[0] for r in db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "member_absences" in tables

        member = db.session.get(TeamMember, 1)
        assert member.capacity_percent == 100
        assert member.cost_per_day is None
        assert db.session.get(Task, 1).allocation_percent is None

        # and the upgraded row still plans as it always did
        project_row = db.session.get(Project, 1)
        caps = build_capacity(project_row, list(project_row.tasks), list(project_row.members), weeks=1)
        assert caps.rows[0].weeks[0].demand == pytest.approx(5.0)
        assert caps.rows[0].weeks[0].supply == pytest.approx(5.0)


def test_absences_follow_the_resource_when_it_is_deleted(app, seeded):
    ada = TeamMember.query.filter_by(project_id=seeded.id, name="Ada").one()
    assert len(ada.absences) == 1
    assert ada.absences[0].label == "8 Jun 2026"
    db.session.delete(ada)
    db.session.commit()
    assert MemberAbsence.query.filter_by(project_id=seeded.id).count() == 0


def test_absences_follow_the_project(app, seeded):
    assert MemberAbsence.query.filter_by(project_id=seeded.id).count() == 1
    db.session.delete(seeded)
    db.session.commit()
    assert MemberAbsence.query.count() == 0
