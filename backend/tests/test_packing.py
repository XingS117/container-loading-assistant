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
    assert stable.metrics.loading_steps == 4
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
    high_fill, _, easy = response.solutions

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
    high_fill, _, easy = response.solutions

    assert easy.metrics.loaded_pieces <= high_fill.metrics.loaded_pieces
    assert len(easy.zones) < len(high_fill.zones)
    assert easy.metrics.loading_steps <= high_fill.metrics.loading_steps
    assert any("少装" in con for con in easy.cons)
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
    high_fill, _, easy = response.solutions

    assert easy.loaded_counts == high_fill.loaded_counts
    assert easy.loaded_counts["must"] == 48
    assert validate_solution(
        request.container,
        request.cargo_items,
        easy.placements,
    ).valid


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
