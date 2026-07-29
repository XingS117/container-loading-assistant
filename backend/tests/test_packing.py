import pytest

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import PackingFailure, pack_order
from app.validator import validate_solution


def small_container() -> ContainerSpec:
    return ContainerSpec(
        id="small",
        name="小型测试柜",
        inner_length_mm=2000,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=1_000_000,
        clearance_mm=0,
    )


def boxes(quantity: int = 2, **overrides) -> CargoSpec:
    values = {
        "id": "box-a",
        "sku": "A-100",
        "name": "标准箱",
        "kind": "carton",
        "length_mm": 1000,
        "width_mm": 1000,
        "height_mm": 1000,
        "weight_g": 100_000,
        "quantity": quantity,
        "allowed_orientations": ["LWH"],
        "stackable": False,
        "max_layers": 1,
        "max_top_load_g": 0,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def test_returns_three_deterministic_valid_solutions():
    request = PackRequest(container=small_container(), cargo_items=[boxes()])

    first = pack_order(request)
    second = pack_order(request)

    assert [solution.profile for solution in first.solutions] == [
        "high_fill",
        "stable",
        "easy",
    ]
    assert first.model_dump() == second.model_dump()
    for solution in first.solutions:
        assert solution.loaded_counts == {"box-a": 2}
        assert solution.unloaded_counts == {"box-a": 0}
        assert solution.metrics.loaded_pieces == 2
        assert validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
        ).valid


def test_reports_unloaded_quantity_when_order_exceeds_container():
    request = PackRequest(container=small_container(), cargo_items=[boxes(quantity=3)])

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"box-a": 2}
        assert solution.unloaded_counts == {"box-a": 1}


def test_rejects_order_when_must_load_cargo_cannot_fit():
    request = PackRequest(
        container=small_container(),
        cargo_items=[boxes(quantity=3, must_load=True)],
    )

    with pytest.raises(PackingFailure, match="必装") as exc_info:
        pack_order(request)

    assert exc_info.value.code == "MUST_LOAD_UNSATISFIED"


def test_packs_cartons_and_whole_pallets_together():
    container = ContainerSpec(
        id="mixed",
        name="混装测试柜",
        inner_length_mm=3000,
        inner_width_mm=2000,
        inner_height_mm=2000,
        door_width_mm=2000,
        door_height_mm=2000,
        max_payload_g=5_000_000,
    )
    carton = boxes(
        quantity=4,
        length_mm=500,
        width_mm=500,
        height_mm=500,
        allowed_orientations=["LWH", "WLH"],
        stackable=True,
        max_layers=2,
        max_top_load_g=100_000,
    )
    pallet = boxes(
        id="pallet-b",
        sku="P-200",
        name="整托",
        kind="pallet",
        quantity=1,
        length_mm=1200,
        width_mm=1000,
        height_mm=1200,
        weight_g=500_000,
    )
    request = PackRequest(container=container, cargo_items=[carton, pallet])

    response = pack_order(request)

    assert response.solutions[0].loaded_counts == {"box-a": 4, "pallet-b": 1}
    assert all(
        validate_solution(container, request.cargo_items, solution.placements).valid
        for solution in response.solutions
    )


def test_uses_mixed_horizontal_orientations_for_the_same_sku():
    container = ContainerSpec(
        id="rotation",
        name="混合朝向测试柜",
        inner_length_mm=1000,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=1_000_000,
    )
    item = boxes(
        quantity=3,
        length_mm=600,
        width_mm=400,
        height_mm=1000,
        allowed_orientations=["LWH", "WLH"],
    )

    response = pack_order(PackRequest(container=container, cargo_items=[item]))

    assert response.solutions[0].loaded_counts["box-a"] == 3
    assert {placement.rotation.value for placement in response.solutions[0].placements} == {"LWH", "WLH"}


def test_rotatable_cargo_still_rotates_when_another_sku_has_fixed_orientation():
    container = ContainerSpec(
        id="per-cargo-rotation",
        name="逐货物旋转测试柜",
        inner_length_mm=1000,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=1_000_000,
    )
    fixed = boxes(
        id="fixed",
        sku="FIXED",
        quantity=1,
        length_mm=200,
        width_mm=1000,
        height_mm=1000,
        must_load=True,
    )
    rotatable = boxes(
        id="rotatable",
        sku="ROTATABLE",
        quantity=3,
        length_mm=600,
        width_mm=400,
        height_mm=1000,
        allowed_orientations=["LWH", "WLH"],
    )

    response = pack_order(PackRequest(container=container, cargo_items=[fixed, rotatable]))

    assert response.solutions[0].loaded_counts == {"fixed": 1, "rotatable": 3}
    assert {placement.rotation.value for placement in response.solutions[0].placements if placement.cargo_id == "rotatable"} == {"LWH", "WLH"}


def test_considers_lighter_combination_instead_of_pretrimming_by_volume():
    container = ContainerSpec(
        id="weight-choice",
        name="载重组合测试柜",
        inner_length_mm=1000,
        inner_width_mm=1000,
        inner_height_mm=1000,
        door_width_mm=1000,
        door_height_mm=1000,
        max_payload_g=100_000,
    )
    heavy = boxes(
        id="heavy",
        sku="HEAVY",
        quantity=1,
        length_mm=700,
        width_mm=700,
        height_mm=1000,
        weight_g=80_000,
    )
    light = boxes(
        id="light",
        sku="LIGHT",
        quantity=2,
        length_mm=500,
        width_mm=500,
        height_mm=1000,
        weight_g=20_000,
    )

    response = pack_order(PackRequest(container=container, cargo_items=[heavy, light]))

    assert response.solutions[0].loaded_counts == {"heavy": 0, "light": 2}


def test_stable_solution_can_lower_vertical_center_of_gravity():
    container = ContainerSpec(
        id="vertical-balance",
        name="垂直重心测试柜",
        inner_length_mm=1200,
        inner_width_mm=800,
        inner_height_mm=1000,
        door_width_mm=800,
        door_height_mm=1000,
        max_payload_g=1_000_000,
    )
    item = boxes(
        quantity=5,
        length_mm=600,
        width_mm=400,
        height_mm=250,
        allowed_orientations=["LWH", "LHW"],
        stackable=True,
        max_layers=5,
        max_top_load_g=500_000,
    )

    response = pack_order(PackRequest(container=container, cargo_items=[item]))

    high_fill, stable = response.solutions[:2]
    assert stable.loaded_counts == high_fill.loaded_counts
    assert stable.metrics.center_of_gravity.z_mm < high_fill.metrics.center_of_gravity.z_mm
