import pytest

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import (
    CompositeUnit,
    PackedStack,
    StackUnit,
    _build_stack_units,
    _expand_stacks,
    _merge_pallet_cartons,
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
