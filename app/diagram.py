from collections import deque
from datetime import timedelta

CARD_W = 168
CARD_H = 108  # tall enough for a two-line title
MILESTONE_W = 52
MILESTONE_H = 52
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


def _layout_node(task, slot_x, slot_y):
    """Return pixel box (x, y, w, h) for a task card or centered milestone diamond."""
    if task.is_milestone:
        w, h = MILESTONE_W, MILESTONE_H
        x = slot_x + (CARD_W - w) / 2
        y = slot_y + (CARD_H - h) / 2
    else:
        w, h = CARD_W, CARD_H
        x, y = slot_x, slot_y
    return x, y, w, h


def build_diagram(lanes, tasks, members=None, collapsed_lanes=None):
    """Render swimlane diagram as HTML+SVG partial."""
    from flask import render_template

    collapsed_set = set(collapsed_lanes) if collapsed_lanes else set()
    lanes = sorted(lanes, key=lambda l: l.sort_order)
    lane_ids = [l.id for l in lanes]
    lane_index = {l.id: i for i, l in enumerate(lanes)}
    project_id = getattr(lanes[0], "project_id", None) if lanes else (getattr(tasks[0], "project_id", None) if tasks else None)

    # Group tasks by lane, sort by start_date then code
    tasks_by_lane = {l.id: [] for l in lanes}
    for t in tasks:
        if t.lane_id in tasks_by_lane:
            tasks_by_lane[t.lane_id].append(t)
    for lid in tasks_by_lane:
        tasks_by_lane[lid].sort(key=lambda t: (t.start_date, t.code))

    lane_collapsed = {l.id: (l.id in collapsed_set) for l in lanes}
    lane_task_counts = {l.id: len(tasks_by_lane[l.id]) for l in lanes}

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

    # Build positions. Within a lane, tasks that share the same predecessor
    # set (including none) are siblings: same column, stacked vertically.
    positions = {}  # task.code -> {x, y, lane_idx, idx_in_lane, col, row}
    lane_tops = {}  # lane.id -> top offset (px)
    lane_heights = {}  # lane.id -> band height (px)
    y_cursor = 0
    for lane in lanes:
        li = lane_index[lane.id]
        if lane_collapsed[lane.id]:
            lane_h = 42
            lane_tops[lane.id] = y_cursor
            lane_heights[lane.id] = lane_h
            y_cursor += lane_h
            continue

        col = None
        rows_in_col = {}      # col -> number of stacked cards
        col_for_preds = {}    # predecessor signature (or () for roots) -> col
        placements = []
        for idx, task in enumerate(tasks_by_lane[lane.id]):
            preds = tuple(sorted(
                p for p in task.predecessor_codes() if p in all_tasks_map
            ))
            group_key = preds if preds else ()
            if group_key in col_for_preds:
                c = col_for_preds[group_key]
            else:
                base = min_col[task.code]
                c = base
                col_for_preds[group_key] = c
                col = c if col is None else max(col, c)
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
            slot_x = LANE_LABEL_W + GAP_X + c * (CARD_W + GAP_X)
            slot_y = y_cursor + top_pad + r * (CARD_H + V_GAP)
            x, y, w, h = _layout_node(task, slot_x, slot_y)
            positions[task.code] = {
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "lane_idx": li,
                "idx_in_lane": idx,
                "col": c,
                "row": r,
                "task": task,
            }
        y_cursor += lane_h

    # Canvas size
    max_width = LANE_LABEL_W + RIGHT_MARGIN + 320
    for p in positions.values():
        max_width = max(max_width, p["x"] + p["w"] + RIGHT_MARGIN)
    canvas_h = max(y_cursor + 20, 240)

    # Status colors per task
    task_status = {t.code: STATUS_STYLES[t.status] for t in tasks}

    # Distinct members for the responsible filter
    if members is None:
        assignee_names = sorted({t.responsible for t in tasks if t.responsible})
        member_list = [{"name": r} for r in assignee_names]
    else:
        member_list = members

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

    def v_seg_clash(x, y1, y2, skip=frozenset()):
        """True if a vertical segment at x between y1 and y2 crosses a card."""
        lo, hi = min(y1, y2), max(y1, y2)
        for code, p in positions.items():
            if code in skip:
                continue
            if not (p["x"] < x < p["x"] + p["w"]):
                continue
            if lo < p["y"] + p["h"] and hi > p["y"]:
                return True
        return False

    def h_seg_clash(y, x1, x2, skip=frozenset()):
        """True if a horizontal segment at y between x1 and x2 crosses a card."""
        lo, hi = min(x1, x2), max(x1, x2)
        for code, p in positions.items():
            if code in skip:
                continue
            if not (p["y"] < y < p["y"] + p["h"]):
                continue
            if lo < p["x"] + p["w"] and hi > p["x"]:
                return True
        return False

    def route_forward_elbow(ax1, ay1, ax2, ay2, src_code, dst_code):
        """Right stub -> vertical -> left stub, avoiding card interiors."""
        skip = frozenset({src_code, dst_code})
        stub = 12
        candidates = []
        gap_lo = ax1 + stub
        gap_hi = ax2 - stub
        if gap_hi > gap_lo:
            candidates.append((gap_lo + gap_hi) / 2)
        candidates.append(gap_lo)
        candidates.append(gap_hi)
        for step in range(8, 160, 8):
            candidates.append(gap_lo + step)
            candidates.append(gap_hi - step)
        seen = set()
        for mid_x in candidates:
            mid_x = round(mid_x, 2)
            if mid_x in seen:
                continue
            seen.add(mid_x)
            if h_seg_clash(ay1, ax1, mid_x, skip):
                continue
            if v_seg_clash(mid_x, ay1, ay2, skip):
                continue
            if h_seg_clash(ay2, mid_x, ax2, skip):
                continue
            return (
                f"M {ax1} {ay1} L {mid_x} {ay1} L {mid_x} {ay2} L {ax2} {ay2}"
            )
        mid_x = round(gap_lo + get_v_offset(gap_lo, ay1, ay2), 2)
        return f"M {ax1} {ay1} L {mid_x} {ay1} L {mid_x} {ay2} L {ax2} {ay2}"

    for src_code, dst_code in edges:
        if src_code not in positions or dst_code not in positions:
            continue
        src = positions[src_code]
        dst = positions[dst_code]
        sx, sy = src["x"], src["y"]
        dx, dy = dst["x"], dst["y"]
        sw, sh = src["w"], src["h"]
        dw, dh = dst["w"], dst["h"]
        s_lane = src["lane_idx"]
        d_lane = dst["lane_idx"]

        backward = (d_lane < s_lane) or (d_lane == s_lane and dx + dw <= sx)
        backward_direct = d_lane < s_lane and dx > sx + sw

        # Anchor points
        if backward_direct:
            ax1 = sx + sw
            ay1 = sy + sh / 2
            ax2 = dx
            ay2 = dy + dh / 2
        elif backward:
            ax1 = sx + sw / 2
            ay1 = sy + sh
            ax2 = dx + dw / 2
            ay2 = dy
        else:
            ax1 = sx + sw
            ay1 = sy + sh / 2
            ax2 = dx
            ay2 = dy + dh / 2

        path = ""
        marker = "url(#arrowhead)"
        stroke = "#444"
        stroke_dash = ""

        if not backward and s_lane == d_lane and ay1 == ay2:
            skip = frozenset({src_code, dst_code})
            if not h_seg_clash(ay1, ax1, ax2, skip):
                path = f"M {ax1} {ay1} L {ax2} {ay2}"
            else:
                path = route_forward_elbow(ax1, ay1, ax2, ay2, src_code, dst_code)
        elif not backward and s_lane == d_lane:
            path = route_forward_elbow(ax1, ay1, ax2, ay2, src_code, dst_code)
        elif not backward and d_lane > s_lane:
            path = route_forward_elbow(ax1, ay1, ax2, ay2, src_code, dst_code)
        elif backward_direct:
            # Destination is upper-right: a direct elbow (right -> up -> left) is
            # clean enough to render like any normal dependency.
            stub = 12
            gap = dx - (sx + sw)
            cands = []
            if gap >= 2 * stub:
                cands.append(sx + sw + gap / 2)
            if gap > stub:
                cands.append(sx + sw + stub)
                cands.append(dx - stub)
            if not cands:
                cands.append((sx + sw + dx) / 2)
            mid_x = None
            for cand in cands:
                if not v_seg_clash(cand, ay1, ay2, frozenset({src_code, dst_code})):
                    mid_x = cand
                    break
            if mid_x is None:
                mid_x = cands[-1] + get_v_offset(cands[-1], ay1, ay2)
            path = f"M {ax1} {ay1} L {mid_x} {ay1} L {mid_x} {ay2} L {ax2} {ay2}"
        else:
            # Backward: task sits left/above; the route has to loop around the
            # outer right margin and enter the destination from its top. The red
            # dashed styling flags it as an out-of-order dependency.
            stroke = "#c0392b"
            stroke_dash = "4,3"
            marker = "url(#arrowhead-red)"
            if d_lane < s_lane:
                # up and left: go right, up, left
                rx = max_width - 20
                path = f"M {ax1} {ay1} L {rx} {ay1} L {rx} {ay2} L {ax2} {ay2}"
            else:
                # same lane backward: go down under cards, back left, up
                under_y = sy + sh + 20 + get_v_offset(sx, sy, sy + sh + 20)
                back_x = max(ax2 - 10, 0)
                path = f"M {ax1} {ay1} L {ax1 + 10} {ay1} L {ax1 + 10} {under_y} L {back_x} {under_y} L {back_x} {ay2} L {ax2} {ay2}"

        arrows.append({
            "path": path,
            "stroke": stroke,
            "stroke_dash": stroke_dash,
            "marker": marker,
            "src": src_code,
            "dst": dst_code,
            "src_lane_id": src["task"].lane_id,
            "dst_lane_id": dst["task"].lane_id,
        })

    return render_template(
        "partials/diagram.html",
        project_id=project_id,
        lanes=lanes,
        tasks=tasks,
        tasks_by_lane=tasks_by_lane,
        positions=positions,
        canvas_w=max_width,
        canvas_h=canvas_h,
        arrows=arrows,
        lane_tops=lane_tops,
        lane_heights=lane_heights,
        lane_collapsed=lane_collapsed,
        lane_task_counts=lane_task_counts,
        lane_h=LANE_H,
        lane_label_w=LANE_LABEL_W,
        card_w=CARD_W,
        card_h=CARD_H,
        milestone_w=MILESTONE_W,
        milestone_h=MILESTONE_H,
        gap_x=GAP_X,
        task_status=task_status,
        status_styles=STATUS_STYLES,
        members=member_list,
    )
