import hashlib
import json
from collections import Counter, defaultdict

from app.models import PackRequest, PackingSolution, Placement
from app.validator import validate_solution


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def rectangle(p: Placement):
    return p.x_mm, p.y_mm, p.x_mm + p.length_mm, p.y_mm + p.width_mm


def union_area(rectangles) -> int:
    rectangles = list(set(rectangles))
    edges = sorted({x for rect in rectangles for x in (rect[0], rect[2])})
    area = 0
    for left, right in zip(edges, edges[1:]):
        intervals = sorted((y1, y2) for x1, y1, x2, y2 in rectangles if x1 < right and x2 > left)
        covered = 0
        end = None
        for start, stop in intervals:
            covered += max(0, stop - max(start, end if end is not None else start))
            end = max(end if end is not None else stop, stop)
        area += (right - left) * covered
    return area


def bbox_void(rectangles):
    if not rectangles:
        return None
    area = ((max(r[2] for r in rectangles) - min(r[0] for r in rectangles))
            * (max(r[3] for r in rectangles) - min(r[1] for r in rectangles)))
    return round((area - union_area(rectangles)) / 1_000_000, 6)


def geometry_metrics(request: PackRequest, placements: list[Placement]) -> dict:
    floor_z = request.container.clearance_mm
    layers = defaultdict(list)
    tops = defaultdict(list)
    for p in placements:
        layers[p.z_mm].append(rectangle(p))
        tops[p.z_mm + p.height_mm].append(rectangle(p))
    support = []
    for p in placements:
        if p.z_mm <= floor_z:
            continue
        x1, y1, x2, y2 = rectangle(p)
        clipped = []
        for a, b, c, d in tops[p.z_mm]:
            left, bottom, right, top = max(x1, a), max(y1, b), min(x2, c), min(y2, d)
            if left < right and bottom < top:
                clipped.append((left, bottom, right, top))
        support.append(union_area(clipped) / (p.length_mm * p.width_mm))
    return {
        'floor_bbox_void_m2': bbox_void(layers[floor_z]),
        'upper_layer_max_void_m2': max((bbox_void(rects) for z, rects in layers.items() if z > floor_z), default=None),
        'min_support_pct': round(min(support) * 100, 4) if support else None,
        'unsupported_upper_pieces': sum(value < 1 for value in support),
        'upper_pieces': len(support),
    }


def summarize_solution(request: PackRequest, solution: PackingSolution) -> dict:
    pieces = solution.placements
    validation = validate_solution(request.container, request.cargo_items, pieces, item_gap_mm=request.item_gap_mm)
    errors = {issue.code for issue in validation.errors}
    counts = Counter(p.cargo_id for p in pieces)
    expected_loaded = {item.id: counts[item.id] for item in request.cargo_items}
    expected_unloaded = {item.id: item.quantity - counts[item.id] for item in request.cargo_items}
    if solution.loaded_counts != expected_loaded or solution.unloaded_counts != expected_unloaded:
        errors.add('COUNT_MISMATCH')
    if len({p.id for p in pieces}) != len(pieces):
        errors.add('DUPLICATE_ID')
    metrics = solution.metrics
    if metrics.loaded_pieces != len(pieces) or metrics.loaded_weight_g != sum(p.weight_g for p in pieces):
        errors.add('METRIC_MISMATCH')
    steps = Counter((p.cargo_id, p.step) for p in pieces)
    zones = Counter()
    for zone in solution.zones:
        zones[zone.cargo_id, zone.step] += zone.piece_count
    if steps != zones or metrics.loading_steps != len({p.step for p in pieces}):
        errors.add('STEP_COUNT_MISMATCH')
    layout = [p.model_dump(mode='json') for p in sorted(pieces, key=lambda p: (p.cargo_id, p.instance_index))]
    return {
        'valid': not errors, 'errors': sorted(errors), 'layout_sha256': digest(layout),
        'loaded_pieces': len(pieces), 'unloaded_pieces': sum(expected_unloaded.values()),
        'loaded_weight_g': sum(p.weight_g for p in pieces),
        'volume_utilization_pct': metrics.volume_utilization_pct,
        'length_imbalance_pct': metrics.length_imbalance_pct,
        'width_imbalance_pct': metrics.width_imbalance_pct,
        'loading_steps': metrics.loading_steps, 'cargo_zones': metrics.cargo_zones,
        'floor_largest_gap_mm': metrics.floor_largest_gap_mm,
        'floor_largest_transverse_gap_mm': metrics.floor_largest_transverse_gap_mm,
        'door_reserve_mm': request.container.inner_length_mm - max((p.x_mm + p.length_mm for p in pieces), default=0),
        'door_target_mm': request.door_buffer_mm,
        **geometry_metrics(request, pieces),
    }
