from collections import Counter
from dataclasses import replace

import pytest

from app.models import CargoSpec, ContainerSpec, Orientation, PackRequest, Placement
from app.packing import (
    PackedStack,
    PackingFailure,
    _build_solution,
    _build_stack_units,
    _compact_floor_candidate,
    _expand_stacks,
    _generic_floor_band_layout,
    _ai_strategy_score,
    _layout_quality_score,
    _high_fill_candidate,
    pack_order,
)
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


def test_cargo_label_does_not_change_layout():
    request = PackRequest(container=small_container(), cargo_items=[boxes()])
    renamed_item = request.cargo_items[0].model_copy(
        update={"sku": "用户自定义代号", "name": "用户自定义名称"}
    )
    renamed = request.model_copy(update={"cargo_items": [renamed_item]})

    original = pack_order(request)
    renamed_response = pack_order(renamed)

    assert [solution.model_dump() for solution in original.solutions] == [
        solution.model_dump() for solution in renamed_response.solutions
    ]


def test_ai_strategy_score_rewards_requested_floor_order_and_orientation():
    request = PackRequest(
        container=small_container(),
        cargo_items=[
            boxes(id="cargo-a", quantity=1, allowed_orientations=["LWH", "WLH"]),
            boxes(id="cargo-b", quantity=1, allowed_orientations=["LWH", "WLH"]),
        ],
        ai_layout_hint={
            "sku_order": ["cargo-b", "cargo-a"],
            "orientations": {"cargo-a": "WLH", "cargo-b": "LWH"},
        },
    )
    matching = [
        Placement(
            id="b", cargo_id="cargo-b", instance_index=0, x_mm=0, y_mm=0,
            z_mm=0, length_mm=1000, width_mm=500, height_mm=500,
            rotation=Orientation.LWH, weight_g=100_000, step=1,
        ),
        Placement(
            id="a", cargo_id="cargo-a", instance_index=0, x_mm=1000, y_mm=0,
            z_mm=0, length_mm=500, width_mm=1000, height_mm=500,
            rotation=Orientation.WLH, weight_g=100_000, step=1,
        ),
    ]
    reversed_layout = [matching[1], matching[0]]
    reversed_layout[0] = matching[1].model_copy(update={"x_mm": 0})
    reversed_layout[1] = matching[0].model_copy(update={"x_mm": 1000})

    assert _ai_strategy_score(request, matching) > _ai_strategy_score(request, reversed_layout)


def test_ai_hint_sets_a_legal_stack_unit_orientation_before_layout_search():
    request = PackRequest(
        container=ContainerSpec(
            id="ai-orientation", name="AI 朝向柜", inner_length_mm=3000,
            inner_width_mm=2000, inner_height_mm=1500, door_width_mm=2000,
            door_height_mm=1400, max_payload_g=1_000_000,
        ),
        cargo_items=[
            boxes(
                id="cargo-a", quantity=1, kind="pallet", length_mm=1000,
                width_mm=600, height_mm=500, allowed_orientations=["LWH", "WLH"],
            ),
        ],
        ai_layout_hint={
            "sku_order": ["cargo-a"],
            "orientations": {"cargo-a": "WLH"},
            "row_groups": [],
        },
    )

    units = _build_stack_units(request)

    assert [unit.orientation for unit in units] == [Orientation.WLH]


