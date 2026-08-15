# -*- coding: utf-8 -*-
"""改造回归：X/Y 场景三目标行为、三条硬原则、_layer_layout 柱高数学、
_shelf_layout 排内纯净化。"""
from collections import Counter, defaultdict

import pytest

from app.models import CargoSpec, ContainerSpec, Orientation, Placement, PackRequest
from app.packing import (
    _build_stack_units,
    _expand_stacks,
    _layer_layout,
    _merge_pallet_cartons,
    _shelf_layout,
    pack_order,
)
from app.validator import validate_solution

CONTAINER = ContainerSpec(
    id="40hq",
    name="40HQ",
    inner_length_mm=12032,
    inner_width_mm=2352,
    inner_height_mm=2698,
    door_width_mm=2340,
    door_height_mm=2585,
    max_payload_g=28_600_000,
)


def box(pid, sku, L, W, H, qty, layers, top, weight):
    return CargoSpec(
        id=pid, sku=sku, name=sku, kind="carton",
        length_mm=L, width_mm=W, height_mm=H,
        weight_g=weight, quantity=qty,
        allowed_orientations=["LWH", "LHW", "WLH", "WHL"], stackable=True,
        max_layers=layers, max_top_load_g=top,
    )


def xy_request() -> PackRequest:
    """X/Y 散箱场景（两个 bug 的精确复现参数）。"""
    return PackRequest(
        container=CONTAINER,
        door_buffer_mm=300,
        item_gap_mm=0,
        cargo_items=[
            box("x", "X", 600, 400, 350, 400, 5, 200_000, 20_000),
            box("y", "Y", 450, 350, 300, 300, 7, 150_000, 10_000),
        ],
    )


def top_center_deviation(solution) -> float:
    """顶层（最高层）货物质心距柜长中心的距离（mm）。"""
    placements = solution.placements
    top_z = max(p.z_mm for p in placements)
    top = [p for p in placements if p.z_mm == top_z]
    mean_x = sum(p.x_mm + p.length_mm / 2 for p in top) / len(top)
    return abs(mean_x - CONTAINER.inner_length_mm / 2)


def test_xy_high_fill_loads_all_700_pieces(pack_by_goal):
    """Bug 1 基线：high_fill 在 X/Y 场景应全装 700 件、2 区 2 步。"""
    solution = pack_by_goal(xy_request(), "high_fill")

    assert solution.metrics.loaded_pieces == 700
    assert solution.loaded_counts == {"x": 400, "y": 300}
    assert solution.metrics.cargo_zones == 2
    assert solution.metrics.loading_steps == 2


def test_xy_stable_conserves_per_sku_counts_and_centers_top_layer(pack_by_goal):
    """Bug 1 修复：stable 逐 SKU 与 high 相等（不丢件），
    顶层集中中间、前后偏差显著小于 high。"""
    request = xy_request()
    high = pack_by_goal(request, "high_fill")
    stable = pack_by_goal(request, "stable")

    assert stable.loaded_counts == high.loaded_counts
    assert stable.metrics.loaded_pieces == 700
    assert top_center_deviation(stable) <= 1500
    assert stable.metrics.length_imbalance_pct <= 5
    # 装载步骤不得碎片化（步骤 2.5：同 SKU 连续段分组）
    assert stable.metrics.loading_steps <= 4
    assert stable.metrics.cargo_zones <= 4


