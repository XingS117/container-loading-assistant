from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Literal

from rectpack import (
    GuillotineBssfSas,
    MaxRectsBaf,
    MaxRectsBssf,
)

from .models import (
    CargoSpec,
    CenterOfGravity,
    Orientation,
    PackRequest,
    PackResponse,
    PackingSolution,
    Placement,
    SolutionMetrics,
    Zone,
)
from .validator import validate_solution


class PackingFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __reduce__(self):
        return type(self), (self.code, self.message)


@dataclass(frozen=True)
class StackUnit:
    id: str
    cargo: CargoSpec
    orientation: Orientation
    count: int
    length_mm: int
    width_mm: int
    item_height_mm: int
    stack_height_mm: int
    total_weight_g: int
    first_instance_index: int
    required: bool

    @property
    def volume_mm3(self) -> int:
        return (
            self.cargo.length_mm
            * self.cargo.width_mm
            * self.cargo.height_mm
            * self.count
        )


@dataclass(frozen=True)
class CompositeUnit:
    """A pallet (count=1) with carton stacks packed on its top surface.

    Each on_top entry records the carton stack together with its (x, y)
    offset inside the pallet top surface, so multi-stack top layouts keep
    distinct footprints when expanded.
    """
    pallet: StackUnit
    on_top: tuple[tuple[StackUnit, int, int], ...] = ()

    @property
    def id(self) -> str:
        return self.pallet.id

    @property
    def cargo(self) -> CargoSpec:
        return self.pallet.cargo

    @property
    def required(self) -> bool:
        return self.pallet.required

    @property
    def orientation(self) -> Orientation:
        return self.pallet.orientation

    @property
    def length_mm(self) -> int:
        return self.pallet.length_mm

    @property
    def width_mm(self) -> int:
        return self.pallet.width_mm

    @property
    def count(self) -> int:
        return self.pallet.count + sum(stack.count for stack, _, _ in self.on_top)

    @property
    def total_weight_g(self) -> int:
        return self.pallet.total_weight_g + sum(stack.total_weight_g for stack, _, _ in self.on_top)

    @property
    def volume_mm3(self) -> int:
        return self.pallet.volume_mm3 + sum(stack.volume_mm3 for stack, _, _ in self.on_top)


@dataclass(frozen=True)
class PackedStack:
    unit: StackUnit
    x_mm: int
    y_mm: int
    rotated: bool = False
    step: int | None = None

    @property
    def length_mm(self) -> int:
        return self.unit.width_mm if self.rotated else self.unit.length_mm

    @property
    def width_mm(self) -> int:
        return self.unit.length_mm if self.rotated else self.unit.width_mm

    @property
    def orientation(self) -> Orientation:
        if not self.rotated:
            return self.unit.orientation
        return SWAP_ORIENTATIONS[self.unit.orientation]


SWAP_ORIENTATIONS = {
    Orientation.LWH: Orientation.WLH,
    Orientation.WLH: Orientation.LWH,
    Orientation.LHW: Orientation.HLW,
    Orientation.HLW: Orientation.LHW,
    Orientation.WHL: Orientation.HWL,
    Orientation.HWL: Orientation.WHL,
}


PACK_ALGOS = (MaxRectsBssf, MaxRectsBaf, GuillotineBssfSas)


def _stack_capacity(
    cargo: CargoSpec,
    orientation: Orientation,
    available_height: int,
    gap: int,
) -> int:
    _, _, item_height = cargo.dimensions_for(orientation)
    # Horizontal clearance must not create a vertical air gap between stacked items.
    by_height = available_height // item_height
    if not cargo.stackable or cargo.fragile:
        return min(1, by_height)
    by_load = cargo.max_top_load_g // cargo.weight_g + 1
    return max(0, min(cargo.max_layers, by_height, by_load))


def _best_orientation(
    request: PackRequest,
    cargo: CargoSpec,
    quantity: int,
    profile: Literal["high_fill", "stable"] = "high_fill",
) -> Orientation | None:
    c = request.container.clearance_mm
    available_length = request.container.inner_length_mm - 2 * c
    available_width = request.container.inner_width_mm - 2 * c
    available_height = request.container.inner_height_mm - 2 * c
    choices: list[tuple[float, int, int, str, Orientation]] = []
    for orientation in cargo.allowed_orientations:
        length, width, height = cargo.dimensions_for(orientation)
        capacity = _stack_capacity(
            cargo,
            orientation,
            available_height,
            request.item_gap_mm,
        )
        if (
            capacity < 1
            or length > available_length
            or width > available_width
            or width > request.container.door_width_mm - 2 * c
            or height > request.container.door_height_mm - 2 * c
        ):
            continue
        floor_per_piece = (length * width) / capacity
        full_stacks, remainder = divmod(quantity, capacity)
        vertical_cg = height * (
            full_stacks * capacity**2 + remainder**2
        ) / (2 * quantity)
        primary = vertical_cg if profile == "stable" else floor_per_piece
        secondary = floor_per_piece if profile == "stable" else height
        choices.append((primary, secondary, max(length, width), orientation.value, orientation))
    if not choices:
        return None
    choices.sort()
    return choices[0][-1]


