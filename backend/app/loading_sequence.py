import heapq
from collections import defaultdict

from .models import Placement
from .validator import _base_intersection_area


def regenerate_loading_steps(placements: list[Placement]) -> list[Placement]:
    """Order a validated layout by support dependencies, then cabinet depth."""
    tops: dict[int, list[int]] = defaultdict(list)
    for index, placed in enumerate(placements):
        tops[placed.z_mm + placed.height_mm].append(index)
    remaining = [0] * len(placements)
    above: dict[int, list[int]] = defaultdict(list)
    for index, placed in enumerate(placements):
        for support in tops[placed.z_mm]:
            if _base_intersection_area(placed, placements[support]) > 0:
                remaining[index] += 1
                above[support].append(index)

    def priority(index: int) -> tuple:
        p = placements[index]
        return p.x_mm, p.z_mm, p.y_mm, p.cargo_id, p.id, index

    ready = [priority(i) for i, count in enumerate(remaining) if count == 0]
    heapq.heapify(ready)
    steps: dict[int, int] = {}
    previous_group = None
    step = 0
    while ready:
        index = heapq.heappop(ready)[-1]
        p = placements[index]
        group = (p.x_mm, p.z_mm, p.cargo_id)
        if group != previous_group:
            step += 1
            previous_group = group
        steps[index] = step
        for upper in above[index]:
            remaining[upper] -= 1
            if remaining[upper] == 0:
                heapq.heappush(ready, priority(upper))
    # Preserve array order and identities for editor selections and undo history.
    return [p.model_copy(update={"step": steps[i]}) for i, p in enumerate(placements)]