def test_profile_ai_hints_are_used_without_reducing_required_high_fill_count():
    request = PackRequest(
        container=ContainerSpec(
            id="profile-ai",
            name="profile AI 测试柜",
            inner_length_mm=4000,
            inner_width_mm=2000,
            inner_height_mm=1000,
            door_width_mm=2000,
            door_height_mm=1000,
            max_payload_g=1_000_000,
        ),
        cargo_items=[
            boxes(
                id="cargo-a",
                quantity=2,
                length_mm=1000,
                width_mm=500,
                height_mm=500,
                allowed_orientations=["LWH"],
                stackable=False,
            ),
            boxes(
                id="cargo-b",
                quantity=2,
                length_mm=1000,
                width_mm=500,
                height_mm=500,
                allowed_orientations=["LWH"],
                stackable=False,
            ),
        ],
        ai_layout_hint={
            "sku_order": ["cargo-a", "cargo-b"],
            "orientations": {},
            "row_groups": [],
            "profiles": {
                "high_fill": {"sku_order": ["cargo-a", "cargo-b"]},
                "stable": {"sku_order": ["cargo-b", "cargo-a"]},
                "easy": {"zone_order": ["cargo-b", "cargo-a"], "max_zones": 2},
            },
        },
    )

    response = pack_order(request)
    high_fill, stable, easy = response.solutions

    def first_floor_cargo(solution):
        floor = [item for item in solution.placements if item.z_mm == 0]
        return min(floor, key=lambda item: item.x_mm).cargo_id

    assert first_floor_cargo(high_fill) == "cargo-a"
    assert first_floor_cargo(stable) == "cargo-b"
    assert stable.metrics.loaded_pieces == high_fill.metrics.loaded_pieces
    assert easy.metrics.loaded_pieces <= high_fill.metrics.loaded_pieces
    assert all(
        validate_solution(request.container, request.cargo_items, solution.placements).valid
        for solution in response.solutions
    )
    assert easy.metrics.loading_steps <= high_fill.metrics.loading_steps


def test_compact_score_prefers_fewer_internal_floor_gaps():
    request = PackRequest(container=small_container(), cargo_items=[boxes(quantity=3)])

    def floor_piece(instance_index, x_mm):
        return Placement(
            id=f"piece-{instance_index}",
            cargo_id="box-a",
            instance_index=instance_index,
            x_mm=x_mm,
            y_mm=0,
            z_mm=0,
            length_mm=200,
            width_mm=200,
            height_mm=200,
            rotation=Orientation.LWH,
            weight_g=100_000,
            step=1,
        )

    compact = [floor_piece(0, 0), floor_piece(1, 400), floor_piece(2, 800)]
    gapped = [floor_piece(0, 0), floor_piece(1, 300), floor_piece(2, 800)]

    assert _layout_quality_score(request, compact, "high_fill") > _layout_quality_score(
        request, gapped, "high_fill"
    )


def test_compact_floor_candidate_keeps_validator_safe():
    request = PackRequest(
        container=small_container(),
        cargo_items=[
            boxes(
                quantity=2,
                length_mm=200,
                width_mm=200,
                height_mm=200,
            )
        ],
    )
    units = _build_stack_units(request)
    stacks = [
        PackedStack(unit=units[0], x_mm=400, y_mm=0),
        PackedStack(unit=units[1], x_mm=1000, y_mm=0),
    ]

    compact = _compact_floor_candidate(request, stacks, "high_fill")

    assert compact is not None
    placements = _expand_stacks(request, compact, "high_fill")
    assert min(item.x_mm for item in placements) == 0
    assert validate_solution(request.container, request.cargo_items, placements).valid


def test_reports_unloaded_quantity_when_order_exceeds_container():
    request = PackRequest(container=small_container(), cargo_items=[boxes(quantity=3)])

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"box-a": 2}
        assert solution.unloaded_counts == {"box-a": 1}


def test_warns_when_the_order_exceeds_container_payload():
    request = PackRequest(
        container=small_container().model_copy(update={"max_payload_g": 100_000}),
        cargo_items=[boxes(quantity=2, weight_g=100_000)],
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert any("订单总重" in warning and "超过柜体最大载重" in warning for warning in solution.warnings)


def test_all_profiles_keep_high_fill_loaded_set_when_rearrangement_cannot_improve_it():
    container = ContainerSpec(
        id="profile-counts",
        name="方案装入数量测试柜",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            boxes(id="c0", sku="C0", quantity=14, length_mm=700, width_mm=600, height_mm=500, weight_g=81_000, allowed_orientations=["LWH", "WLH"], stackable=True, max_layers=2, max_top_load_g=500_000, kind="pallet"),
            boxes(id="c1", sku="C1", quantity=5, length_mm=500, width_mm=500, height_mm=700, weight_g=198_000, allowed_orientations=["LWH", "WLH"], stackable=True, max_layers=2, max_top_load_g=500_000, kind="pallet"),
            boxes(id="c2", sku="C2", quantity=5, length_mm=700, width_mm=600, height_mm=800, weight_g=141_000, allowed_orientations=["LWH", "WLH"], stackable=False, max_layers=1, max_top_load_g=0, kind="pallet"),
            boxes(id="c3", sku="C3", quantity=9, length_mm=800, width_mm=700, height_mm=600, weight_g=183_000, allowed_orientations=["LWH", "WLH"], stackable=False, max_layers=1, max_top_load_g=0, kind="pallet"),
        ],
    )

    response = pack_order(request)

    assert response.solutions[1].loaded_counts == response.solutions[0].loaded_counts
    high_fill_candidate = _high_fill_candidate(request, _build_stack_units(request))
    assert len(_expand_stacks(request, high_fill_candidate, "high_fill")) == 33