def _build_stack_units(
    request: PackRequest,
    quantity_limits: dict[str, int] | None = None,
    profile: Literal["high_fill", "stable"] = "high_fill",
) -> list[StackUnit]:
    available_height = request.container.inner_height_mm - 2 * request.container.clearance_mm
    units: list[StackUnit] = []
    for cargo in request.cargo_items:
        target_quantity = quantity_limits.get(cargo.id, cargo.quantity) if quantity_limits else cargo.quantity
        orientation = _best_orientation(request, cargo, target_quantity, profile)
        if orientation is None:
            if cargo.must_load:
                raise PackingFailure(
                    "MUST_LOAD_UNSATISFIED",
                    f"必装货物 {cargo.sku} 无法按允许方向通过柜门或放入柜内",
                )
            continue
        length, width, height = cargo.dimensions_for(orientation)
        capacity = _stack_capacity(
            cargo,
            orientation,
            available_height,
            request.item_gap_mm,
        )
        remaining = target_quantity
        instance_index = 0
        stack_index = 0
        while remaining:
            count = min(capacity, remaining)
            units.append(
                StackUnit(
                    id=f"{cargo.id}-stack-{stack_index}",
                    cargo=cargo,
                    orientation=orientation,
                    count=count,
                    length_mm=length,
                    width_mm=width,
                    item_height_mm=height,
                    stack_height_mm=count * height,
                    total_weight_g=count * cargo.weight_g,
                    first_instance_index=instance_index,
                    required=cargo.must_load,
                )
            )
            remaining -= count
            instance_index += count
            stack_index += 1
    return units


def _select_payload_units(
    request: PackRequest,
    units: list[StackUnit],
    strategy: str,
) -> list[StackUnit]:
    capacity = request.container.max_payload_g
    selected: list[StackUnit] = []
    for unit in _ordered_units(units, strategy):
        if unit.total_weight_g <= capacity:
            selected.append(unit)
            capacity -= unit.total_weight_g
            continue
        possible_count = capacity // unit.cargo.weight_g
        if possible_count > 0 and not unit.required:
            selected.append(
                replace(
                    unit,
                    count=possible_count,
                    stack_height_mm=possible_count * unit.item_height_mm,
                    total_weight_g=possible_count * unit.cargo.weight_g,
                )
            )
            capacity -= possible_count * unit.cargo.weight_g
        if unit.required:
            raise PackingFailure(
                "MUST_LOAD_UNSATISFIED",
                f"必装货物 {unit.cargo.sku} 导致总重量超过柜体载重",
            )
    return selected


def _try_add_to_pallet_top(
    packer,
    carton: StackUnit,
    request: PackRequest,
) -> tuple[object, StackUnit] | tuple[None, None]:
    """Add a carton stack to a pallet-top bin; rotation only if allowed.

    Returns (rect, placed_unit); placed_unit is a rotated variant when the
    stack was placed rotated, so expansion uses consistent dimensions.
    """
    c = request.container.clearance_mm
    door_usable_width = request.container.door_width_mm - 2 * c
    rect = packer.add_rect(carton.length_mm, carton.width_mm, rid=carton.id)
    if rect is not None:
        return rect, carton
    swapped_orientation = SWAP_ORIENTATIONS.get(carton.orientation)
    if (
        swapped_orientation in carton.cargo.allowed_orientations
        and carton.length_mm <= door_usable_width
    ):
        rect = packer.add_rect(carton.width_mm, carton.length_mm, rid=carton.id)
        if rect is not None:
            return rect, replace(
                carton,
                length_mm=carton.width_mm,
                width_mm=carton.length_mm,
                orientation=swapped_orientation,
            )
    return None, None


def _merge_pallet_cartons(
    request: PackRequest,
    units: list[StackUnit],
) -> list[CompositeUnit | StackUnit]:
    """Merge stackable carton stacks onto pallet tops (greedy, deterministic)."""
    pallets = [unit for unit in units if unit.cargo.kind == "pallet"]
    cartons = [unit for unit in units if unit.cargo.kind == "carton"]
    if not pallets or not cartons:
        return list(units)
    c = request.container.clearance_mm
    available_height = request.container.inner_height_mm - 2 * c
    pallets.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    cartons.sort(key=lambda unit: (-unit.volume_mm3, unit.id))
    merged: list[CompositeUnit | StackUnit] = []
    remaining: list[StackUnit] = cartons
    for pallet in pallets:
        if pallet.cargo.max_top_load_g <= 0:
            merged.append(pallet)
            continue
        packer = MaxRectsBssf(pallet.length_mm, pallet.width_mm, rot=False)
        assigned: list[tuple[StackUnit, int, int]] = []
        load_left = pallet.cargo.max_top_load_g
        height_left = available_height - pallet.stack_height_mm
        still: list[StackUnit] = []
        for carton in remaining:
            if carton.cargo.fragile:
                still.append(carton)
                continue
            if (
                carton.stack_height_mm > height_left
                or carton.total_weight_g > load_left
            ):
                still.append(carton)
                continue
            rect, placed_carton = _try_add_to_pallet_top(packer, carton, request)
            if rect is None:
                still.append(carton)
                continue
            assigned.append((placed_carton, int(rect.x), int(rect.y)))
            load_left -= carton.total_weight_g
        remaining = still
        if assigned:
            merged.append(CompositeUnit(pallet=pallet, on_top=tuple(assigned)))
        else:
            merged.append(pallet)
    merged.extend(remaining)
    return merged


def _ordered_units(units: list[StackUnit], strategy: str) -> list[StackUnit]:
    required = [unit for unit in units if unit.required]
    optional = [unit for unit in units if not unit.required]
    key_map = {
        "volume": lambda unit: (-unit.volume_mm3, -unit.length_mm * unit.width_mm, unit.id),
        "footprint": lambda unit: (-unit.length_mm * unit.width_mm, -unit.volume_mm3, unit.id),
        "weight": lambda unit: (-unit.total_weight_g, -unit.volume_mm3, unit.id),
        "lightweight": lambda unit: (unit.total_weight_g, -unit.volume_mm3, unit.id),
        "pieces": lambda unit: (-unit.count, unit.total_weight_g, unit.id),
        "sku": lambda unit: (unit.cargo.id, -unit.length_mm * unit.width_mm, unit.id),
    }
    key = key_map[strategy]
    return sorted(required, key=key) + sorted(optional, key=key)


