from __future__ import annotations

import hashlib
import json
from collections import Counter
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
class PackedStack:
    unit: StackUnit
    x_mm: int
    y_mm: int
    rotated: bool = False

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
        swapped = {
            Orientation.LWH: Orientation.WLH,
            Orientation.WLH: Orientation.LWH,
            Orientation.LHW: Orientation.HLW,
            Orientation.HLW: Orientation.LHW,
            Orientation.WHL: Orientation.HWL,
            Orientation.HWL: Orientation.WHL,
        }
        return swapped[self.unit.orientation]


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
    return max(candidates, key=_candidate_score)


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


def _expand_stacks(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: str,
) -> list[Placement]:
    ordered = sorted(
        stacks,
        key=(
            (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.cargo.id))
            if profile == "easy"
            else (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.id))
        ),
    )
    if profile == "easy":
        cargo_order = list(dict.fromkeys(stack.unit.cargo.id for stack in ordered))
        step_by_cargo = {cargo_id: index + 1 for index, cargo_id in enumerate(cargo_order)}
        step_by_id = {stack.unit.id: step_by_cargo[stack.unit.cargo.id] for stack in ordered}
    else:
        step_by_id = {stack.unit.id: index + 1 for index, stack in enumerate(ordered)}
    placements: list[Placement] = []
    for stack in stacks:
        unit = stack.unit
        for offset in range(unit.count):
            instance_index = unit.first_instance_index + offset
            placements.append(
                Placement(
                    id=f"{unit.cargo.id}-{instance_index}",
                    cargo_id=unit.cargo.id,
                    instance_index=instance_index,
                    x_mm=stack.x_mm,
                    y_mm=stack.y_mm,
                    z_mm=request.container.clearance_mm + offset * unit.item_height_mm,
                    length_mm=stack.length_mm,
                    width_mm=stack.width_mm,
                    height_mm=unit.item_height_mm,
                    rotation=stack.orientation,
                    weight_g=unit.cargo.weight_g,
                    step=step_by_id[unit.id],
                )
            )
    placements.sort(key=lambda item: (item.step, item.z_mm, item.id))
    return placements


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
            f"重心最大偏差控制在 {metrics.weight_imbalance_pct}%",
            f"装入数量保持为 {metrics.loaded_pieces} 件",
        ]
        cons = [f"需要按 {metrics.loading_steps} 个货物栈执行装载"]
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
    stable_stacks = _repack_same_units(request, stable_units, "stable") or _center_stacks(request, high_stacks)
    easy_stacks = _repack_same_units(request, selected_units, "easy") or high_stacks

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

    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    request_id = hashlib.sha256(request_json.encode("utf-8")).hexdigest()[:12]
    return PackResponse(request_id=request_id, solutions=solutions)
