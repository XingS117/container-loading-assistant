from __future__ import annotations

from collections import Counter, defaultdict

from .models import (
    CargoSpec,
    ContainerSpec,
    Placement,
    ValidationIssue,
    ValidationResult,
)


def _intersects(a: Placement, b: Placement) -> bool:
    return (
        a.x_mm < b.x_mm + b.length_mm
        and a.x_mm + a.length_mm > b.x_mm
        and a.y_mm < b.y_mm + b.width_mm
        and a.y_mm + a.width_mm > b.y_mm
        and a.z_mm < b.z_mm + b.height_mm
        and a.z_mm + a.height_mm > b.z_mm
    )


def _base_intersection_area(a: Placement, b: Placement) -> int:
    overlap_x = max(
        0,
        min(a.x_mm + a.length_mm, b.x_mm + b.length_mm)
        - max(a.x_mm, b.x_mm),
    )
    overlap_y = max(
        0,
        min(a.y_mm + a.width_mm, b.y_mm + b.width_mm)
        - max(a.y_mm, b.y_mm),
    )
    return overlap_x * overlap_y


def validate_solution(
    container: ContainerSpec,
    cargo_items: list[CargoSpec],
    placements: list[Placement],
    item_gap_mm: int = 0,
) -> ValidationResult:
    """校验装柜方案。

    高层货物必须得到 100% 完整支撑，不允许悬挑（与 packing 层同规格叠放
    约束一致）。
    """
    errors: list[ValidationIssue] = []
    cargo_by_id = {item.id: item for item in cargo_items}
    known: list[Placement] = []
    seen_instances: set[tuple[str, int]] = set()
    loaded_counts: Counter[str] = Counter()

    def add(code: str, message: str, *placement_ids: str) -> None:
        errors.append(
            ValidationIssue(
                code=code,
                message=message,
                placement_ids=list(placement_ids),
            )
        )

    c = container.clearance_mm
    for placed in placements:
        item = cargo_by_id.get(placed.cargo_id)
        if item is None:
            add("UNKNOWN_CARGO", "布局引用了不存在的货物", placed.id)
            continue

        instance_key = (placed.cargo_id, placed.instance_index)
        if instance_key in seen_instances:
            add("DUPLICATE_INSTANCE", "同一件货物被重复放置", placed.id)
        seen_instances.add(instance_key)
        if placed.instance_index >= item.quantity:
            add("INSTANCE_OUT_OF_RANGE", "货物序号超过输入数量", placed.id)

        loaded_counts[item.id] += 1
        known.append(placed)

        expected_dims = item.dimensions_for(placed.rotation)
        actual_dims = (placed.length_mm, placed.width_mm, placed.height_mm)
        if placed.rotation not in item.allowed_orientations:
            add("ORIENTATION_NOT_ALLOWED", "货物使用了未允许的朝向", placed.id)
        if actual_dims != expected_dims:
            add("DIMENSIONS_MISMATCH", "布局尺寸与货物朝向不一致", placed.id)
        if placed.weight_g != item.weight_g:
            add("WEIGHT_MISMATCH", "布局重量与货物单重不一致", placed.id)

        if (
            placed.x_mm < c
            or placed.y_mm < c
            or placed.z_mm < c
            or placed.x_mm + placed.length_mm > container.inner_length_mm - c
            or placed.y_mm + placed.width_mm > container.inner_width_mm - c
            or placed.z_mm + placed.height_mm > container.inner_height_mm - c
        ):
            add("OUT_OF_BOUNDS", "货物超出柜体有效边界", placed.id)

        if (
            placed.width_mm > container.door_width_mm - 2 * c
            or placed.height_mm > container.door_height_mm - 2 * c
        ):
            add("DOOR_FIT", "货物按当前朝向无法通过柜门", placed.id)

    above_by_id: dict[str, list[Placement]] = defaultdict(list)
    supporters_by_id: dict[str, list[Placement]] = defaultdict(list)
    ordered_by_x = sorted(known, key=lambda placed: (placed.x_mm, placed.y_mm, placed.z_mm, placed.id))
    for index, first in enumerate(ordered_by_x):
        comparison_limit = first.x_mm + first.length_mm + item_gap_mm
        for second in ordered_by_x[index + 1 :]:
            if second.x_mm >= comparison_limit:
                break
            if _intersects(first, second):
                add("OVERLAP", "货物发生空间重叠", first.id, second.id)
                continue
            z_overlaps = (
                first.z_mm < second.z_mm + second.height_mm
                and first.z_mm + first.height_mm > second.z_mm
            )
            if item_gap_mm > 0 and z_overlaps:
                separation_x = max(
                    0,
                    second.x_mm - (first.x_mm + first.length_mm),
                    first.x_mm - (second.x_mm + second.length_mm),
                )
                separation_y = max(
                    0,
                    second.y_mm - (first.y_mm + first.width_mm),
                    first.y_mm - (second.y_mm + second.width_mm),
                )
                if separation_x**2 + separation_y**2 < item_gap_mm**2:
                    add("CLEARANCE_VIOLATION", "同层货物间隙小于设置值", first.id, second.id)

            base_area = _base_intersection_area(first, second)
            if base_area <= 0:
                continue
            first_top = first.z_mm + first.height_mm
            second_top = second.z_mm + second.height_mm
            if second.z_mm >= first_top:
                above_by_id[first.id].append(second)
                if second.z_mm == first_top:
                    supporters_by_id[second.id].append(first)
            elif first.z_mm >= second_top:
                above_by_id[second.id].append(first)
                if first.z_mm == second_top:
                    supporters_by_id[first.id].append(second)

    total_weight = sum(cargo_by_id[p.cargo_id].weight_g for p in known)
    if total_weight > container.max_payload_g:
        add("PAYLOAD_EXCEEDED", "装载总重量超过柜体最大载重")

    for item in cargo_items:
        if item.must_load and loaded_counts[item.id] < item.quantity:
            add("MUST_LOAD_MISSING", f"必装货物 {item.sku} 未全部装入")

    for placed in known:
        if placed.z_mm <= c:
            continue

        supporters = supporters_by_id[placed.id]
        support_area = sum(_base_intersection_area(placed, other) for other in supporters)
        required_area = placed.length_mm * placed.width_mm
        if support_area < required_area:
            add("UNSUPPORTED", "高层货物底面未得到完整支撑", placed.id)

    for support in known:
        item = cargo_by_id[support.cargo_id]
        above = above_by_id[support.id]
        if not above:
            continue

        if item.kind == "pallet":
            pallet_above = [
                other for other in above if cargo_by_id[other.cargo_id].kind == "pallet"
            ]
            if pallet_above and not item.stackable:
                add(
                    "PALLET_STACKING",
                    "整托上方不能叠放整托（请开启该整托的“可叠”选项）",
                    support.id,
                )
            if pallet_above:
                support_spec = (
                    item.length_mm,
                    item.width_mm,
                    item.height_mm,
                    item.weight_g,
                )
                for other in pallet_above:
                    above_item = cargo_by_id[other.cargo_id]
                    above_spec = (
                        above_item.length_mm,
                        above_item.width_mm,
                        above_item.height_mm,
                        above_item.weight_g,
                    )
                    if above_spec != support_spec:
                        add(
                            "STACKING_SPEC_MISMATCH",
                            "只有相同规格参数的整托货物才能相互叠放",
                            support.id,
                            other.id,
                        )
                # 整托叠放层数受 max_layers 约束；散箱层数由散箱自身校验（不在此限制）
                pallet_levels = 1 + len({other.z_mm for other in pallet_above})
                if pallet_levels > item.max_layers:
                    add("MAX_LAYERS_EXCEEDED", "整托叠放层数超过限制", support.id)
        else:
            if not item.stackable:
                add("NON_STACKABLE", "不可叠放货物上方存在其他货物", support.id)
            if item.fragile:
                add("FRAGILE_STACKING", "易碎货物上方存在其他货物", support.id)
            levels = 1 + len({other.z_mm for other in above})
            if levels > item.max_layers:
                add("MAX_LAYERS_EXCEEDED", "货物堆叠层数超过限制", support.id)

        top_load = sum(cargo_by_id[other.cargo_id].weight_g for other in above)
        if top_load > item.max_top_load_g:
            add("TOP_LOAD_EXCEEDED", "货物顶部承重超过限制", support.id)

    return ValidationResult(valid=not errors, errors=errors)
