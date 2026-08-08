import pytest

from app.models import CargoSpec, ContainerSpec, Orientation, PackRequest
from app.packing import (
    CompositeUnit,
    PackedStack,
    StackUnit,
    _build_stack_units,
    _expand_stacks,
    _merge_pallet_cartons,
    _mixed_balance_layout,
    pack_order,
)
from app.validator import validate_solution


def mixed_container() -> ContainerSpec:
    return ContainerSpec(
        id="mixed",
        name="混装测试柜",
        inner_length_mm=6000,
        inner_width_mm=2400,
        inner_height_mm=2400,
        door_width_mm=2350,
        door_height_mm=2300,
        max_payload_g=10_000_000,
    )


def pallet_box(**overrides) -> CargoSpec:
    values = {
        "id": "pallet",
        "sku": "P-100",
        "name": "整托",
        "kind": "pallet",
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 1100,
        "weight_g": 400_000,
        "quantity": 1,
        "allowed_orientations": ["LWH"],
        "stackable": False,
        "max_layers": 1,
        "max_top_load_g": 500_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def carton_box(**overrides) -> CargoSpec:
    values = {
        "id": "carton",
        "sku": "C-200",
        "name": "散箱",
        "kind": "carton",
        "length_mm": 500,
        "width_mm": 400,
        "height_mm": 300,
        "weight_g": 20_000,
        "quantity": 4,
        "allowed_orientations": ["LWH", "WLH"],
        "stackable": True,
        "max_layers": 4,
        "max_top_load_g": 100_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def test_merge_pallet_cartons_creates_composite():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4)],
    )
    units = _build_stack_units(request)

    merged = _merge_pallet_cartons(request, units)

    composites = [unit for unit in merged if isinstance(unit, CompositeUnit)]
    assert composites, "可叠散箱应合并到可承重托盘上"
    composite = composites[0]
    assert composite.pallet.cargo.kind == "pallet"
    assert len(composite.on_top) >= 1
    assert composite.count == 5
    assert composite.total_weight_g == 400_000 + 4 * 20_000
    assert composite.length_mm == 1200 and composite.width_mm == 1000


def test_fragile_cartons_never_merge():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4, fragile=True)],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_zero_top_load_pallet_never_merges():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(max_top_load_g=0), carton_box(quantity=4)],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_carton_too_large_for_pallet_top_never_merges():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            carton_box(quantity=1, length_mm=1300, width_mm=1300, height_mm=500,
                       allowed_orientations=["LWH"]),
        ],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_pure_carton_or_pallet_order_unchanged():
    carton_request = PackRequest(
        container=mixed_container(),
        cargo_items=[carton_box(quantity=4)],
    )
    pallet_request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box()],
    )

    merged_cartons = _merge_pallet_cartons(
        carton_request, _build_stack_units(carton_request)
    )
    merged_pallets = _merge_pallet_cartons(
        pallet_request, _build_stack_units(pallet_request)
    )

    assert all(isinstance(unit, StackUnit) for unit in merged_cartons)
    assert all(isinstance(unit, StackUnit) for unit in merged_pallets)


def test_expand_composite_places_cartons_on_pallet_top():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4)],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))
    composite = next(unit for unit in merged if isinstance(unit, CompositeUnit))

    placements = _expand_stacks(
        request,
        [PackedStack(unit=composite, x_mm=0, y_mm=0, step=1)],
        "high_fill",
    )

    pallet_p = [p for p in placements if p.cargo_id == "pallet"]
    carton_p = [p for p in placements if p.cargo_id == "carton"]
    assert len(pallet_p) == 1
    assert len(carton_p) == 4
    assert pallet_p[0].z_mm == 0
    assert pallet_p[0].height_mm == 1100
    assert [p.z_mm for p in sorted(carton_p, key=lambda p: p.instance_index)] == [
        1100, 1400, 1700, 2000,
    ]
    assert all(p.step == 1 for p in placements)
    # 散箱件使用散箱自身尺寸与朝向（未旋转，LWH）
    assert all(p.length_mm == 500 for p in carton_p)
    assert all(p.width_mm == 400 for p in carton_p)
    assert all(p.rotation == Orientation.LWH for p in carton_p)