def _pack_units(
    request: PackRequest,
    units: list[StackUnit],
    pack_algo,
    order: str,
) -> list[PackedStack]:
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    bin_length = request.container.inner_length_mm - 2 * c + gap
    bin_width = request.container.inner_width_mm - 2 * c + gap
    packing_bin = pack_algo(bin_length, bin_width, rot=False)
    packed: list[PackedStack] = []
    for unit in _ordered_units(units, order):
        swapped_orientation = PackedStack(
            unit=unit,
            x_mm=0,
            y_mm=0,
            rotated=True,
        ).orientation
        packing_bin.rot = swapped_orientation in unit.cargo.allowed_orientations
        rect = packing_bin.add_rect(
            unit.length_mm + gap,
            unit.width_mm + gap,
            rid=unit.id,
        )
        if rect is None:
            continue
        rotated = (
            unit.length_mm != unit.width_mm
            and int(rect.width) == unit.width_mm + gap
            and int(rect.height) == unit.length_mm + gap
        )
        packed.append(
            PackedStack(
                unit=unit,
                x_mm=c + int(rect.x),
                y_mm=c + int(rect.y),
                rotated=rotated,
            )
        )
    packed.sort(key=lambda stack: stack.unit.id)
    return packed


def _candidate_score(stacks: list[PackedStack]) -> tuple[int, int, int]:
    return (
        sum(stack.unit.volume_mm3 for stack in stacks),
        sum(stack.unit.count for stack in stacks),
        -len(stacks),
    )


def _high_fill_candidate(request: PackRequest, units: list[StackUnit]) -> list[PackedStack]:
    candidates: list[list[PackedStack]] = []
    for payload_strategy in ("volume", "footprint", "pieces", "lightweight"):
        pool = _select_payload_units(request, units, payload_strategy)
        for algo in PACK_ALGOS:
            for order in ("volume", "footprint", "weight", "lightweight"):
                packed = _pack_units(request, pool, algo, order)
                packed_ids = {stack.unit.id for stack in packed}
                if all(not unit.required or unit.id in packed_ids for unit in units):
                    candidates.append(packed)
    if not candidates:
        missing_required = [unit for unit in units if unit.required]
        cargo_names = "、".join(sorted({unit.cargo.sku for unit in missing_required}))
        raise PackingFailure(
            "MUST_LOAD_UNSATISFIED",
            f"必装货物 {cargo_names} 无法全部放入当前柜型",
        )
    best_score = max(_candidate_score(candidate) for candidate in candidates)
    best_candidates = [
        candidate
        for candidate in candidates
        if _candidate_score(candidate) == best_score
    ]
    return min(best_candidates, key=lambda candidate: _stack_imbalance(request, candidate))


def _repack_same_units(
    request: PackRequest,
    selected: list[StackUnit],
    profile: Literal["stable", "easy"],
) -> list[PackedStack]:
    candidates = [
        _pack_units(request, selected, algo, order)
        for algo in PACK_ALGOS
        for order in (("weight", "footprint", "volume") if profile == "stable" else ("sku", "footprint", "volume"))
    ]
    complete = [candidate for candidate in candidates if len(candidate) == len(selected)]
    if not complete:
        return []
    if profile == "stable":
        centered = [_center_stacks(request, candidate) for candidate in complete]
        return min(centered, key=lambda candidate: _stack_imbalance(request, candidate))
    return min(complete, key=_cargo_transitions)


