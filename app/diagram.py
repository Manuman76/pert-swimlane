from collections import deque
from datetime import timedelta
import heapq

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
ROUTE_CLEARANCE = 6
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

    # Collect dependency edges
    edges = []
    for task in tasks:
        for pcode in task.predecessor_codes():
            if pcode in all_tasks_map:
                edges.append((pcode, task.code))

    # Route on a visibility grid around inflated card bounds. This gives
    # arrows a small clearance and, unlike a fixed elbow, can route around
    # intermediate cards even when source and destination share a row.
    obstacles = {}
    x_coords = {0, max_width}
    y_coords = {0, canvas_h}
    for code, pos in positions.items():
        left = pos["x"] - ROUTE_CLEARANCE
        right = pos["x"] + pos["w"] + ROUTE_CLEARANCE
        top = pos["y"] - ROUTE_CLEARANCE
        bottom = pos["y"] + pos["h"] + ROUTE_CLEARANCE
        obstacles[code] = (left, top, right, bottom)
        x_coords.update((left, pos["x"], pos["x"] + pos["w"] / 2,
                         pos["x"] + pos["w"], right))
        y_coords.update((top, pos["y"], pos["y"] + pos["h"] / 2,
                         pos["y"] + pos["h"], bottom))

    x_coords = sorted(x_coords)
    y_coords = sorted(y_coords)

    def point_blocked(x, y):
        return any(
            left < x < right and top < y < bottom
            for left, top, right, bottom in obstacles.values()
        )

    def segment_clear(x1, y1, x2, y2, ignored_codes=frozenset()):
        for code, (left, top, right, bottom) in obstacles.items():
            if code in ignored_codes:
                continue
            if x1 == x2:
                if left < x1 < right and min(y1, y2) < bottom and max(y1, y2) > top:
                    return False
            elif y1 == y2:
                if top < y1 < bottom and min(x1, x2) < right and max(x1, x2) > left:
                    return False
        return True

    # The visibility graph is shared by all edges in this diagram render.
    graph = {}
    rows = {}
    columns = {}
    for x in x_coords:
        for y in y_coords:
            if point_blocked(x, y):
                continue
            node = (x, y)
            graph[node] = []
            rows.setdefault(y, []).append(node)
            columns.setdefault(x, []).append(node)

    def connect_line(nodes):
        nodes.sort()
        for first, second in zip(nodes, nodes[1:]):
            x1, y1 = first
            x2, y2 = second
            if segment_clear(x1, y1, x2, y2):
                distance = abs(x2 - x1) + abs(y2 - y1)
                direction = "h" if y1 == y2 else "v"
                graph[first].append((second, distance, direction))
                graph[second].append((first, distance, direction))

    for row in rows.values():
        connect_line(row)
    for column in columns.values():
        connect_line(column)

    def find_grid_path(start, end):
        if start not in graph or end not in graph:
            return None
        start_state = (start, None)
        costs = {start_state: 0}
        previous = {}
        pending = [(abs(start[0] - end[0]) + abs(start[1] - end[1]),
                    0, start, None)]
        final_state = None
        while pending:
            _estimate, cost, node, previous_direction = heapq.heappop(pending)
            state = (node, previous_direction)
            if cost != costs.get(state):
                continue
            if node == end:
                final_state = state
                break
            for neighbor, distance, direction in graph[node]:
                next_cost = cost + distance
                if previous_direction and previous_direction != direction:
                    next_cost += 24
                next_state = (neighbor, direction)
                if next_cost >= costs.get(next_state, float("inf")):
                    continue
                costs[next_state] = next_cost
                previous[next_state] = state
                estimate = next_cost + abs(neighbor[0] - end[0]) + abs(neighbor[1] - end[1])
                heapq.heappush(
                    pending, (estimate, next_cost, neighbor, direction)
                )
        if final_state is None:
            return None
        path = []
        state = final_state
        while state != start_state:
            path.append(state[0])
            state = previous[state]
        path.append(start)
        return list(reversed(path))

    def side_points(pos, side):
        x, y, w, h = pos["x"], pos["y"], pos["w"], pos["h"]
        if side == "right":
            return (x + w, y + h / 2), (x + w + ROUTE_CLEARANCE, y + h / 2)
        if side == "left":
            return (x, y + h / 2), (x - ROUTE_CLEARANCE, y + h / 2)
        if side == "bottom":
            return (x + w / 2, y + h), (x + w / 2, y + h + ROUTE_CLEARANCE)
        return (x + w / 2, y), (x + w / 2, y - ROUTE_CLEARANCE)

    def route_around_cards(src_code, dst_code, preferred_source, preferred_target):
        sides = ("right", "left", "bottom", "top")
        side_pairs = [(preferred_source, preferred_target)]
        alternatives = [
            (source_side, target_side)
            for source_side in sides
            for target_side in sides
            if (source_side, target_side) != (preferred_source, preferred_target)
        ]
        alternatives.sort(key=lambda pair: (
            pair[0] != preferred_source,
            pair[1] != preferred_target,
            sides.index(pair[0]),
            sides.index(pair[1]),
        ))
        side_pairs.extend(alternatives)
        ignored = frozenset((src_code, dst_code))

        for source_side, target_side in side_pairs:
            source_anchor, start = side_points(positions[src_code], source_side)
            target_anchor, end = side_points(positions[dst_code], target_side)
            if not segment_clear(*source_anchor, *start, ignored):
                continue
            if not segment_clear(*end, *target_anchor, ignored):
                continue
            grid_path = find_grid_path(start, end)
            if not grid_path:
                continue

            points = [source_anchor, *grid_path, target_anchor]
            simplified = []
            for point in points:
                if simplified and point == simplified[-1]:
                    continue
                while len(simplified) >= 2:
                    x1, y1 = simplified[-2]
                    x2, y2 = simplified[-1]
                    x3, y3 = point
                    if (x1 == x2 == x3) or (y1 == y2 == y3):
                        simplified.pop()
                    else:
                        break
                simplified.append(point)
            return " ".join(
                f"{'M' if index == 0 else 'L'} {x:g} {y:g}"
                for index, (x, y) in enumerate(simplified)
            )
        raise RuntimeError(f"Unable to route dependency arrow {src_code} -> {dst_code}")

    # Build arrows
    arrows = []

    for src_code, dst_code in edges:
        if src_code not in positions or dst_code not in positions:
            continue
        src = positions[src_code]
        dst = positions[dst_code]
        dx, dy = dst["x"], dst["y"]
        dw, dh = dst["w"], dst["h"]
        s_lane = src["lane_idx"]
        d_lane = dst["lane_idx"]

        backward = (d_lane < s_lane) or (
            d_lane == s_lane and dx + dw <= src["x"]
        )
        backward_direct = d_lane < s_lane and dx > src["x"] + src["w"]
        preferred_source, preferred_target = (
            ("bottom", "top") if backward and not backward_direct else ("right", "left")
        )
        path = route_around_cards(
            src_code, dst_code, preferred_source, preferred_target
        )
        marker = "url(#arrowhead)"
        stroke = "#444"
        stroke_dash = ""

        if backward and not backward_direct:
            stroke = "#c0392b"
            stroke_dash = "4,3"
            marker = "url(#arrowhead-red)"

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