def test_generic_floor_band_search_handles_non_preset_four_sku_mix():
    container = ContainerSpec(
        id="custom-four",
        name="自定义四 SKU 测试柜",
        inner_length_mm=6000,
        inner_width_mm=2400,
        inner_height_mm=2500,
        door_width_mm=2400,
        door_height_mm=2300,
        max_payload_g=20_000_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            boxes(
                id="custom-a",
                sku="ZT-A",
                name="自定义 A",
                kind="pallet",
                length_mm=600,
                width_mm=600,
                height_mm=700,
                quantity=9,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            boxes(
                id="custom-b",
                sku="ZT-B",
                name="自定义 B",
                kind="pallet",
                length_mm=700,
                width_mm=700,
                height_mm=700,
                quantity=9,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            boxes(
                id="custom-c",
                sku="ZT-C",
                name="自定义 C",
                kind="pallet",
                length_mm=900,
                width_mm=800,
                height_mm=700,
                quantity=3,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
            ),
            boxes(
                id="custom-d",
                sku="ZT-D",
                name="自定义 D",
                kind="pallet",
                length_mm=1000,
                width_mm=700,
                height_mm=700,
                quantity=2,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
            ),
        ],
    )
    units = _build_stack_units(request)
    by_cargo = {unit.cargo.id: unit for unit in units}
    quantity_by_cargo = Counter(unit.cargo.id for unit in units for _ in range(unit.count))
    capacity_by_cargo = {
        cargo_id: 2 if unit.cargo.stackable else 1
        for cargo_id, unit in by_cargo.items()
    }

    floor = _generic_floor_band_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        "fill",
    )

    assert floor is not None
    floor_items = [
        stack for stack in floor
        if stack.z_mm == request.container.clearance_mm
    ]
    upper_items = [
        stack for stack in floor
        if stack.z_mm > request.container.clearance_mm
    ]
    assert {stack.unit.cargo.id for stack in floor_items} == set(by_cargo)
    assert len(floor_items) > len(upper_items)
    assert all(
        stack.unit.cargo.id in {"custom-a", "custom-b"}
        for stack in upper_items
    )

    bounded_floor = _generic_floor_band_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        "fill",
        candidate_limit=200,
    )

    assert bounded_floor is not None
    assert validate_solution(
        request.container,
        request.cargo_items,
        _expand_stacks(request, bounded_floor, "high_fill"),
    ).valid

    response = pack_order(request)
    assert len(response.solutions) == 3
    for solution in response.solutions:
        assert validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
        ).valid


def test_rejects_order_when_must_load_cargo_cannot_fit():
    request = PackRequest(
        container=small_container(),
        cargo_items=[boxes(quantity=3, must_load=True)],
    )

    with pytest.raises(PackingFailure, match="必装") as exc_info:
        pack_order(request)

    assert exc_info.value.code == "MUST_LOAD_UNSATISFIED"