def test_xy_easy_keeps_all_pieces_with_few_zones(pack_by_goal):
    """Bug 2 修复：easy 在 X/Y 场景全装 700 件且 ≤4 区 ≤4 步。"""
    solution = pack_by_goal(xy_request(), "easy")

    assert solution.metrics.loaded_pieces == 700
    assert solution.loaded_counts == {"x": 400, "y": 300}
    assert solution.metrics.cargo_zones <= 4
    assert solution.metrics.loading_steps <= 4


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_xy_three_principles_hold(pack_by_goal, goal):
    """三条硬原则（所有目标生效）：

    1. 底层先铺满：上层件全部落在已铺底层位正上方（无悬空、无缺底叠高）
    2. 同规格参数才可以叠放：每个上层件正下方支撑件与自身同 SKU
    3. 上层集中中间：顶层质心距柜长中心 ≤ 1500mm
    """
    request = xy_request()
    solution = pack_by_goal(request, goal)

    result = validate_solution(
        request.container, request.cargo_items, solution.placements
    )
    assert result.valid, [issue.code for issue in result.errors]

    floor_columns = {
        (p.x_mm, p.y_mm)
        for p in solution.placements
        if p.z_mm == request.container.clearance_mm
    }
    upper = [p for p in solution.placements if p.z_mm > request.container.clearance_mm]
    assert upper, "应有叠高层"
    # 原则 1：上层列全部有底层件（先铺满底层才叠高）
    assert all((p.x_mm, p.y_mm) in floor_columns for p in upper), (
        f"{goal} 上层存在无底层支撑的列"
    )
    # 原则 2：同规格叠放——同一列内所有件同 SKU、同 footprint
    for column_x, column_y in floor_columns:
        column = [
            p
            for p in solution.placements
            if (p.x_mm, p.y_mm) == (column_x, column_y)
        ]
        skus = {p.cargo_id for p in column}
        footprints = {(p.length_mm, p.width_mm) for p in column}
        assert len(skus) == 1 and len(footprints) == 1, (
            f"{goal} 同一列混叠不同规格: {skus} {footprints}"
        )
    # 原则 3：上层集中中间
    assert top_center_deviation(solution) <= 1500, (
        f"{goal} 顶层质心偏离柜长中心 {top_center_deviation(solution):.0f}mm"
    )


def test_layer_layout_column_math_overflow_is_explicit():
    """1a 修复：底位不足（k × capacity < total）时溢出件显式不装，
    所有列装满到容量，不产生残缺列。"""
    container = ContainerSpec(
        id="tiny",
        name="小柜",
        inner_length_mm=2200,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=10_000_000,
    )
    cargo = CargoSpec(
        id="ca", sku="CA", name="散箱", kind="carton",
        length_mm=500, width_mm=400, height_mm=250, weight_g=10_000,
        quantity=60, allowed_orientations=["LWH"], stackable=True,
        max_layers=3, max_top_load_g=100_000,
    )
    request = PackRequest(container=container, cargo_items=[cargo])
    units = _build_stack_units(request)
    merged = _merge_pallet_cartons(request, units)

    stacks = _layer_layout(request, merged)

    assert stacks is not None
    placements = _expand_stacks(request, stacks, "stable")
    columns: dict[tuple[int, int], int] = Counter(
        (p.x_mm, p.y_mm) for p in placements
    )
    # 8 个底位 × 3 层容量 = 24 件；溢出 36 件显式不装
    assert sum(columns.values()) == 24
    assert len(columns) == 8
    assert set(columns.values()) == {3}
    assert validate_solution(container, [cargo], placements).valid


def test_layer_layout_extra_layers_concentrate_near_length_center():
    """1a 修复：多余 +1 层优先加在距柜长中心近的底位（上层集中中间）。"""
    container = ContainerSpec(
        id="tiny",
        name="小柜",
        inner_length_mm=2200,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=10_000_000,
    )
    cargo = CargoSpec(
        id="ca", sku="CA", name="散箱", kind="carton",
        length_mm=500, width_mm=400, height_mm=250, weight_g=10_000,
        quantity=19, allowed_orientations=["LWH"], stackable=True,
        max_layers=3, max_top_load_g=100_000,
    )
    request = PackRequest(container=container, cargo_items=[cargo])
    units = _build_stack_units(request)
    merged = _merge_pallet_cartons(request, units)

    stacks = _layer_layout(request, merged)

    assert stacks is not None
    placements = _expand_stacks(request, stacks, "stable")
    columns: dict[tuple[int, int], list[Placement]] = defaultdict(list)
    for p in placements:
        columns[(p.x_mm, p.y_mm)].append(p)
    heights = {column: len(items) for column, items in columns.items()}
    assert sum(heights.values()) == 19
    # 柱高相差不超过 1（base + 最多 1 层）
    assert max(heights.values()) - min(heights.values()) <= 1
    # +1 层集中在距柜长中心近的底位：高柱平均偏离 < 矮柱平均偏离
    taller = [column for column, height in heights.items() if height == max(heights.values())]
    shorter = [column for column, height in heights.items() if height == min(heights.values())]
    center = 2200 / 2

    def mean_dev(columns):
        return sum(
            abs(x + 500 / 2 - center) for x, _ in columns
        ) / len(columns)

    assert mean_dev(taller) < mean_dev(shorter)


