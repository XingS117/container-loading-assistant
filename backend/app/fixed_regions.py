from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar


@dataclass(frozen=True)
class FloorRect:
    x: int
    y: int
    length: int
    width: int


T = TypeVar("T")


def choose_region(regions: list[FloorRect], length: int, width: int, score: Callable[[FloorRect], tuple] | None = None) -> FloorRect | None:
    """Return the smallest usable free region for one rectangle."""
    candidates = [region for region in regions if (length <= region.length and width <= region.width) or (width <= region.length and length <= region.width)]
    if not candidates:
        return None
    return min(candidates, key=score or (lambda region: (region.length * region.width, region.x, region.y)))


def subtract_rect(free: FloorRect, occupied: FloorRect) -> list[FloorRect]:
    """Cut one occupied floor rectangle out of a free rectangle."""
    left = max(free.x, occupied.x)
    right = min(free.x + free.length, occupied.x + occupied.length)
    bottom = max(free.y, occupied.y)
    top = min(free.y + free.width, occupied.y + occupied.width)
    if left >= right or bottom >= top:
        return [free]
    result: list[FloorRect] = []
    if left > free.x:
        result.append(FloorRect(free.x, free.y, left - free.x, free.width))
    if right < free.x + free.length:
        result.append(FloorRect(right, free.y, free.x + free.length - right, free.width))
    if bottom > free.y:
        result.append(FloorRect(left, free.y, right - left, bottom - free.y))
    if top < free.y + free.width:
        result.append(FloorRect(left, top, right - left, free.y + free.width - top))
    return [rect for rect in result if rect.length > 0 and rect.width > 0]


def free_floor_regions(length: int, width: int, occupied: list[FloorRect]) -> list[FloorRect]:
    regions = [FloorRect(0, 0, length, width)]
    for locked in occupied:
        regions = [part for region in regions for part in subtract_rect(region, locked)]
    return regions