def test_expand_composite_multiple_stacks_do_not_overlap():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=8)],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))
    composite = next(unit for unit in merged if isinstance(unit, CompositeUnit))
    # 8 个散箱按 max_layers=4 拆成 2 个 on_top 栈（4 件 × 2 栈）
    assert len(composite.on_top) == 2

    placements = _expand_stacks(
        request,
        [PackedStack(unit=composite, x_mm=0, y_mm=0, step=1)],
        "high_fill",
    )

    carton_p = [p for p in placements if p.cargo_id == "carton"]
    assert len(carton_p) == 8
    for p in carton_p:
        # 每个栈内 4 件依次上叠，z = 托盘栈高 1100 + offset×300
        assert p.z_mm == 1100 + (p.instance_index % 4) * 300
        # 每件完整落在托盘顶面（1200×1000）内
        assert 0 <= p.x_mm <= 1200
        assert 0 <= p.y_mm <= 1000
        assert p.x_mm + p.length_mm <= 1200
        assert p.y_mm + p.width_mm <= 1000
    # (x, y, z) 两两不同 → 互不重叠
    assert len({(p.x_mm, p.y_mm, p.z_mm) for p in carton_p}) == 8
    # 至少两个不同平面位置 → 托盘顶面内偏移已生效
    assert len({(p.x_mm, p.y_mm) for p in carton_p}) >= 2


def test_mixed_balance_layout_centers_pallets():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    layout = _mixed_balance_layout(request, merged)

    assert layout is not None
    pallet_stacks = [s for s in layout if s.unit.cargo.kind == "pallet"]
    assert len(pallet_stacks) == 2
    total = sum(s.unit.total_weight_g for s in pallet_stacks)
    cg_x = (
        sum((s.x_mm + s.length_mm / 2) * s.unit.total_weight_g for s in pallet_stacks)
        / total
    )
    assert mixed_container().inner_length_mm / 3 <= cg_x <= mixed_container().inner_length_mm * 2 / 3


def test_mixed_balance_layout_passes_validator():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    layout = _mixed_balance_layout(request, merged)

    assert layout is not None
    placements = _expand_stacks(request, layout, "stable")
    result = validate_solution(
        request.container, request.cargo_items, placements, request.item_gap_mm
    )
    assert result.valid, [error.code for error in result.errors]


def test_mixed_balance_layout_returns_none_for_pure_carton():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[carton_box(quantity=4)],
    )

    layout = _mixed_balance_layout(request, _build_stack_units(request))

    assert layout is None


def test_mixed_order_pallets_on_bottom_cartons_on_top():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[pallet_box(must_load=True), carton_box(quantity=8)],
    )

    response = pack_order(request)

    assert response.solutions[0].loaded_counts == {"pallet": 1, "carton": 8}
    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert pallet_p
        pallet_top = pallet_p[0].z_mm + pallet_p[0].height_mm
        assert any(p.z_mm == pallet_top for p in carton_p), "散箱应叠在整托顶面"


def test_stable_keeps_high_fill_piece_count_and_centers_pallets():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )

    response = pack_order(request)

    high_fill = response.solutions[0]
    stable = response.solutions[1]
    assert stable.metrics.loaded_pieces == high_fill.metrics.loaded_pieces
    pallet_p = [p for p in stable.placements if p.cargo_id == "pallet"]
    total = sum(p.weight_g for p in pallet_p)
    cg_x = sum((p.x_mm + p.length_mm / 2) * p.weight_g for p in pallet_p) / total
    assert container.inner_length_mm / 3 <= cg_x <= container.inner_length_mm * 2 / 3


def test_mixed_order_partial_pallet_top_loading():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200"),
            carton_box(quantity=40),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"pallet": 1, "pallet2": 1, "carton": 40}
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert len(carton_p) == 40
        pallet_top = pallet_p[0].z_mm + pallet_p[0].height_mm
        on_pallet = [p for p in carton_p if p.z_mm == pallet_top]
        on_floor = [p for p in carton_p if p.z_mm == container.clearance_mm]
        assert on_pallet, "应有散箱叠在托盘顶面"
        assert on_floor, "散箱放不下时应作为独立栈放在柜底"


def test_mixed_order_with_item_gap_valid():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(must_load=True),
            pallet_box(id="pallet2", sku="P-200"),
            carton_box(quantity=40),
        ],
        item_gap_mm=10,
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"pallet": 1, "pallet2": 1, "carton": 40}
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert len(carton_p) == 40
        pallet_top = pallet_p[0].z_mm + pallet_p[0].height_mm
        on_pallet = [p for p in carton_p if p.z_mm == pallet_top]
        on_floor = [p for p in carton_p if p.z_mm == container.clearance_mm]
        assert on_pallet, "应有散箱叠在托盘顶面"
        assert on_floor, "应有独立散箱栈放在柜底"
