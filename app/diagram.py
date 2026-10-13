from collections import deque
from datetime import timedelta

CARD_W = 168
CARD_H = 108  # tall enough for a two-line title
GAP_X = 24
V_GAP = 16
LANE_H = 140
LANE_GAP = 0
LANE_LABEL_W = 120
HEADER_H = 36
RIGHT_MARGIN = 40
# Vertical breathing room above/below the cards inside a lane.
V_PAD = (LANE_H - CARD_H) // 2

# Status styling: each of the three task states gets its own color.
STATUS_STYLES = {
    "not_started": {"border": "#6c757d", "bar": "#6c757d", "label": "Not started", "icon": "\u25cb"},
    "started": {"border": "#f39c12", "bar": "#f39c12", "label": "Started", "icon": "\u25d1"},
    "done": {"border": "#27ae60", "bar": "#27ae60", "label": "Done", "icon": "\u2713"},
}


def build_diagram(lanes, tasks):
    """Render swimlane diagram as HTML+SVG partial."""
    from flask import render_template

    lanes = sorted(lanes, key=lambda l: l.sort_order)
    lane_ids = [l.id for l in lanes]
    lane_index = {l.id: i for i, l in enumerate(lanes)}

    # Group tasks by lane, sort by start_date then code
    tasks_by_lane = {l.id: [] for l in lanes}
    for t in tasks:
        if t.lane_id in tasks_by_lane:
            tasks_by_lane[t.lane_id].append(t)
    for lid in tasks_by_lane:
        tasks_by_lane[lid].sort(key=lambda t: (t.start_date, t.code))

    # All task codes available in this project
    all_tasks_map = {t.code: t for t in tasks}

    # Flow columns: every task must sit to the right of all its predecessors.
    # (Topological order; Kahn's algorithm. Cycles can't exist in saved data,
    # but any orphaned tasks simply stay at column 0 if they do.)
    dependents = {t.code: [] for t in tasks}
    in_deg = {}
    for t in tasks:
        preds = [p for p in t.predecessor_codes() if p in all_tasks_map]
        in_deg[t.code] = len(preds)
        for p in preds:
            dependents[p].append(t.code)
    min_col = {t.code: 0 for t in tasks}
    queue = deque(c for c, n in in_deg.items() if n == 0)
    while queue:
        c = queue.popleft()
        for d in dependents[c]:
            min_col[d] = max(min_col[d], min_col[c] + 1)
            in_deg[d] -= 1
            if in_deg[d] == 0:
                queue.append(d)

    # Build positions. Within a lane, tasks that share the same (non-empty)
    # predecessor set are siblings: they occupy the same column and stack
    # vertically. This keeps their shared incoming arrow from crossing the
    # other sibling's card.
    positions = {}  # task.code -> {x, y, lane_idx, idx_in_lane, col, row}
    lane_tops = {}  # lane.id -> top offset (px)
    lane_heights = {}  # lane.id -> band height (px)
    y_cursor = 0
    for lane in lanes:
        li = lane_index[lane.id]
        col = None
        rows_in_col = {}      # col -> number of stacked cards
        col_for_preds = {}    # predecessor signature -> col
        placements = []
        for idx, task in enumerate(tasks_by_lane[lane.id]):
            preds = tuple(sorted(
                p for p in task.predecessor_codes() if p in all_tasks_map
            ))
            if preds and preds in col_for_preds:
                c = col_for_preds[preds]
            else:
                base = min_col[task.code]
                c = base if col is None else max(base, col + 1)
                col = c
                if preds:
                    col_for_preds[preds] = c
            r = rows_in_col.get(c, 0)
            rows_in_col[c] = r + 1
            placements.append((task, idx, c, r))

        max_rows = max(rows_in_col.values()) if rows_in_col else 1
        content_h = max_rows * CARD_H + (max_rows - 1) * V_GAP
        lane_h = content_h + 2 * V_PAD
        top_pad = (lane_h - content_h) / 2
        lane_tops[lane.id] = y_cursor
        lane_heights[lane.id] = lane_h

        for task, idx, c, r in placements:
            x = LANE_LABEL_W + GAP_X + c * (CARD_W + GAP_X)
            y = y_cursor + top_pad + r * (CARD_H + V_GAP)
            positions[task.code] = {
                "x": x,
                "y": y,
                "lane_idx": li,
                "idx_in_lane": idx,
                "col": c,
                "row": r,
                "task": task,
            }
        y_cursor += lane_h

    # Canvas size
    max_width = LANE_LABEL_W + RIGHT_MARGIN
    for p in positions.values():
        max_width = max(max_width, p["x"] + CARD_W + RIGHT_MARGIN)
    canvas_h = y_cursor + 20

    # Status colors per task
    task_status = {t.code: STATUS_STYLES[t.status] for t in tasks}

    # Build arrows
    arrows = []

    # Collect dependency edges
    edges = []
    for task in tasks:
        for pcode in task.predecessor_codes():
            if pcode in all_tasks_map:
                edges.append((pcode, task.code))

    # Offset tracking for overlapping vertical segments
    v_offsets = {}  # (x, y1, y2) -> next offset

    def get_v_offset(x, y1, y2):
        key = (round(x), round(min(y1, y2)), round(max(y1, y2)))
        off = v_offsets.get(key, 0)
        v_offsets[key] = off + 8
        return off

    for src_code, dst_code in edges:
        if src_code not in positions or dst_code not in positions:
            continue
        src = positions[src_code]
        dst = positions[dst_code]
        sx, sy = src["x"], src["y"]
        dx, dy = dst["x"], dst["y"]
        s_lane = src["lane_idx"]
        d_lane = dst["lane_idx"]

        backward = (d_lane < s_lane) or (d_lane == s_lane and dx < sx + CARD_W)

        # Anchor points
        if backward:
            # leave from bottom, enter from top
            ax1 = sx + CARD_W / 2
            ay1 = sy + CARD_H
            ax2 = dx + CARD_W / 2
            ay2 = dy
        else:
            # normal: leave right, enter left
            ax1 = sx + CARD_W
            ay1 = sy + CARD_H / 2
            ax2 = dx
            ay2 = dy + CARD_H / 2

        path = ""
        marker = "url(#arrowhead)"
        stroke = "#444"
        stroke_dash = ""

        if not backward and s_lane == d_lane and ay1 == ay2:
            # Same lane, same row, forward: horizontal straight
            path = f"M {ax1} {ay1} L {ax2} {ay2}"
        elif not backward and s_lane == d_lane:
            # Same lane but stacked rows: right stub -> vertical -> left edge
            stub = 12
            path = f"M {ax1} {ay1} L {ax1 + stub} {ay1} L {ax1 + stub} {ay2} L {ax2} {ay2}"
        elif not backward and d_lane > s_lane:
            # Downward: right stub -> vertical -> left stub
            stub = 12
            candidates = []
            if dx > sx + CARD_W:
                candidates.append(sx + CARD_W + (dx - (sx + CARD_W)) / 2)
            candidates.append(ax1 + stub)
            # Pick a vertical x that does not pass through cards in the lanes in between.
            mid_x = None
            for cand in candidates:
                clash = any(
                    s_lane < p["lane_idx"] < d_lane and p["x"] < cand < p["x"] + CARD_W
                    for p in positions.values()
                )
                if not clash:
                    mid_x = cand
                    break
            if mid_x is None:
                mid_x = candidates[-1] + get_v_offset(candidates[-1], ay1, ay2)
            path = f"M {ax1} {ay1} L {mid_x} {ay1} L {mid_x} {ay2} L {ax2} {ay2}"
        else:
            # Backward: task sits left/above; route around the outer right margin
            # and enter the destination from its top.
            stroke = "#c0392b"
            stroke_dash = "4,3"
            marker = "url(#arrowhead-red)"
            if d_lane < s_lane:
                # up and left: go right, up, left
                rx = max_width - 20
                path = f"M {ax1} {ay1} L {rx} {ay1} L {rx} {ay2} L {ax2} {ay2}"
            else:
                # same lane backward: go down under cards, back left, up
                under_y = sy + CARD_H + 20 + get_v_offset(sx, sy, sy + CARD_H + 20)
                back_x = max(ax2 - 10, 0)
                path = f"M {ax1} {ay1} L {ax1 + 10} {ay1} L {ax1 + 10} {under_y} L {back_x} {under_y} L {back_x} {ay2} L {ax2} {ay2}"

        arrows.append({
            "path": path,
            "stroke": stroke,
            "stroke_dash": stroke_dash,
            "marker": marker,
        })

    return render_template(
        "partials/diagram.html",
        lanes=lanes,
        tasks_by_lane=tasks_by_lane,
        positions=positions,
        canvas_w=max_width,
        canvas_h=canvas_h,
        arrows=arrows,
        lane_tops=lane_tops,
        lane_heights=lane_heights,
        lane_h=LANE_H,
        lane_label_w=LANE_LABEL_W,
        card_w=CARD_W,
        card_h=CARD_H,
        gap_x=GAP_X,
        task_status=task_status,
        status_styles=STATUS_STYLES,
    )