def test_returns_safe_fallback_when_upper_layer_is_not_continuous():
    container = ContainerSpec(
        id="fallback",
        name="次优方案测试柜",
        inner_length_mm=5000,
        inner_width_mm=2000,
        inner_height_mm=3000,
        door_width_mm=2000,
        door_height_mm=3000,
        max_payload_g=5_000_000,
    )
    cargo_items = [
        CargoSpec(
            id="a",
            sku="A",
            name="A",
            kind="pallet",
            length_mm=1000,
            width_mm=1000,
            height_mm=1000,
            weight_g=100_000,
            quantity=2,
            allowed_orientations=["LWH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
        CargoSpec(
            id="b",
            sku="B",
            name="B",
            kind="pallet",
            length_mm=1000,
            width_mm=1000,
            height_mm=1000,
            weight_g=100_000,
            quantity=2,
            allowed_orientations=["LWH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
    ]
    request = PackRequest(container=container, cargo_items=cargo_items)
    units = _build_stack_units(request)
    by_cargo = {unit.cargo.id: unit for unit in units}

    def one_piece(cargo_id: str, instance_index: int, stack_id: str):
        unit = by_cargo[cargo_id]
        return replace(
            unit,
            id=stack_id,
            count=1,
            stack_height_mm=unit.item_height_mm,
            total_weight_g=unit.cargo.weight_g,
            first_instance_index=instance_index,
        )

    stacks = [
        PackedStack(one_piece("a", 0, "a-floor"), x_mm=0, y_mm=0),
        PackedStack(
            one_piece("a", 1, "a-upper"),
            x_mm=0,
            y_mm=0,
            z_mm=1000,
        ),
        PackedStack(one_piece("b", 0, "b-floor"), x_mm=3000, y_mm=0),
        PackedStack(
            one_piece("b", 1, "b-upper"),
            x_mm=3000,
            y_mm=0,
            z_mm=1000,
        ),
    ]

    solution = _build_solution(request, stacks, "high_fill")

    assert solution.metrics.loaded_pieces == 4
    assert any("未形成单一中部连续区域" in warning for warning in solution.warnings)


def test_soft_layout_quality_never_blocks_safe_solution(monkeypatch):
    import app.packing as packing

    request = PackRequest(
        container=ContainerSpec(
            id="soft-rule",
            name="软规则测试柜",
            inner_length_mm=5000,
            inner_width_mm=2000,
            inner_height_mm=3000,
            door_width_mm=2000,
            door_height_mm=3000,
            max_payload_g=5_000_000,
        ),
        cargo_items=[
            CargoSpec(
                id="a",
                sku="A",
                name="A",
                kind="pallet",
                length_mm=1000,
                width_mm=1000,
                height_mm=1000,
                weight_g=100_000,
                quantity=2,
                allowed_orientations=["LWH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            CargoSpec(
                id="b",
                sku="B",
                name="B",
                kind="pallet",
                length_mm=1000,
                width_mm=1000,
                height_mm=1000,
                weight_g=100_000,
                quantity=2,
                allowed_orientations=["LWH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
        ],
    )
    monkeypatch.setattr(packing, "_upper_layout_quality_ok", lambda *_: False)

    response = packing.pack_order(request)

    assert len(response.solutions) == 3
    for solution in response.solutions:
        assert solution.metrics.loaded_pieces == 4
        assert "未完全满足上层连续集中要求，当前为次优方案，请现场复核" in solution.warnings
        assert validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
        ).valid


def test_invalid_primary_candidate_is_skipped_before_solution_build(monkeypatch):
    import app.packing as packing

    request = PackRequest(
        container=ContainerSpec(
            id="candidate-gate",
            name="候选门控测试柜",
            inner_length_mm=5000,
            inner_width_mm=2000,
            inner_height_mm=3000,
            door_width_mm=2000,
            door_height_mm=3000,
            max_payload_g=5_000_000,
        ),
        cargo_items=[
            CargoSpec(
                id="a",
                sku="A",
                name="A",
                kind="pallet",
                length_mm=1000,
                width_mm=1000,
                height_mm=1000,
                weight_g=100_000,
                quantity=2,
                allowed_orientations=["LWH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
        ],
    )
    unit = _build_stack_units(request)[0]
    monkeypatch.setattr(
        packing,
        "_pure_pallet_floor_first_layout",
        lambda *_: [
            PackedStack(
                unit=unit,
                x_mm=10_000,
                y_mm=0,
                step=1,
            )
        ],
    )

    response = packing.pack_order(request)

    assert len(response.solutions) == 3
    for solution in response.solutions:
        assert validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
        ).valid


def test_optional_unfit_cargo_returns_safe_empty_fallback():
    request = PackRequest(
        container=ContainerSpec(
            id="optional-unfit",
            name="可选货物测试柜",
            inner_length_mm=2000,
            inner_width_mm=1000,
            inner_height_mm=1000,
            door_width_mm=1000,
            door_height_mm=1000,
            max_payload_g=5_000_000,
        ),
        cargo_items=[
            CargoSpec(
                id="too-large",
                sku="OPTIONAL",
                name="可选超大件",
                kind="carton",
                length_mm=3000,
                width_mm=500,
                height_mm=500,
                weight_g=100_000,
                quantity=1,
                allowed_orientations=["LWH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
                must_load=False,
            )
        ],
    )

    response = pack_order(request)

    assert len(response.solutions) == 3
    for solution in response.solutions:
        assert solution.metrics.loaded_pieces == 0
        assert solution.unloaded_counts["too-large"] == 1
        assert any("未装入" in warning for warning in solution.warnings)


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

    # 二维候选与分层候选统一比较后，旋转货物可补满最后一个可用位置。
    assert response.solutions[0].loaded_counts == {"fixed": 1, "rotatable": 3}
    assert response.solutions[0].metrics.loaded_pieces == 4


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
    # 分层铺满（floor-layer-first）下货物浅铺：三方案垂直重心差异小
    assert abs(
        stable.metrics.center_of_gravity.z_mm - high_fill.metrics.center_of_gravity.z_mm
    ) <= 100


def forty_gp() -> ContainerSpec:
    return ContainerSpec(
        id="40gp",
        name="40GP",
        inner_length_mm=12032,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_800_000,
    )


def pallet(sku: str, weight_g: int, quantity: int, **overrides) -> CargoSpec:
    values = {
        "id": sku.lower(),
        "sku": sku,
        "name": f"整托 {sku}",
        "kind": "pallet",
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 1500,
        "weight_g": weight_g,
        "quantity": quantity,
        "allowed_orientations": ["LWH", "WLH"],
        "stackable": False,
        "max_layers": 1,
        "max_top_load_g": 0,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def test_metrics_include_length_and_width_imbalance():
    request = PackRequest(
        container=small_container(),
        cargo_items=[boxes()],
    )

    metrics = pack_order(request).solutions[0].metrics

    assert metrics.length_imbalance_pct >= 0
    assert metrics.width_imbalance_pct >= 0
    assert metrics.weight_imbalance_pct == round(
        max(metrics.length_imbalance_pct, metrics.width_imbalance_pct),
        2,
    )


def test_solution_zones_account_for_every_loaded_piece():
    container = ContainerSpec(
        id="zones",
        name="区域测试柜",
        inner_length_mm=3000,
        inner_width_mm=2000,
        inner_height_mm=2000,
        door_width_mm=2000,
        door_height_mm=2000,
        max_payload_g=5_000_000,
    )
    carton = boxes(
        quantity=8,
        length_mm=500,
        width_mm=400,
        height_mm=500,
        allowed_orientations=["LWH", "WLH"],
        stackable=True,
        max_layers=2,
        max_top_load_g=100_000,
    )
    request = PackRequest(container=container, cargo_items=[carton])

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.zones
        assert sum(zone.piece_count for zone in solution.zones) == len(
            solution.placements
        )
        assert all(zone.step >= 1 for zone in solution.zones)


def test_stable_balances_pallet_weights_along_length():
    request = PackRequest(
        container=forty_gp(),
        cargo_items=[
            pallet("HEAVY", 1_200_000, 11),
            pallet("LIGHT", 400_000, 11),
        ],
    )

    response = pack_order(request)
    high_fill, stable = response.solutions[:2]

    assert stable.loaded_counts == high_fill.loaded_counts
    assert stable.metrics.length_imbalance_pct <= 5
    assert stable.identical_to != "high_fill"
    assert validate_solution(
        request.container,
        request.cargo_items,
        stable.placements,
    ).valid


def test_stable_improves_on_asymmetric_pallet_weights():
    request = PackRequest(
        container=forty_gp(),
        cargo_items=[
            pallet("HEAVY", 1_200_000, 13),
            pallet("LIGHT", 400_000, 9),
        ],
    )

    response = pack_order(request)
    high_fill, stable = response.solutions[:2]

    assert stable.loaded_counts == high_fill.loaded_counts
    assert stable.metrics.length_imbalance_pct <= 5


def test_stable_uses_balancing_grid_for_pallet_only_order():
    container = ContainerSpec(
        id="20gp",
        name="20GP",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet("HEAVY", 1_200_000, 4),
            pallet("LIGHT", 400_000, 4),
        ],
    )

    response = pack_order(request)
    stable = response.solutions[1]

    assert stable.metrics.length_imbalance_pct <= 5
    assert stable.metrics.loading_steps == 2  # SKU 块布局：每 SKU 一块一步
    assert validate_solution(
        request.container,
        request.cargo_items,
        stable.placements,
    ).valid


def test_easy_keeps_all_pallet_pieces():
    request = PackRequest(
        container=forty_gp(),
        cargo_items=[
            pallet("HEAVY", 1_200_000, 11),
            pallet("LIGHT", 400_000, 11),
        ],
    )

    response = pack_order(request)
    easy = response.solutions[2]

    assert easy.loaded_counts == {"heavy": 11, "light": 11}


def test_easy_keeps_same_pieces_when_region_layout_fits():
    container = ContainerSpec(
        id="easy-simple",
        name="易操作简单柜",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            boxes(
                id="alpha",
                sku="ALPHA",
                quantity=24,
                length_mm=500,
                width_mm=400,
                height_mm=300,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=4,
                max_top_load_g=200_000,
            ),
            boxes(
                id="beta",
                sku="BETA",
                quantity=24,
                length_mm=400,
                width_mm=350,
                height_mm=300,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=4,
                max_top_load_g=200_000,
            ),
        ],
    )

    response = pack_order(request)
    high_fill, _, easy = response.solutions[:3]

    assert easy.loaded_counts == high_fill.loaded_counts
    assert len(easy.zones) == 2


def test_easy_drops_pieces_for_dense_order_and_discloses():
    container = ContainerSpec(
        id="20gp",
        name="20GP",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    )
    skus = [
        ("A", 600, 400, 300, 60),
        ("B", 500, 350, 250, 80),
        ("C", 700, 500, 400, 40),
        ("D", 450, 350, 300, 70),
        ("E", 800, 400, 350, 45),
    ]
    request = PackRequest(
        container=container,
        cargo_items=[
            boxes(
                id=sku,
                sku=sku,
                quantity=quantity,
                length_mm=length,
                width_mm=width,
                height_mm=height,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=4,
                max_top_load_g=200_000,
            )
            for sku, length, width, height, quantity in skus
        ],
        item_gap_mm=5,
    )

    response = pack_order(request)
    high_fill, _, easy = response.solutions[:3]

    assert easy.metrics.loaded_pieces <= high_fill.metrics.loaded_pieces
    assert len(easy.zones) < len(high_fill.zones)
    assert easy.metrics.loading_steps <= high_fill.metrics.loading_steps
    assert validate_solution(
        request.container,
        request.cargo_items,
        easy.placements,
    ).valid


def test_easy_fallback_keeps_must_load_counts():
    container = ContainerSpec(
        id="20gp",
        name="20GP",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            boxes(
                id="must",
                sku="MUST",
                quantity=48,
                length_mm=600,
                width_mm=400,
                height_mm=1000,
                weight_g=20_000,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
                must_load=True,
            ),
            boxes(
                id="optional",
                sku="OPTIONAL",
                quantity=20,
                length_mm=500,
                width_mm=300,
                height_mm=900,
                weight_g=10_000,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
            ),
        ],
    )

    response = pack_order(request)
    high_fill, _, easy = response.solutions[:3]

    assert easy.loaded_counts == high_fill.loaded_counts
    assert easy.loaded_counts["must"] == 48
    assert validate_solution(
        request.container,
        request.cargo_items,
        easy.placements,
    ).valid


def test_easy_may_drop_optional_cargo_for_compact_regions():
    request = PackRequest(
        container=ContainerSpec(
            id="easy-compaction",
            name="易操作测试柜",
            inner_length_mm=3000,
            inner_width_mm=1000,
            inner_height_mm=1000,
            door_width_mm=1000,
            door_height_mm=1000,
            max_payload_g=1_000_000,
            clearance_mm=0,
        ),
        cargo_items=[
            boxes(
                id="must-load",
                quantity=2,
                length_mm=1000,
                width_mm=500,
                height_mm=500,
                must_load=True,
            ),
            boxes(
                id="optional-a",
                quantity=3,
                length_mm=600,
                width_mm=500,
                height_mm=500,
            ),
            boxes(
                id="optional-b",
                quantity=1,
                length_mm=400,
                width_mm=500,
                height_mm=500,
            ),
        ],
        ai_layout_hint={
            "profiles": {
                "easy": {"max_zones": 2},
            },
        },
    )

    response = pack_order(request)
    high_fill, _, easy = response.solutions

    assert easy.metrics.loaded_pieces <= high_fill.metrics.loaded_pieces
    assert high_fill.metrics.loaded_pieces - easy.metrics.loaded_pieces <= max(
        1,
        round(high_fill.metrics.loaded_pieces * 0.05),
    )
    assert easy.loaded_counts["must-load"] == 2
    assert any("易操作方案少装" in warning for warning in easy.warnings)
    assert validate_solution(request.container, request.cargo_items, easy.placements).valid


def test_raise_for_invalid_layout_known_codes_gives_chinese_advice():
    from app.models import ValidationIssue, ValidationResult
    from app.packing import LAYOUT_ADVICE, _raise_for_invalid_layout

    validation = ValidationResult(
        valid=False,
        errors=[
            ValidationIssue(code="PALLET_STACKING", message="整托上方不能叠放整托"),
            ValidationIssue(code="TOP_LOAD_EXCEEDED", message="货物顶部承重超过限制"),
        ]
    )

    with pytest.raises(PackingFailure) as exc_info:
        _raise_for_invalid_layout(validation)

    failure = exc_info.value
    assert failure.code == "LAYOUT_NOT_FEASIBLE"
    assert "无法生成有效" in failure.message
    assert failure.hint is not None
    assert LAYOUT_ADVICE["PALLET_STACKING"] in failure.hint
    assert LAYOUT_ADVICE["TOP_LOAD_EXCEEDED"] in failure.hint


def test_raise_for_invalid_layout_unknown_code_stays_internal():
    from app.models import ValidationIssue, ValidationResult
    from app.packing import _raise_for_invalid_layout

    validation = ValidationResult(
        valid=False,
        errors=[ValidationIssue(code="MYSTERY_BUG", message="未知错误")]
    )

    with pytest.raises(PackingFailure) as exc_info:
        _raise_for_invalid_layout(validation)

    assert exc_info.value.code == "INTERNAL_INVALID_LAYOUT"
    assert exc_info.value.hint is None


def test_layout_advice_covers_every_validator_error_code():
    """LAYOUT_ADVICE 必须覆盖 validator 的全部错误码，否则新错误码会退回 500。"""
    import re
    from pathlib import Path

    from app.packing import LAYOUT_ADVICE

    validator_source = (
        Path(__file__).resolve().parents[1] / "app" / "validator.py"
    ).read_text(encoding="utf-8")
    validator_codes = set(re.findall(r'add\("([A-Z_]+)",', validator_source))

    assert validator_codes, "应能从 validator.py 提取错误码"
    missing = sorted(validator_codes - set(LAYOUT_ADVICE))
    assert not missing, f"LAYOUT_ADVICE 缺少以下错误码的建议：{missing}"