def test_shelf_layout_keeps_rows_single_sku_and_shared_steps():
    """1d 增强：shelf 排内 SKU 纯净化——每排只装同 SKU，
    同 SKU 连续排共用装载步。"""
    container = ContainerSpec(
        id="shelf",
        name="货架测试柜",
        inner_length_mm=3000,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=10_000_000,
    )

    def carton(pid, sku, length, qty):
        return CargoSpec(
            id=pid, sku=sku, name=sku, kind="carton",
            length_mm=length, width_mm=300, height_mm=250, weight_g=10_000,
            quantity=qty, allowed_orientations=["LWH"], stackable=True,
            max_layers=1, max_top_load_g=100_000,
        )

    request = PackRequest(
        container=container,
        cargo_items=[
            carton("a", "A", 500, 4),
            carton("b", "B", 400, 6),
        ],
    )
    stacks = _shelf_layout(request, _build_stack_units(request))

    assert stacks is not None
    rows: dict[int, list] = defaultdict(list)
    for stack in stacks:
        rows[stack.x_mm].append(stack)
    # 每排只含一个 SKU
    for row_stacks in rows.values():
        skus = {stack.unit.cargo.id for stack in row_stacks}
        assert len(skus) == 1, f"排内混 SKU: {skus}"
    # 同 SKU 连续排共用 step（A 两排 = 1 步，B 两排 = 1 步）
    row_steps = [
        next(iter({stack.step for stack in row_stacks}))
        for _, row_stacks in sorted(rows.items())
    ]
    assert row_steps == [1, 1, 2, 2], row_steps


def test_validator_rejects_different_spec_pallet_stacking():
    """原则 2：validator 拒绝不同规格参数的整托相互叠放。"""
    container = ContainerSpec(
        id="tall",
        name="高柜",
        inner_length_mm=2000,
        inner_width_mm=2000,
        inner_height_mm=4000,
        door_width_mm=2000,
        door_height_mm=4000,
        max_payload_g=10_000_000,
    )
    big = CargoSpec(
        id="big", sku="BIG", name="大托", kind="pallet",
        length_mm=1200, width_mm=1000, height_mm=1100, weight_g=400_000,
        quantity=1, allowed_orientations=["LWH"], stackable=True,
        max_layers=2, max_top_load_g=500_000,
    )
    small = CargoSpec(
        id="small", sku="SMALL", name="小托", kind="pallet",
        length_mm=600, width_mm=500, height_mm=900, weight_g=100_000,
        quantity=1, allowed_orientations=["LWH"], stackable=True,
        max_layers=2, max_top_load_g=500_000,
    )
    placements = [
        Placement(
            id="big-0", cargo_id="big", instance_index=0,
            x_mm=0, y_mm=0, z_mm=0,
            length_mm=1200, width_mm=1000, height_mm=1100,
            rotation=Orientation.LWH, weight_g=400_000, step=1,
        ),
        Placement(
            id="small-0", cargo_id="small", instance_index=0,
            x_mm=0, y_mm=0, z_mm=1100,
            length_mm=600, width_mm=500, height_mm=900,
            rotation=Orientation.LWH, weight_g=100_000, step=2,
        ),
    ]

    result = validate_solution(container, [big, small], placements)

    assert not result.valid
    assert any(
        issue.code == "STACKING_SPEC_MISMATCH" for issue in result.errors
    ), [issue.code for issue in result.errors]


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_request_id_identifies_goal(goal):
    """optimization_goal 参与 request_id：同请求不同目标 → 不同 request_id。"""
    request = xy_request()

    response = pack_order(request.model_copy(update={"optimization_goal": goal}))
    other = pack_order(request.model_copy(update={"optimization_goal": "high_fill"}))
    if goal != "high_fill":
        assert response.request_id != other.request_id
    else:
        assert response.request_id == other.request_id
