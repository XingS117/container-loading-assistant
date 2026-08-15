from app.models import CargoSpec, ContainerSpec, Placement
from app.validator import validate_solution


def container(**overrides) -> ContainerSpec:
    values = {
        "id": "test",
        "name": "测试柜",
        "inner_length_mm": 6000,
        "inner_width_mm": 2400,
        "inner_height_mm": 2400,
        "door_width_mm": 2350,
        "door_height_mm": 2300,
        "max_payload_g": 28_000_000,
        "clearance_mm": 0,
    }
    values.update(overrides)
    return ContainerSpec(**values)


def cargo(cargo_id: str = "A", **overrides) -> CargoSpec:
    values = {
        "id": cargo_id,
        "sku": cargo_id,
        "name": f"货物 {cargo_id}",
        "kind": "carton",
        "length_mm": 1000,
        "width_mm": 1000,
        "height_mm": 1000,
        "weight_g": 100_000,
        "quantity": 2,
        "allowed_orientations": ["LWH"],
        "stackable": True,
        "max_layers": 2,
        "max_top_load_g": 500_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def placement(
    placement_id: str,
    cargo_id: str = "A",
    instance_index: int = 0,
    **overrides,
) -> Placement:
    values = {
        "id": placement_id,
        "cargo_id": cargo_id,
        "instance_index": instance_index,
        "x_mm": 0,
        "y_mm": 0,
        "z_mm": 0,
        "length_mm": 1000,
        "width_mm": 1000,
        "height_mm": 1000,
        "rotation": "LWH",
        "weight_g": 100_000,
        "step": 1,
    }
    values.update(overrides)
    return Placement(**values)


def error_codes(result) -> set[str]:
    return {error.code for error in result.errors}


def test_accepts_valid_floor_layout():
    items = [cargo()]
    placements = [
        placement("A-0"),
        placement("A-1", instance_index=1, x_mm=1000),
    ]

    result = validate_solution(container(), items, placements)

    assert result.valid is True
    assert result.errors == []


def test_rejects_out_of_bounds_and_overlap():
    items = [cargo(quantity=3)]
    placements = [
        placement("A-0"),
        placement("A-1", instance_index=1, x_mm=500),
        placement("A-2", instance_index=2, x_mm=5500),
    ]

    result = validate_solution(container(), items, placements)

    assert {"OVERLAP", "OUT_OF_BOUNDS"}.issubset(error_codes(result))


def test_rejects_disallowed_orientation_and_door_fit():
    item = cargo(
        length_mm=2400,
        width_mm=1000,
        height_mm=2200,
        quantity=1,
        allowed_orientations=["LWH"],
    )
    placed = placement(
        "A-0",
        length_mm=1000,
        width_mm=2400,
        height_mm=2200,
        rotation="WLH",
    )

    result = validate_solution(container(), [item], [placed])

    assert {"ORIENTATION_NOT_ALLOWED", "DOOR_FIT"}.issubset(error_codes(result))


def test_rejects_payload_overweight():
    item = cargo(quantity=2, weight_g=600_000)
    placements = [
        placement("A-0", weight_g=600_000),
        placement("A-1", instance_index=1, x_mm=1000, weight_g=600_000),
    ]

    result = validate_solution(
        container(max_payload_g=1_000_000), item and [item], placements
    )

    assert "PAYLOAD_EXCEEDED" in error_codes(result)


def test_requires_full_support_for_elevated_item():
    items = [cargo(quantity=2)]
    placements = [
        placement("A-0"),
        placement("A-1", instance_index=1, x_mm=500, z_mm=1000),
    ]

    result = validate_solution(container(), items, placements)

    assert "UNSUPPORTED" in error_codes(result)


def test_rejects_stacking_on_fragile_or_non_stackable_item():
    bottom = cargo("A", quantity=1, fragile=True, stackable=False)
    top = cargo("B", quantity=1)
    placements = [
        placement("A-0", cargo_id="A"),
        placement("B-0", cargo_id="B", z_mm=1000),
    ]

    result = validate_solution(container(), [bottom, top], placements)

    assert {"NON_STACKABLE", "FRAGILE_STACKING"}.issubset(error_codes(result))


def test_rejects_max_layers_and_top_load():
    bottom = cargo(
        "A",
        quantity=1,
        max_layers=1,
        max_top_load_g=50_000,
    )
    top = cargo("B", quantity=1, weight_g=100_000)
    placements = [
        placement("A-0", cargo_id="A"),
        placement("B-0", cargo_id="B", z_mm=1000),
    ]

    result = validate_solution(container(), [bottom, top], placements)

    assert {"MAX_LAYERS_EXCEEDED", "TOP_LOAD_EXCEEDED"}.issubset(
        error_codes(result)
    )


def test_rejects_duplicate_instances_and_unknown_cargo():
    items = [cargo(quantity=1)]
    placements = [
        placement("A-0"),
        placement("A-0-copy"),
        placement("X-0", cargo_id="X", x_mm=2000),
    ]

    result = validate_solution(container(), items, placements)

    assert {"DUPLICATE_INSTANCE", "UNKNOWN_CARGO"}.issubset(error_codes(result))


def test_enforces_horizontal_item_gap_without_separating_stack_layers():
    item = cargo(quantity=2, length_mm=500, width_mm=500, height_mm=500)
    placements = [
        placement("A-0", length_mm=500, width_mm=500, height_mm=500),
        placement("A-1", instance_index=1, x_mm=505, length_mm=500, width_mm=500, height_mm=500),
    ]

    result = validate_solution(container(), [item], placements, item_gap_mm=10)

    assert "CLEARANCE_VIOLATION" in error_codes(result)


def test_accepts_cartons_on_top_of_pallet():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=1_000_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    carton_item = cargo(
        "B", quantity=1, length_mm=500, width_mm=400, height_mm=300,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("B-0", cargo_id="B", z_mm=1000, length_mm=500, width_mm=400, height_mm=300),
    ]

    result = validate_solution(container(), [pallet_item, carton_item], placements)

    assert result.valid is True
    assert result.errors == []


def test_rejects_pallet_stacked_on_pallet():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=1_000_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    pallet2 = cargo(
        "Q", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=0, length_mm=1000, width_mm=1000, height_mm=1000,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("Q-0", cargo_id="Q", z_mm=1000),
    ]

    result = validate_solution(container(), [pallet_item, pallet2], placements)

    assert "PALLET_STACKING" in error_codes(result)


def test_rejects_different_pallet_specs_stacked_on_each_other():
    bottom = cargo(
        "P",
        kind="pallet",
        quantity=1,
        stackable=True,
        max_layers=2,
        max_top_load_g=1_000_000,
        length_mm=1200,
        width_mm=1000,
        height_mm=1000,
    )
    top = cargo(
        "Q",
        kind="pallet",
        quantity=1,
        stackable=True,
        max_layers=2,
        max_top_load_g=1_000_000,
        length_mm=1000,
        width_mm=1000,
        height_mm=1000,
    )
    placements = [
        placement(
            "P-0",
            cargo_id="P",
            length_mm=1200,
            width_mm=1000,
        ),
        placement(
            "Q-0",
            cargo_id="Q",
            z_mm=1000,
            length_mm=1000,
            width_mm=1000,
        ),
    ]

    result = validate_solution(container(), [bottom, top], placements)

    assert "STACKING_SPEC_MISMATCH" in error_codes(result)


def test_rejects_cartons_exceeding_pallet_top_load():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=50_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    carton_item = cargo(
        "B", quantity=1, weight_g=100_000, length_mm=500, width_mm=400, height_mm=300,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("B-0", cargo_id="B", z_mm=1000, length_mm=500, width_mm=400, height_mm=300),
    ]

    result = validate_solution(container(), [pallet_item, carton_item], placements)

    assert "TOP_LOAD_EXCEEDED" in error_codes(result)
