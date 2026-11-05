"""Routes package exposing the 'main' Blueprint and re-exports for backwards compatibility."""

from .context import bp, task_table_render_kwargs, task_table_swap_response, overview_context
from .projects import (
    get_project_stats,
    index,
    create_project,
    rename_project,
    delete_project,
    project,
    project_overview_partial,
    update_project_settings,
    export_jira_csv,
    diagram_partial,
)
from .settings import (
    swap_lane_orders,
    manage_lanes,
    manage_members,
)
from .requirements import (
    requirements_partial_context,
    manage_requirements,
    requirements_list_partial,
)
from .raid import (
    raid_context,
    manage_raid,
)
from .tasks import (
    set_tasks_predecessors_helper,
    task_detail_context,
    create_task,
    update_task,
    set_tasks_predecessors,
    set_task_predecessors,
    delete_task,
    task_detail_empty,
    task_detail,
    update_task_detail,
)

from datetime import date

# Re-exports from services for backwards compatibility with tests and seed scripts:
from ..services.common import (
    fmt_date,
    parse_date,
    normalize_rich_text as _normalize_rich_text,
    parse_moscow as _parse_moscow,
    next_task_code,
    lanes_in_order,
    members_in_order,
    requirements_in_order,
    next_requirement_code,
    next_raid_code,
    parse_raid_kind as _parse_raid_kind,
    parse_raid_status as _parse_raid_status,
    parse_raid_level as _parse_raid_level,
)
from ..services.scheduler import (
    recalculate_project,
    validate_task,
    validate_predecessors_change as _validate_predecessors_change,
    project_date_stats,
    task_successors,
)

# Internal helper aliases for test compatibility
_task_table_render_kwargs = task_table_render_kwargs
_task_table_swap_response = task_table_swap_response
_overview_context = overview_context
_requirements_partial_context = requirements_partial_context
_raid_context = raid_context
_task_detail_context = task_detail_context
_set_tasks_predecessors = set_tasks_predecessors_helper
_swap_lane_orders = swap_lane_orders

__all__ = [
    "bp",
    "recalculate_project",
    "validate_task",
    "_validate_predecessors_change",
    "project_date_stats",
    "task_successors",
    "parse_date",
    "fmt_date",
    "_normalize_rich_text",
    "_parse_moscow",
    "next_task_code",
    "lanes_in_order",
    "members_in_order",
    "requirements_in_order",
    "next_requirement_code",
    "next_raid_code",
    "_parse_raid_kind",
    "_parse_raid_status",
    "_parse_raid_level",
    "get_project_stats",
    "_overview_context",
    "_requirements_partial_context",
    "_raid_context",
    "_task_detail_context",
    "_task_table_render_kwargs",
    "_task_table_swap_response",
    "_set_tasks_predecessors",
    "_swap_lane_orders",
]