def _stack_imbalance(request: PackRequest, stacks: list[PackedStack]) -> float:
    total_weight = sum(stack.unit.total_weight_g for stack in stacks)
    if not total_weight:
        return 0
    cg_x = sum((stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g for stack in stacks) / total_weight
    cg_y = sum((stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g for stack in stacks) / total_weight
    return max(
        abs(cg_x - request.container.inner_length_mm / 2) / (request.container.inner_length_mm / 2),
        abs(cg_y - request.container.inner_width_mm / 2) / (request.container.inner_width_mm / 2),
    )


def _cargo_transitions(stacks: list[PackedStack]) -> int:
    ordered = sorted(stacks, key=lambda stack: (stack.x_mm, stack.y_mm, stack.unit.cargo.id))
    return sum(
        first.unit.cargo.id != second.unit.cargo.id
        for first, second in zip(ordered, ordered[1:])
    )


def _center_stacks(request: PackRequest, stacks: list[PackedStack]) -> list[PackedStack]:
    if not stacks:
        return stacks
    c = request.container.clearance_mm
    total_weight = sum(stack.unit.total_weight_g for stack in stacks)
    cg_x = sum(
        (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
        for stack in stacks
    ) / total_weight
    cg_y = sum(
        (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
        for stack in stacks
    ) / total_weight
    target_x = request.container.inner_length_mm / 2
    target_y = request.container.inner_width_mm / 2
    min_x = min(stack.x_mm for stack in stacks)
    min_y = min(stack.y_mm for stack in stacks)
    max_x = max(stack.x_mm + stack.length_mm for stack in stacks)
    max_y = max(stack.y_mm + stack.width_mm for stack in stacks)
    dx = round(target_x - cg_x)
    dy = round(target_y - cg_y)
    dx = max(c - min_x, min(dx, request.container.inner_length_mm - c - max_x))
    dy = max(c - min_y, min(dy, request.container.inner_width_mm - c - max_y))
    return [replace(stack, x_mm=stack.x_mm + dx, y_mm=stack.y_mm + dy) for stack in stacks]


def _swap_balance(
    request: PackRequest,
    stacks: list[PackedStack],
    max_passes: int = 4,
) -> list[PackedStack]:
    """Swap stacks with identical footprints to lower length-axis imbalance first."""
    if len(stacks) < 2:
        return stacks
    target_x = request.container.inner_length_mm / 2
    target_y = request.container.inner_width_mm / 2
    current = list(stacks)

    def deviations(candidate: list[PackedStack]) -> tuple[float, float]:
        total_weight = sum(stack.unit.total_weight_g for stack in candidate) or 1
        cg_x = sum(
            (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
            for stack in candidate
        ) / total_weight
        cg_y = sum(
            (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
            for stack in candidate
        ) / total_weight
        return abs(cg_x - target_x) / (target_x or 1), abs(cg_y - target_y) / (target_y or 1)

    for _ in range(max_passes):
        current_x, current_y = deviations(current)
        best_swap: tuple[int, int] | None = None
        best_dev: tuple[float, float] | None = None
        groups: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index, stack in enumerate(current):
            groups[(stack.length_mm, stack.width_mm)].append(index)
        total_weight = sum(stack.unit.total_weight_g for stack in current) or 1
        cg_x = sum(
            (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
            for stack in current
        ) / total_weight
        cg_y = sum(
            (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
            for stack in current
        ) / total_weight
        for indexes in groups.values():
            for first in range(len(indexes)):
                for second in range(first + 1, len(indexes)):
                    i, j = indexes[first], indexes[second]
                    a, b = current[i], current[j]
                    weight_a = a.unit.total_weight_g
                    weight_b = b.unit.total_weight_g
                    center_ax = a.x_mm + a.length_mm / 2
                    center_bx = b.x_mm + b.length_mm / 2
                    center_ay = a.y_mm + a.width_mm / 2
                    center_by = b.y_mm + b.width_mm / 2
                    next_cg_x = cg_x + (weight_a - weight_b) * (center_bx - center_ax) / total_weight
                    next_cg_y = cg_y + (weight_a - weight_b) * (center_by - center_ay) / total_weight
                    next_dev = (
                        abs(next_cg_x - target_x) / (target_x or 1),
                        abs(next_cg_y - target_y) / (target_y or 1),
                    )
                    if best_dev is None or next_dev < best_dev:
                        best_dev = next_dev
                        best_swap = (i, j)
        if best_swap is None or best_dev >= (current_x, current_y):
            break
        i, j = best_swap
        original_i = current[i]
        current[i] = replace(original_i, x_mm=current[j].x_mm, y_mm=current[j].y_mm)
        current[j] = replace(current[j], x_mm=original_i.x_mm, y_mm=original_i.y_mm)
    return current


def _refine_grid_length_balance(
    stacks: list[PackedStack],
    target_x: float,
    max_passes: int = 4,
) -> list[PackedStack]:
    """Swap stacks inside the same grid column to center the length-axis CG."""
    current = list(stacks)
    total_weight = sum(stack.unit.total_weight_g for stack in current)
    if not total_weight:
        return current

    def moment(stack: PackedStack) -> float:
        return stack.unit.total_weight_g * (stack.x_mm + stack.length_mm / 2 - target_x)

    for _ in range(max_passes):
        residual = sum(moment(stack) for stack in current)
        if abs(residual) < 1:
            break
        columns: dict[int, list[int]] = defaultdict(list)
        for index, stack in enumerate(current):
            columns[stack.y_mm].append(index)
        best_swap: tuple[int, int] | None = None
        best_gain = 0.0
        for indexes in columns.values():
            for first in range(len(indexes)):
                for second in range(first + 1, len(indexes)):
                    i, j = indexes[first], indexes[second]
                    weight_i = current[i].unit.total_weight_g
                    weight_j = current[j].unit.total_weight_g
                    center_i = current[i].x_mm + current[i].length_mm / 2
                    center_j = current[j].x_mm + current[j].length_mm / 2
                    next_residual = residual + (weight_i - weight_j) * (center_j - center_i)
                    gain = abs(residual) - abs(next_residual)
                    if gain > best_gain:
                        best_gain = gain
                        best_swap = (i, j)
        if best_swap is None or best_gain <= 0:
            break
        i, j = best_swap
        original_i = current[i]
        current[i] = replace(original_i, x_mm=current[j].x_mm)
        current[j] = replace(current[j], x_mm=original_i.x_mm)
    return current


def _pallet_grid_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Balance single-layer pallets in a weight-aware grid, one step per row."""
    if not units or any(unit.count != 1 or unit.cargo.kind != "pallet" for unit in units):
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c

    options: list[set[tuple[int, int]]] = []
    for unit in units:
        candidates = {(unit.length_mm, unit.width_mm)}
        swapped_orientation = SWAP_ORIENTATIONS.get(unit.orientation)
        if swapped_orientation in unit.cargo.allowed_orientations:
            swapped = (unit.width_mm, unit.length_mm)
            if swapped[1] <= door_usable_width:
                candidates.add(swapped)
        options.append(candidates)
    common_footprints = set.intersection(*options)
    if not common_footprints:
        return None

    best: tuple[tuple[int, int, int], tuple[int, int]] | None = None
    for footprint in sorted(common_footprints):
        along_length, across_width = footprint
        if across_width + gap > usable_width:
            continue
        columns = usable_width // (across_width + gap)
        if columns < 1:
            continue
        rows = (len(units) + columns - 1) // columns
        total_length = rows * along_length + (rows - 1) * gap
        if total_length > usable_length:
            continue
        score = (columns, -total_length, along_length)
        if best is None or score > best[0]:
            best = (score, footprint)
    if best is None:
        return None

    along_length, across_width = best[1]
    columns = usable_width // (across_width + gap)
    rows = (len(units) + columns - 1) // columns
    oriented = []
    for unit in units:
        rotated = (unit.width_mm, unit.length_mm) == (along_length, across_width)
        oriented.append((unit, rotated))
    oriented.sort(key=lambda item: (-item[0].total_weight_g, item[0].id))

    row_order = sorted(
        range(rows),
        key=lambda row: (abs(row - (rows - 1) / 2), row),
    )
    column_weights = [0] * columns
    assignments: list[tuple[int, int, int]] = []
    index = 0
    for row in row_order:
        for _ in range(columns):
            if index >= len(oriented):
                break
            column = min(
                range(columns),
                key=lambda col: (column_weights[col], col),
            )
            column_weights[column] += oriented[index][0].total_weight_g
            assignments.append((row, column, index))
            index += 1

    placed: list[PackedStack] = []
    for row, column, unit_index in assignments:
        unit, rotated = oriented[unit_index]
        placed.append(
            PackedStack(
                unit=unit,
                x_mm=c + row * (along_length + gap),
                y_mm=c + column * (across_width + gap),
                rotated=rotated,
            )
        )
    placed = _refine_grid_length_balance(placed, request.container.inner_length_mm / 2)
    return [
        replace(
            stack,
            step=round((stack.x_mm - c) / (along_length + gap)) + 1,
        )
        for stack in placed
    ]


def _mixed_balance_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
) -> list[PackedStack] | None:
    """Center pallet units along container length; carton stacks fill both ends."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c
    pallets = [unit for unit in units if unit.cargo.kind == "pallet"]
    cartons = [unit for unit in units if unit.cargo.kind == "carton"]
    if not pallets:
        return None  # 纯散箱走 rectpack；纯整托/散箱全上托时仍返回居中托盘带

    options: list[set[tuple[int, int]]] = []
    for unit in pallets:
        candidates = {(unit.length_mm, unit.width_mm)}
        swapped_orientation = SWAP_ORIENTATIONS.get(unit.orientation)
        if swapped_orientation in unit.cargo.allowed_orientations:
            swapped = (unit.width_mm, unit.length_mm)
            if swapped[1] <= door_usable_width:
                candidates.add(swapped)
        options.append(candidates)
    common_footprints = set.intersection(*options)
    if not common_footprints:
        return None

    best: tuple[tuple[int, int, int], tuple[int, int]] | None = None
    for footprint in sorted(common_footprints):
        along_length, across_width = footprint
        if across_width + gap > usable_width:
            continue
        columns = usable_width // (across_width + gap)
        if columns < 1:
            continue
        rows = (len(pallets) + columns - 1) // columns
        total_length = rows * along_length + (rows - 1) * gap
        if total_length > usable_length:
            continue
        score = (columns, -total_length, along_length)
        if best is None or score > best[0]:
            best = (score, footprint)
    if best is None:
        return None

    along_length, across_width = best[1]
    columns = usable_width // (across_width + gap)
    rows = (len(pallets) + columns - 1) // columns
    total_length = rows * along_length + (rows - 1) * gap
    x0 = c + (usable_length - total_length) // 2

    pallets.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    row_order = sorted(
        range(rows),
        key=lambda row: (abs(row - (rows - 1) / 2), row),
    )
    column_weights = [0] * columns
    assignments: list[tuple[int, int, int]] = []
    index = 0
    for row in row_order:
        for _ in range(columns):
            if index >= len(pallets):
                break
            column = min(
                range(columns),
                key=lambda col: (column_weights[col], col),
            )
            column_weights[column] += pallets[index].total_weight_g
            assignments.append((row, column, index))
            index += 1

    left_len = x0 - c
    right_len = usable_length - total_length - left_len
    # 装载区域按 x 升序编号：左端区、托盘带（按行）、右端区
    next_step = 1
    left_step = next_step if left_len > 0 else None
    if left_step is not None:
        next_step += 1
    pallet_row_first_step = next_step
    next_step += rows
    right_step = next_step if right_len > 0 else None

    placed: list[PackedStack] = []
    for row, column, unit_index in assignments:
        unit = pallets[unit_index]
        placed.append(
            PackedStack(
                unit=unit,
                x_mm=x0 + row * (along_length + gap),
                y_mm=c + column * (across_width + gap),
                rotated=(unit.length_mm, unit.width_mm) == (across_width, along_length),
                step=pallet_row_first_step + row,
            )
        )

    cartons.sort(key=lambda unit: (-unit.volume_mm3, unit.id))
    remaining = cartons
    for zone_x, zone_len, zone_step in (
        (c, left_len, left_step),
        (x0 + total_length, right_len, right_step),
    ):
        if zone_step is None or not remaining:
            continue
        packer = MaxRectsBssf(zone_len, usable_width, rot=False)
        still: list[StackUnit] = []
        for carton in remaining:
            rect, _ = _try_add_to_pallet_top(packer, carton, request)
            if rect is None:
                still.append(carton)
                continue
            placed.append(
                PackedStack(
                    unit=carton,
                    x_mm=zone_x + int(rect.x),
                    y_mm=c + int(rect.y),
                    rotated=(
                        carton.length_mm != carton.width_mm
                        and int(rect.width) == carton.width_mm
                        and int(rect.height) == carton.length_mm
                    ),
                    step=zone_step,
                )
            )
        remaining = still
    if remaining:
        return None  # 两端放不下全部散箱 → 布局失败，由调用方回退
    placed.sort(key=lambda stack: stack.unit.id)
    return placed


def _expand_stacks(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: str,
) -> list[Placement]:
    explicit_steps = {
        stack.unit.id: stack.step
        for stack in stacks
        if stack.step is not None
    }
    ordered = sorted(
        stacks,
        key=(
            (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.cargo.id))
            if profile == "easy"
            else (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.id))
        ),
    )
    if len(explicit_steps) == len(stacks):
        step_by_id = explicit_steps
    elif profile == "easy":
        cargo_order = list(dict.fromkeys(stack.unit.cargo.id for stack in ordered))
        step_by_cargo = {cargo_id: index + 1 for index, cargo_id in enumerate(cargo_order)}
        step_by_id = {stack.unit.id: step_by_cargo[stack.unit.cargo.id] for stack in ordered}
    else:
        step_by_id = {stack.unit.id: index + 1 for index, stack in enumerate(ordered)}
    placements: list[Placement] = []
    for stack in stacks:
        unit = stack.unit
        base_z = request.container.clearance_mm
        step = step_by_id[unit.id]
        if isinstance(unit, CompositeUnit):
            pallet = unit.pallet
            for offset in range(pallet.count):
                placements.append(
                    Placement(
                        id=f"{pallet.cargo.id}-{pallet.first_instance_index + offset}",
                        cargo_id=pallet.cargo.id,
                        instance_index=pallet.first_instance_index + offset,
                        x_mm=stack.x_mm,
                        y_mm=stack.y_mm,
                        z_mm=base_z + offset * pallet.item_height_mm,
                        length_mm=stack.length_mm,
                        width_mm=stack.width_mm,
                        height_mm=pallet.item_height_mm,
                        rotation=stack.orientation,
                        weight_g=pallet.cargo.weight_g,
                        step=step,
                    )
                )
            top_z = base_z + pallet.stack_height_mm
            for on_top, offset_x, offset_y in unit.on_top:
                for offset in range(on_top.count):
                    placements.append(
                        Placement(
                            id=f"{on_top.cargo.id}-{on_top.first_instance_index + offset}",
                            cargo_id=on_top.cargo.id,
                            instance_index=on_top.first_instance_index + offset,
                            x_mm=stack.x_mm + offset_x,
                            y_mm=stack.y_mm + offset_y,
                            z_mm=top_z + offset * on_top.item_height_mm,
                            length_mm=on_top.length_mm,
                            width_mm=on_top.width_mm,
                            height_mm=on_top.item_height_mm,
                            rotation=on_top.orientation,
                            weight_g=on_top.cargo.weight_g,
                            step=step,
                        )
                    )
            continue
        for offset in range(unit.count):
            instance_index = unit.first_instance_index + offset
            placements.append(
                Placement(
                    id=f"{unit.cargo.id}-{instance_index}",
                    cargo_id=unit.cargo.id,
                    instance_index=instance_index,
                    x_mm=stack.x_mm,
                    y_mm=stack.y_mm,
                    z_mm=base_z + offset * unit.item_height_mm,
                    length_mm=stack.length_mm,
                    width_mm=stack.width_mm,
                    height_mm=unit.item_height_mm,
                    rotation=stack.orientation,
                    weight_g=unit.cargo.weight_g,
                    step=step,
                )
            )
    placements.sort(key=lambda item: (item.step, item.z_mm, item.id))
    return placements


def _band_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Full-width contiguous band per SKU, one loading step per band."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c
    by_sku: dict[str, list[StackUnit]] = defaultdict(list)
    for unit in units:
        by_sku[unit.cargo.id].append(unit)

    placed: list[PackedStack] = []
    cursor = 0
    band_index = 0
    for cargo_id in (item.id for item in request.cargo_items):
        group = by_sku.get(cargo_id)
        if not group:
            continue
        base_length = group[0].length_mm
        base_width = group[0].width_mm
        variants: list[tuple[int, int, bool]] = [(base_length, base_width, False)]
        swapped_orientation = SWAP_ORIENTATIONS.get(group[0].orientation)
        if swapped_orientation in group[0].cargo.allowed_orientations:
            swapped = (base_width, base_length)
            if swapped[1] <= door_usable_width:
                variants.append((swapped[0], swapped[1], True))
        best: tuple[int, int, int, int, bool] | None = None
        for along_length, across_width, rotated in variants:
            if across_width + gap > usable_width:
                continue
            columns = usable_width // (across_width + gap)
            if columns < 1:
                continue
            rows = (len(group) + columns - 1) // columns
            depth = rows * along_length + (rows - 1) * gap
            if best is None or (depth, -columns) < (best[0], best[1]):
                best = (depth, columns, along_length, across_width, rotated)
        if best is None:
            return None
        depth, columns, along_length, across_width, rotated = best
        if cursor + depth > usable_length:
            return None
        x0 = c + cursor
        for index, unit in enumerate(group):
            row = index // columns
            column = index % columns
            placed.append(
                PackedStack(
                    unit=unit,
                    x_mm=x0 + row * (along_length + gap),
                    y_mm=c + column * (across_width + gap),
                    rotated=rotated,
                    step=band_index + 1,
                )
            )
        cursor += depth + gap
        band_index += 1
    return placed


def _shelf_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Full-width shelves with contiguous same-SKU blocks, one step per shelf."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    remaining = sorted(
        units,
        key=lambda unit: (-unit.length_mm, -unit.volume_mm3, unit.id),
    )
    sku_order = {
        cargo_id: index
        for index, cargo_id in enumerate(item.id for item in request.cargo_items)
    }
    placed: list[PackedStack] = []
    cursor = 0
    shelf_index = 0
    while remaining:
        shelf_depth = remaining[0].length_mm
        if cursor + shelf_depth > usable_length:
            return None
        shelf_units: list[StackUnit] = []
        width_left = usable_width
        index = 0
        while index < len(remaining):
            unit = remaining[index]
            if unit.length_mm <= shelf_depth and unit.width_mm + gap <= width_left:
                shelf_units.append(unit)
                width_left -= unit.width_mm + gap
                del remaining[index]
            else:
                index += 1
        if not shelf_units:
            return None
        shelf_units.sort(
            key=lambda unit: (sku_order.get(unit.cargo.id, 10**9), unit.id)
        )
        y = c
        for unit in shelf_units:
            placed.append(
                PackedStack(
                    unit=unit,
                    x_mm=c + cursor,
                    y_mm=y,
                    step=shelf_index + 1,
                )
            )
            y += unit.width_mm + gap
        cursor += shelf_depth + gap
        shelf_index += 1
    return placed


def _try_region_layouts(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    bands = _band_layout(request, units)
    if bands is not None:
        return bands
    return _shelf_layout(request, units)


def _easy_region_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Region-based easy layout; drops optional SKUs/stacks when needed."""
    if not units:
        return None
    if all(unit.count == 1 and unit.cargo.kind == "pallet" for unit in units):
        return _pallet_grid_layout(request, units)
    full = _try_region_layouts(request, units)
    if full is not None:
        return full
    required = [unit for unit in units if unit.required]
    optional_by_sku: dict[str, list[StackUnit]] = defaultdict(list)
    for unit in units:
        if not unit.required:
            optional_by_sku[unit.cargo.id].append(unit)
    optional_order = [
        cargo_id
        for cargo_id in (item.id for item in request.cargo_items)
        if cargo_id in optional_by_sku
    ]
    optional_order.sort(
        key=lambda cargo_id: (
            sum(unit.volume_mm3 for unit in optional_by_sku[cargo_id]),
            cargo_id,
        )
    )
    kept_optional = [unit for unit in units if not unit.required]
    for cargo_id in optional_order:
        candidate = _try_region_layouts(request, required + kept_optional)
        if candidate is not None:
            return candidate
        kept_optional = [
            unit for unit in kept_optional if unit.cargo.id != cargo_id
        ]
    candidate = _try_region_layouts(request, required)
    if candidate is not None:
        return candidate
    optional_sorted = sorted(
        [unit for unit in units if not unit.required],
        key=lambda unit: (unit.volume_mm3, unit.id),
    )
    for skip in range(len(optional_sorted)):
        candidate = _try_region_layouts(
            request,
            required + optional_sorted[skip + 1 :],
        )
        if candidate is not None:
            return candidate
    return None


def _rects_connected(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    tolerance: int,
) -> bool:
    first_x1, first_y1, first_length, first_width = first
    second_x1, second_y1, second_length, second_width = second
    horizontal_gap = max(
        0,
        first_x1 - (second_x1 + second_length),
        second_x1 - (first_x1 + first_length),
    )
    vertical_gap = max(
        0,
        first_y1 - (second_y1 + second_width),
        second_y1 - (first_y1 + first_width),
    )
    return horizontal_gap <= tolerance and vertical_gap <= tolerance


def _merge_zone_rects(
    rects: list[tuple[int, int, int, int, int]],
    tolerance: int,
) -> list[tuple[int, int, int, int, int]]:
    parent = list(range(len(rects)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for first in range(len(rects)):
        for second in range(first + 1, len(rects)):
            if _rects_connected(
                rects[first][:4],
                rects[second][:4],
                tolerance,
            ):
                root_first = find(first)
                root_second = find(second)
                if root_first != root_second:
                    parent[root_second] = root_first
    merged: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rects)):
        merged[find(index)].append(index)
    zones: list[tuple[int, int, int, int, int]] = []
    for indexes in merged.values():
        min_x = min(rects[index][0] for index in indexes)
        min_y = min(rects[index][1] for index in indexes)
        max_x = max(rects[index][0] + rects[index][2] for index in indexes)
        max_y = max(rects[index][1] + rects[index][3] for index in indexes)
        piece_count = sum(rects[index][4] for index in indexes)
        zones.append((min_x, min_y, max_x - min_x, max_y - min_y, piece_count))
    return zones


def _compute_zones(
    request: PackRequest,
    placements: list[Placement],
) -> list[Zone]:
    floor_z = request.container.clearance_mm
    tolerance = request.item_gap_mm + 1
    column_counts = Counter(
        (placement.cargo_id, placement.x_mm, placement.y_mm)
        for placement in placements
    )
    groups: dict[tuple[str, int], list[tuple[int, int, int, int, int]]] = defaultdict(list)
    seen: set[tuple] = set()
    for placement in placements:
        if placement.z_mm != floor_z:
            continue
        rect = (
            placement.x_mm,
            placement.y_mm,
            placement.length_mm,
            placement.width_mm,
        )
        key = (placement.cargo_id, placement.step)
        if (key, rect) in seen:
            continue
        seen.add((key, rect))
        groups[key].append(
            (
                *rect,
                column_counts[(placement.cargo_id, placement.x_mm, placement.y_mm)],
            )
        )
    zones: list[Zone] = []
    for (cargo_id, step), rects in groups.items():
        for merged in _merge_zone_rects(rects, tolerance):
            zones.append(
                Zone(
                    step=step,
                    cargo_id=cargo_id,
                    x_mm=merged[0],
                    y_mm=merged[1],
                    length_mm=merged[2],
                    width_mm=merged[3],
                    piece_count=merged[4],
                )
            )
    zones.sort(key=lambda zone: (zone.step, zone.cargo_id, zone.x_mm, zone.y_mm))
    return zones


def _metrics(request: PackRequest, placements: list[Placement]) -> SolutionMetrics:
    cargo_by_id = {item.id: item for item in request.cargo_items}
    loaded_weight = sum(cargo_by_id[item.cargo_id].weight_g for item in placements)
    loaded_volume = sum(
        item.length_mm * item.width_mm * item.height_mm for item in placements
    )
    container_volume = (
        request.container.inner_length_mm
        * request.container.inner_width_mm
        * request.container.inner_height_mm
    )
    if loaded_weight:
        cg_x = sum(
            (item.x_mm + item.length_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
        cg_y = sum(
            (item.y_mm + item.width_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
        cg_z = sum(
            (item.z_mm + item.height_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
    else:
        cg_x = cg_y = cg_z = 0.0
    x_deviation = abs(cg_x - request.container.inner_length_mm / 2) / (
        request.container.inner_length_mm / 2
    )
    y_deviation = abs(cg_y - request.container.inner_width_mm / 2) / (
        request.container.inner_width_mm / 2
    )
    zones = len({(item.cargo_id, item.step) for item in placements})
    return SolutionMetrics(
        loaded_pieces=len(placements),
        loaded_weight_g=loaded_weight,
        volume_utilization_pct=round(loaded_volume / container_volume * 100, 2),
        weight_utilization_pct=round(
            loaded_weight / request.container.max_payload_g * 100,
            2,
        ),
        center_of_gravity=CenterOfGravity(
            x_mm=round(cg_x, 1),
            y_mm=round(cg_y, 1),
            z_mm=round(cg_z, 1),
        ),
        length_imbalance_pct=round(x_deviation * 100, 2),
        width_imbalance_pct=round(y_deviation * 100, 2),
        weight_imbalance_pct=round(max(x_deviation, y_deviation) * 100, 2),
        loading_steps=len({item.step for item in placements}),
        cargo_zones=zones,
    )


def _build_solution(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: Literal["high_fill", "stable", "easy"],
) -> PackingSolution:
    placements = _expand_stacks(request, stacks, profile)
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=request.item_gap_mm,
    )
    if not validation.valid:
        codes = ", ".join(sorted({issue.code for issue in validation.errors}))
        raise PackingFailure("INTERNAL_INVALID_LAYOUT", f"候选布局校验失败：{codes}")
    loaded = Counter(item.cargo_id for item in placements)
    loaded_counts = {item.id: loaded[item.id] for item in request.cargo_items}
    unloaded_counts = {
        item.id: item.quantity - loaded[item.id] for item in request.cargo_items
    }
    metrics = _metrics(request, placements)
    zones = _compute_zones(request, placements)
    names = {"high_fill": "装得多", "stable": "更稳妥", "easy": "易操作"}
    warnings = []
    if sum(unloaded_counts.values()):
        warnings.append(f"仍有 {sum(unloaded_counts.values())} 件货物未装入本柜")
    if profile == "high_fill":
        pros = [
            f"装入 {metrics.loaded_pieces} 件，体积利用率 {metrics.volume_utilization_pct}%",
            f"重量利用率 {metrics.weight_utilization_pct}%",
        ]
        cons = [f"重心最大偏差 {metrics.weight_imbalance_pct}%"]
    elif profile == "stable":
        pros = [
            f"前后重心偏差 {metrics.length_imbalance_pct}%（左右 {metrics.width_imbalance_pct}%）",
            f"装入数量保持为 {metrics.loaded_pieces} 件",
        ]
        if metrics.length_imbalance_pct > 10:
            warnings.append(
                f"前后重量偏差仍较大（{metrics.length_imbalance_pct}%），建议现场复核重货位置"
            )
        cons = [f"需要按 {metrics.loading_steps} 个区域执行装载"]
    else:
        pros = [
            f"同类货物按区域集中，共 {metrics.cargo_zones} 个货区",
            f"装载顺序拆分为 {metrics.loading_steps} 步",
        ]
        cons = [f"当前体积利用率为 {metrics.volume_utilization_pct}%"]
    return PackingSolution(
        profile=profile,
        name=names[profile],
        placements=placements,
        loaded_counts=loaded_counts,
        unloaded_counts=unloaded_counts,
        metrics=metrics,
        zones=zones,
        pros=pros,
        cons=cons,
        warnings=warnings,
    )


def _layout_signature(solution: PackingSolution) -> tuple:
    return tuple(
        sorted(
            (
                item.cargo_id,
                item.instance_index,
                item.x_mm,
                item.y_mm,
                item.z_mm,
                item.rotation.value,
            )
            for item in solution.placements
        )
    )


def pack_order(request: PackRequest) -> PackResponse:
    units = _build_stack_units(request)
    high_stacks = _high_fill_candidate(request, units)
    selected_units = [stack.unit for stack in high_stacks]
    selected_counts = Counter()
    for unit in selected_units:
        selected_counts[unit.cargo.id] += unit.count
    stable_units = _build_stack_units(request, dict(selected_counts), "stable")
    pallet_grid = _pallet_grid_layout(request, stable_units)
    if pallet_grid is not None:
        stable_stacks = pallet_grid
    else:
        stable_stacks = _repack_same_units(request, stable_units, "stable") or _center_stacks(request, high_stacks)
        stable_stacks = _swap_balance(request, stable_stacks)
    easy_region = _easy_region_layout(request, selected_units)
    easy_stacks = easy_region if easy_region is not None else (_repack_same_units(request, selected_units, "easy") or high_stacks)

    solutions = [
        _build_solution(request, high_stacks, "high_fill"),
        _build_solution(request, stable_stacks, "stable"),
        _build_solution(request, easy_stacks, "easy"),
    ]
    for index, solution in enumerate(solutions):
        for previous in solutions[:index]:
            if _layout_signature(solution) == _layout_signature(previous):
                solution.identical_to = previous.profile
                break
    if solutions[2].metrics.loaded_pieces < solutions[0].metrics.loaded_pieces:
        solutions[2].cons.append(
            f"为便于装载少装 {solutions[0].metrics.loaded_pieces - solutions[2].metrics.loaded_pieces} 件"
        )

    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    request_id = hashlib.sha256(request_json.encode("utf-8")).hexdigest()[:12]
    return PackResponse(request_id=request_id, solutions=solutions)
