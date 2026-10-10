from datetime import timedelta

CARD_W = 168
CARD_H = 92
GAP_X = 24
LANE_H = 140
LANE_GAP = 16
LANE_LABEL_W = 120
HEADER_H = 36
RIGHT_MARGIN = 40


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

    # Build positions
    positions = {}  # task.code -> {x, y, lane_idx, idx_in_lane}
    for lane in lanes:
        li = lane_index[lane.id]
        for idx, task in enumerate(tasks_by_lane[lane.id]):
            x = LANE_LABEL_W + idx * (CARD_W + GAP_X)
            y = HEADER_H + li * (LANE_H + LANE_GAP)
            positions[task.code] = {
                "x": x,
                "y": y,
                "lane_idx": li,
                "idx_in_lane": idx,
                "task": task,
            }

    # Canvas size
    max_width = LANE_LABEL_W + RIGHT_MARGIN
    for lane in lanes:
        n = len(tasks_by_lane[lane.id])
        w = LANE_LABEL_W + max(0, n * (CARD_W + GAP_X) - GAP_X) + RIGHT_MARGIN
        if w > max_width:
            max_width = w
    canvas_h = HEADER_H + len(lanes) * (LANE_H + LANE_GAP) - LANE_GAP + 20

    # Build arrows
    arrows = []
    all_tasks_map = {t.code: t for t in tasks}

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

        backward = (d_lane < s_lane) or (d_lane == s_lane and dst["idx_in_lane"] <= src["idx_in_lane"])

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

        if not backward and s_lane == d_lane and dst["idx_in_lane"] > src["idx_in_lane"]:
            # Same lane, forward: horizontal straight
            path = f"M {ax1} {ay1} L {ax2} {ay2}"
        elif not backward and d_lane > s_lane:
            # Downward: right stub -> vertical -> left stub
            stub = 12
            mid_x = ax1 + stub
            # Route vertical in gap between src and dst if possible
            if dx > sx + CARD_W:
                mid_x = sx + CARD_W + (dx - (sx + CARD_W)) / 2
            else:
                mid_x = ax1 + stub + get_v_offset(ax1 + stub, ay1, ay2)
            path = f"M {ax1} {ay1} L {mid_x} {ay1} L {mid_x} {ay2} L {ax2} {ay2}"
        else:
            # Backward or same-lane backward: route around bottom/right
            stroke = "#c0392b"
            stroke_dash = "4,3"
            marker = "url(#arrowhead-red)"
            # Go right from src, down to dst lane, left to dst
            if d_lane > s_lane:
                # down and left/back: go right, down, left
                rx = max_width - 20
                path = f"M {ax1} {ay1} L {rx} {ay1} L {rx} {ay2} L {ax2} {ay2}"
            elif d_lane < s_lane:
                # up: go right, up, left
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
        header_h=HEADER_H,
        lane_h=LANE_H,
        lane_gap=LANE_GAP,
        lane_label_w=LANE_LABEL_W,
        card_w=CARD_W,
        card_h=CARD_H,
        gap_x=GAP_X,
    )
