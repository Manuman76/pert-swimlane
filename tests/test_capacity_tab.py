"""P3: the Capacity tab - a week grid from derived data, plus its CSV.

Nothing here stores capacity: every test edits a task, a calendar or an absence and
then asks the tab what it now says.
"""

import csv
import io
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, TeamMember, MemberAbsence
from app.routes.capacity import (
    CAPACITY_WEEK_CHOICES,
    capacity_context,
    capacity_csv,
    capacity_query_args,
    month_bands,
    parse_capacity_mode,
    parse_capacity_weeks,
)

MON = date(2026, 6, 1)              # a Monday; W23 of 2026
WEEK = timedelta(weeks=1)


@pytest.fixture
def app():
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
def client(app):
    return app.test_client()


def make_project(name="Board", workdays="0,1,2,3,4"):
    proj = Project(name=name, workdays=workdays)
    db.session.add(proj)
    db.session.flush()
    lane = Lane(project_id=proj.id, name="Lane", sort_order=1)
    db.session.add(lane)
    db.session.commit()
    return proj


def member(project, name, **kwargs):
    row = TeamMember(project_id=project.id, name=name, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def task(project, lane, code, responsible=None, start=MON, duration=5, **kwargs):
    row = Task(project_id=project.id, lane_id=lane.id, code=code, name=f"Task {code}",
               responsible=responsible, start_date=start, duration_days=duration, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def grid(client, project, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"/projects/{project.id}/capacity"
    return client.get(f"{url}?{query}" if query else url)


def cells(html, name):
    """One member's row as a list of ``<td>`` tags."""
    row = re.search(rf'<tr class="capacity-row" id="capacity-row-\d+">\s*<th[^>]*>\s*<span class="fw-semibold">{re.escape(name)}</span>.*?</tr>',
                    html, re.S)
    assert row, f"no capacity row for {name}"
    return re.findall(r"<td .*?</td>", row.group(0), re.S)


def weeks_in(html):
    return re.findall(r'class="capacity-week-col[^"]*"', html)


def body(html):
    return re.sub(r"\s+", " ", html)


def booked(client, project, **params):
    """Render and collapse the whitespace so assertions can match whole tags."""
    return body(grid(client, project, **params).get_data(as_text=True))


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------

def test_the_tab_is_its_own_get_request(client):
    proj = make_project()
    member(proj, "Ann")
    response = grid(client, proj, weeks=8)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Load profile" in html
    assert "Ann" in html


def test_404_for_a_project_that_is_not_there(client):
    assert grid(client, make_project()).status_code == 200
    assert client.get("/projects/999999/capacity").status_code == 404


def test_each_member_is_a_row_and_the_team_and_the_homeless_are_rows_too(client):
    proj = make_project()
    ann = member(proj, "Ann")
    member(proj, "Bob")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann")
    task(proj, lane, "002", responsible=None)

    html = booked(client, proj, weeks=4)
    assert html.count('<td class="capacity-cell') == 4 * 4  # 2 members + unowned + total
    assert "Team total" in html
    assert "Unassigned" in html
    assert "work with no owner" in html


def test_an_over_loaded_week_is_red_a_number_and_a_shape(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)

    ann = cells(grid(client, proj, weeks=1).get_data(as_text=True), "Ann")
    assert 'class="capacity-cell cap-over"' in ann[0]
    assert "200%" in ann[0]
    assert "&#9650;" in ann[0]         # colour is never the only signal


def test_a_day_off_out_of_a_full_week_is_still_called_over(client):
    proj = make_project()
    ann = member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    db.session.add(MemberAbsence(project_id=proj.id, member_id=ann.id,
                                 start_date=MON, end_date=MON + WEEK))
    db.session.commit()

    ann = cells(grid(client, proj, weeks=1).get_data(as_text=True), "Ann")
    assert "cap-over" in ann[0]
    assert "5d booked of 0d available" in ann[0]
    assert ">\u2014<" in ann[0]          # no percentage of an empty week, no lie


def test_quiet_weeks_are_not_marked_over(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=1)

    html = grid(client, proj, weeks=2).get_data(as_text=True)
    ann = cells(html, "Ann")
    assert "cap-idle" in ann[1]
    assert "&#9650;" not in ann[1]
    assert html.count("cap-over") == 0


def test_the_week_is_named_and_dated_and_counted(client):
    proj = make_project()
    member(proj, "Ann")
    html = booked(client, proj, weeks=3, start="2026-06-01")
    assert ">W23<" in html
    assert '<span class="capacity-week-date">1 Jun</span>' in html
    assert "5 working days" in html


def test_a_month_spans_the_weeks_it_covers(client):
    proj = make_project()
    member(proj, "Ann")
    html = booked(client, proj, weeks=4, start="2026-06-01")
    assert '<th scope="col" class="capacity-month" colspan="4">Jun 2026</th>' in html

    html = booked(client, proj, weeks=8, start="2026-06-01")
    bands = re.findall(r'class="capacity-month" colspan="(\d+)">([^<]+)<', html)
    assert bands == [("5", "Jun 2026"), ("3", "Jul 2026")]


def test_the_unowned_row_shows_days_rather_than_a_share_of_nobody(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible=None, duration=2)

    row = re.search(r'<tr class="capacity-row capacity-row-unassigned">.*?</tr>',
                    booked(client, proj, weeks=1), re.S).group(0)
    assert "2d booked" in row
    assert "cap-idle" in row           # 2d against one hypothetical week


def test_names_on_tasks_that_are_not_in_the_team_are_listed(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Cleo", duration=3)

    html = booked(client, proj, weeks=2)
    assert "not in the team" in html
    assert "Cleo (3d)" in html
    # Nobody was invented to carry it: Cleo owns no row of her own.
    assert '<span class="fw-semibold">Cleo</span>' not in html


def test_markup_in_a_name_stays_markup(client):
    proj = make_project(name="Board")
    member(proj, "Ann <b>")
    html = grid(client, proj, weeks=1).get_data(as_text=True)
    assert "Ann &lt;b&gt;" in html
    assert "<b></b>" not in html


# ---------------------------------------------------------------------------
# The window and the mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("whole", None), ("WHOLE", None), ("auto", None),
    ("8", 8), ("13", 13), ("26", 26), ("12", 12),        # typed numbers are honoured
    ("0", 1), ("-4", 1), ("52", 26), ("999", 26),        # and clamped, not trusted
    ("soon", None), ("8.5", None),
])
def test_the_weeks_picker_accepts_picks_and_typed_numbers(raw, expected):
    assert parse_capacity_weeks(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    (None, "remaining"), ("", "remaining"), ("full", "full"),
    ("FULL", "full"), (" full ", "full"), ("as booked", "remaining"),
])
def test_the_mode_picker_defaults_to_what_is_left_to_do(raw, expected):
    assert parse_capacity_mode(raw) == expected


def test_the_number_of_weeks_on_the_toolbar_is_the_number_on_the_screen(client):
    proj = make_project()
    member(proj, "Ann")
    for weeks in (4, 8, 13, 26):
        assert len(weeks_in(grid(client, proj, weeks=weeks).get_data(as_text=True))) == weeks


def test_whole_project_is_the_plans_own_span_padded_to_a_useful_view(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", start=MON, duration=1)
    task(proj, lane, "002", responsible="Ann", start=MON + 3 * WEEK, duration=1)

    html = grid(client, proj, weeks="whole").get_data(as_text=True)
    # Two weeks of plan, but a two-column tab tells you nothing: eight is the floor.
    assert len(weeks_in(html)) == 8


def test_the_plan_running_past_the_window_is_admitted(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", start=MON, duration=1)
    task(proj, lane, "002", responsible="Ann", start=MON + 40 * WEEK, duration=1)

    html = booked(client, proj, weeks="whole")
    assert len(weeks_in(html)) == 26
    assert "the plan runs past these 26 weeks" in html


def test_a_starting_week_moves_the_whole_grid(client):
    proj = make_project()
    member(proj, "Ann")
    html = booked(client, proj, **{"start": "2026-06-22", "weeks": 2})
    assert ">W26<" in html and ">W27<" in html
    assert "2026-W25" not in html


def test_an_unparseable_start_is_ignored_rather_than_guessed(client):
    proj = make_project()
    member(proj, "Ann")
    assert len(weeks_in(grid(client, proj, start="June-ish", weeks=4).get_data(as_text=True))) == 4


def test_remaining_effort_lets_finished_work_go(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5, progress_percent=60)

    remaining = cells(grid(client, proj, weeks=1).get_data(as_text=True), "Ann")
    as_booked = cells(grid(client, proj, weeks=1, mode="full").get_data(as_text=True), "Ann")
    assert "2d booked" in remaining[0]      # 40% of a 5d task is what is left
    assert "5d booked" in as_booked[0]
    assert "40%" in remaining[0] and "100%" in as_booked[0]


def test_a_done_task_has_stopped_loading_its_owner(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5, progress_percent=100)

    ann = cells(grid(client, proj, weeks=1).get_data(as_text=True), "Ann")
    assert "0d booked of 5d available" in ann[0]


# ---------------------------------------------------------------------------
# The toolbar remembering, and the export agreeing
# ---------------------------------------------------------------------------

def test_the_toolbar_reopens_showing_what_was_on_the_screen(client):
    proj = make_project()
    member(proj, "Ann")
    html = grid(client, proj, **{"start": "2026-06-08", "weeks": 13, "mode": "full"}).get_data(as_text=True)
    assert 'value="2026-06-08"' in html
    assert '<option value="13" selected>13 weeks</option>' in html
    assert '<option value="full" selected>Full plan</option>' in html
    assert '<option value="whole" selected>' not in html


def test_the_default_toolbar_offers_the_whole_project(client):
    proj = make_project()
    member(proj, "Ann")
    html = grid(client, proj).get_data(as_text=True)
    assert '<option value="whole" selected>Whole project</option>' in html
    assert 'value=""' in html               # no start date = the plan's own start


def test_the_export_link_exports_the_window_on_the_screen(client):
    proj = make_project()
    member(proj, "Ann")
    html = grid(client, proj, **{"start": "2026-06-08", "weeks": 13, "mode": "full"}).get_data(as_text=True)
    href = re.search(r'href="([^"]*capacity/export\.csv[^"]*)"', html).group(1)
    assert "start=2026-06-08" in href and "weeks=13" in href and "mode=full" in href


def test_an_export_link_with_no_choices_at_all_is_still_an_export_link(client):
    proj = make_project()
    member(proj, "Ann")
    html = grid(client, proj).get_data(as_text=True)
    href = re.search(r'href="([^"]*capacity/export\.csv[^"]*)"', html).group(1)
    assert href.endswith("capacity/export.csv")


@pytest.mark.parametrize("kwargs, expected", [
    ({}, ""),
    ({"start": MON}, "start=2026-06-01"),
    ({"weeks": 8}, "weeks=8"),
    ({"mode": "full"}, "mode=full"),
    ({"mode": "remaining"}, ""),
    ({"start": MON, "weeks": 8, "mode": "full"}, "start=2026-06-01&weeks=8&mode=full"),
])
def test_the_query_string_holds_only_what_differs_from_the_default(kwargs, expected):
    assert capacity_query_args(**kwargs) == expected


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def export(client, project, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return client.get(f"/projects/{project.id}/capacity/export.csv" + (f"?{query}" if query else ""))


def test_the_export_is_one_row_per_person_per_week(client):
    proj = make_project()
    member(proj, "Ann")
    member(proj, "Bob")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)

    response = export(client, proj, weeks=3)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert rows[0]["Member"] == "Ann"
    assert len(rows) == (2 + 1) * 3 + 3        # two people + unowned + team, three weeks
    assert rows[0]["Week"] == "2026-W23"
    assert rows[0]["Week starting"] == "2026-06-01"
    assert rows[0]["Booked days"] == "5.0"
    assert rows[0]["Available days"] == "5.0"
    assert rows[0]["Tasks"] == "001"


def test_the_export_holds_numbers_and_not_presentation(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)

    rows = list(csv.DictReader(io.StringIO(export(client, proj, weeks=1).get_data(as_text=True))))
    ann = rows[0]
    assert ann["Utilization %"] == "200"       # a number a spreadsheet can average
    assert "%" not in ann["Utilization %"]
    assert ann["Over days"] == "5.0"
    assert ann["Capacity %"] == "100"


def test_a_week_nobody_can_work_is_left_blank_not_infinite(client):
    proj = make_project()
    ann = member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=2)
    db.session.add(MemberAbsence(project_id=proj.id, member_id=ann.id,
                                 start_date=MON, end_date=MON + WEEK))
    db.session.commit()

    rows = list(csv.DictReader(io.StringIO(export(client, proj, weeks=1).get_data(as_text=True))))
    assert rows[0]["Available days"] == "0.0"
    assert rows[0]["Utilization %"] == ""      # no division by zero in a spreadsheet


def test_the_export_includes_the_unowned_and_the_team(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible=None, duration=2)

    rows = list(csv.DictReader(io.StringIO(export(client, proj, weeks=1).get_data(as_text=True))))
    kinds = {row["Row type"] for row in rows}
    assert kinds == {"member", "unassigned", "team"}
    assert [row["Member"] for row in rows if row["Row type"] == "team"] == ["Team total"] * 1


def test_the_export_honours_the_window_on_the_screen(client):
    proj = make_project()
    member(proj, "Ann")
    for count in (3, 8, 13):
        rows = list(csv.DictReader(io.StringIO(export(client, proj, weeks=count).get_data(as_text=True))))
        weeks = len({row["Week"] for row in rows if row["Member"] == "Ann"})
        assert weeks == count


def test_the_file_is_named_after_the_project(client):
    proj = make_project(name="Q3 Launch")
    member(proj, "Ann")
    response = export(client, proj)
    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="Q3-Launch-capacity.csv"'
    )


def test_a_name_a_filesystem_would_not_forgive_is_escaped(client):
    proj = make_project(name="Board/Beta")
    member(proj, "Ann")
    assert export(client, proj).headers["Content-Disposition"] == (
        'attachment; filename="Board-Beta-capacity.csv"'
    )


def test_capacity_csv_works_straight_off_the_engine(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    capacity = capacity_context(proj, weeks=2)["capacity"]
    rows = list(csv.DictReader(io.StringIO(capacity_csv(capacity))))
    assert len(rows) == 2 * (1 + 1) + 2


# ---------------------------------------------------------------------------
# Empty and nearly-empty projects
# ---------------------------------------------------------------------------

def test_a_project_with_no_people_says_what_to_do_about_it(client):
    make_project()
    html = grid(client, Project.query.first()).get_data(as_text=True)
    assert "Set capacity in Settings" in html
    assert "capacity-week-col" not in html


def test_a_project_with_people_but_no_tasks_admits_it(client):
    proj = make_project()
    member(proj, "Ann")
    html = booked(client, proj, weeks=2)
    assert "Nothing dated yet" in html
    assert "cap-idle" in html            # the team is still shown, with room to spare


def test_a_cell_explains_whose_week_it_is_and_what_is_in_it(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)

    ann = cells(grid(client, proj, weeks=1).get_data(as_text=True), "Ann")
    assert 'title="Ann &middot; 10d booked of 5d available &middot; tasks 001, 002"' in ann[0]


def test_the_row_header_puts_worked_against_available(client):
    proj = make_project()
    member(proj, "Ann", capacity_percent=50)
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)

    row = body(re.search(r'<tr class="capacity-row" id="capacity-row-\d+">.*?</tr>',
                         grid(client, proj, weeks=1).get_data(as_text=True), re.S).group(0))
    assert "5d of 2.5d" in row          # booked first, available second
    assert "200% utilised" in row
    assert '<span class="badge text-bg-light border ms-1">50%</span>' in row


def test_the_unowned_row_counts_days_because_it_has_no_availability(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible=None, duration=3)

    row = re.search(r'<tr class="capacity-row capacity-row-unassigned">.*?</tr>',
                    grid(client, proj, weeks=1).get_data(as_text=True), re.S).group(0)
    assert '<span class="capacity-cell-value">3d</span>' in row
    assert "%" not in row               # a share of nobody is not a number to print


def test_the_summary_line_names_the_trouble(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    task(proj, lane, "003", responsible=None, duration=2)

    html = booked(client, proj, weeks=1, start="2026-06-01")
    assert "1 over capacity" in html
    assert "2d unowned" in html
    assert "1 member" in html            # singular, not "1 members"


def test_quiet_project_does_not_claim_a_problem(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=1)

    html = booked(client, proj, weeks=1, start="2026-06-01")
    assert "over capacity" not in html
    assert "unowned" not in html


def test_a_member_who_is_over_is_told_twice(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)

    row = re.search(r'<tr class="capacity-row" id="capacity-row-\d+">.*?</tr>',
                    grid(client, proj, weeks=1, start="2026-06-01").get_data(as_text=True), re.S).group(0)
    assert "1 week over" in row
    assert "from 01 Jun 2026" in row     # where it starts, in the tooltip
    assert "person-days too much" in row


def test_the_badge_counts_the_weeks_it_found(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    # Two full weeks of work in each of three weeks: over, three times running.
    for week in range(3):
        for half in range(2):
            task(proj, lane, f"{week}{half}", responsible="Ann", duration=5,
                 start=MON + week * WEEK)

    row = re.search(r'<tr class="capacity-row" id="capacity-row-\d+">.*?</tr>',
                    grid(client, proj, weeks=3, start="2026-06-01").get_data(as_text=True), re.S).group(0)
    assert "3 weeks over" in row
    assert "2 weeks over" not in row


def test_the_team_total_row_shows_a_team_too_big_for_its_weeks(client):
    proj = make_project()
    member(proj, "Ann")
    member(proj, "Bob")
    lane = proj.lanes[0]
    for code in ("001", "002", "003"):
        task(proj, lane, code, responsible="Ann" if code != "003" else "Bob", duration=5)
    task(proj, lane, "004", responsible="Bob", duration=5)

    row = re.search(r'<tr class="capacity-row capacity-row-total">.*?</tr>',
                    grid(client, proj, weeks=1, start="2026-06-01").get_data(as_text=True), re.S).group(0)
    assert "cap-over" in row
    assert "200%" in row
    assert "&#9650;" in row


def test_the_load_profile_is_drawn_for_people_who_have_work(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)

    html = grid(client, proj, weeks=1).get_data(as_text=True)
    bar = re.search(r'<div class="capacity-bar-row">.*?</div>\s*</div>', html, re.S).group(0)
    assert "capacity-bar-tick" in bar          # the mark for a full week
    assert 'style="width: 100.0%"' in bar      # 200% on a scale that stops at 150%
    assert ">200%</span>" in bar


def test_the_load_profile_scales_a_normal_week(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)

    html = grid(client, proj, weeks=1).get_data(as_text=True)
    assert 'style="width: 66.7%"' in html      # 100% of a week on a 150% scale


# ---------------------------------------------------------------------------
# The tab in the page, and who tells it to redraw
# ---------------------------------------------------------------------------

def project_page(client, project):
    return client.get(f"/projects/{project.id}").get_data(as_text=True)


def test_the_capacity_tab_sits_between_raid_and_settings(client):
    html = project_page(client, make_project())
    order = [html.index(f'id="{name}-tab"') for name in ("raid", "capacity", "settings")]
    assert order == sorted(order)
    assert '>Capacity</button>' in html


def test_the_pane_fetches_its_grid_and_listens_for_every_write(client):
    html = project_page(client, make_project())
    loader = re.search(r'<div id="capacity-loader"[^>]*>', html).group(0)
    assert '/capacity' in loader
    assert 'hx-trigger="load, refreshCapacity from:body"' in loader


def test_the_capacity_tab_is_a_place_you_can_link_to(client):
    html = project_page(client, make_project())
    assert "'#capacity': '#capacity-tab'" in html
    assert re.search(r"event\.target\.id === 'capacity-tab'\s*\n?\s*\? '#capacity'", html)


def test_a_new_task_asks_the_tab_to_redraw(client):
    proj = make_project()
    lane = proj.lanes[0]
    response = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": lane.id},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "refreshCapacity" in response.headers["HX-Trigger-After-Swap"]


def test_a_deleted_task_asks_the_tab_to_redraw(client):
    proj = make_project()
    lane = proj.lanes[0]
    row = task(proj, lane, "001", responsible="Ann", duration=2)
    response = client.post(f"/tasks/{row.id}/delete", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "refreshCapacity" in response.headers["HX-Trigger-After-Swap"]


def test_a_rejected_task_edit_asks_for_nothing(client):
    proj = make_project()
    lane = proj.lanes[0]
    row = task(proj, lane, "001", responsible="Ann", duration=2)
    response = client.post(f"/tasks/{row.id}/predecessors",
                           data={"predecessors": "001, 999"}, headers={"HX-Request": "true"})
    assert response.status_code == 422
    assert "refreshCapacity" not in response.headers.get("HX-Trigger-After-Swap", "")


def test_changing_a_deadline_is_not_the_only_way_to_change_a_week(client):
    proj = make_project()
    ann = member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=2)

    posts = [
        (f"/projects/{proj.id}/absences", {"action": "add", "partial": "settings",
                                           "member_id": ann.id, "start_date": "2026-06-01"}),
        (f"/projects/{proj.id}/members", {"action": "capacity", "partial": "settings",
                                          "member_id": ann.id, "capacity_percent": "50"}),
        (f"/projects/{proj.id}/holidays", {"action": "add", "partial": "settings",
                                           "date": "2026-06-02", "name": "Bank holiday"}),
        (f"/projects/{proj.id}/workdays", {"partial": "settings", "weekday": ["1", "2", "3", "4", "5"]}),
    ]
    for url, data in posts:
        response = client.post(url, data=data, headers={"HX-Request": "true"})
        assert response.status_code == 200, url
        assert "refreshCapacity" in response.headers["HX-Trigger-After-Swap"], url


def test_an_edit_that_was_refused_asks_for_nothing(client):
    proj = make_project()
    ann = member(proj, "Ann")
    response = client.post(f"/projects/{proj.id}/absences",
                           data={"action": "add", "partial": "settings",
                                 "member_id": ann.id, "start_date": "2026-06-05",
                                 "end_date": "2026-06-01"},
                           headers={"HX-Request": "true"})
    assert response.status_code == 400
    assert "refreshCapacity" not in response.headers.get("HX-Trigger-After-Swap", "")


def test_the_team_list_refreshes_what_it_just_changed(client):
    proj = make_project()
    ann = member(proj, "Ann")
    post = lambda data: client.post(f"/projects/{proj.id}/members",
                                    data={**data, "member_id": ann.id, "partial": "settings"},
                                    headers={"HX-Request": "true"}).headers["HX-Trigger-After-Swap"]

    both = post({"action": "rename", "name": "Ann-Marie"})
    assert "refreshCapacity" in both
    assert "refreshGrid" in both                 # every task's owner changed

    both = post({"action": "delete"})
    assert "refreshCapacity" in both
    assert "refreshGrid" in both                 # an owner vanished from the grid

    ann = member(proj, "Zoe")
    capacity_only = post({"action": "capacity", "capacity_percent": "60"})
    assert "refreshCapacity" in capacity_only
    assert "refreshGrid" not in capacity_only    # no percent appears in the grid


# ---------------------------------------------------------------------------
# The context helper
# ---------------------------------------------------------------------------

def test_the_context_hands_the_template_the_engine_and_the_toolbar(client):
    proj = make_project()
    member(proj, "Ann")
    ctx = capacity_context(proj, start=MON, weeks=8, mode="full", today=MON)
    assert ctx["capacity"].mode == "full"
    assert ctx["capacity_weeks"] == 8
    assert ctx["capacity_start"] == MON
    assert ctx["capacity_mode"] == "full"
    assert ctx["capacity_window_note"] == "8 weeks from 01 Jun 2026 to 20 Jul 2026"
    assert ctx["capacity_mode_choices"][0][0] == "remaining"
    assert ctx["fmt_capacity_days"](2.5) == "2.5d"


def test_the_context_marks_where_we_are_in_the_plan(client):
    proj = make_project()
    member(proj, "Ann")
    ctx = capacity_context(proj, start=MON, weeks=4, today=date(2026, 6, 10))
    assert ctx["capacity"].weeks[0].is_past is True
    assert ctx["capacity"].weeks[1].is_current is True
    assert ctx["capacity"].weeks[3].is_future is True


def test_the_context_counts_a_week_of_the_team_not_of_the_plan(client):
    proj = make_project()
    member(proj, "Ann")
    member(proj, "Bob")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Bob", duration=5)

    totals = capacity_context(proj, weeks=1, today=MON)["capacity"].totals
    assert totals[0].demand_str == "10d"
    assert totals[0].supply_str == "10d"
    assert totals[0].percent_str == "100%"


def test_month_bands_group_weeks_that_share_a_month():
    columns = [SimpleNamespace(month_label="Jun"), SimpleNamespace(month_label="Jun"),
               SimpleNamespace(month_label="Jul"), SimpleNamespace(month_label="Jul"),
               SimpleNamespace(month_label="Aug")]
    assert month_bands(columns) == [("Jun", 2), ("Jul", 2), ("Aug", 1)]


def test_one_week_says_one_week(client):
    proj = make_project()
    member(proj, "Ann")
    ctx = capacity_context(proj, start=MON, weeks=1, today=MON)
    assert ctx["capacity_window_note"] == "1 week from 01 Jun 2026"
    assert "1 week from 01 Jun 2026" in grid(client, proj, weeks=1, start="2026-06-01").get_data(as_text=True)


def test_the_week_the_reader_is_standing_in_is_marked(app, client):
    proj = make_project()
    member(proj, "Ann")
    from flask import render_template
    with app.test_request_context():
        html = render_template(
            "partials/capacity.html", project=proj,
            **capacity_context(proj, start=MON, weeks=4, today=date(2026, 6, 10)),
        )
    assert "capacity-week-past" in html       # W23 is behind us
    assert "capacity-week-current" in html    # W24 is this one
    assert html.count("capacity-week-current") == 1


def test_month_bands_of_nothing_is_nothing():
    assert month_bands([]) == []
