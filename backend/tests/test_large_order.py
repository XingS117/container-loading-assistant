from time import perf_counter

from app.main import CONTAINER_PRESETS
from app.models import CargoSpec, PackRequest
from app.packing import pack_order
from app.validator import validate_solution


def test_30_sku_order_finishes_within_service_budget():
    cargo_items = [
        CargoSpec(
            id=f"cargo-{index}",
            sku=f"SKU-{index:02d}",
            name=f"测试货物 {index}",
            kind="pallet" if index % 11 == 0 else "carton",
            length_mm=300 + index % 4 * 50,
            width_mm=280 + index % 3 * 60,
            height_mm=250 + index % 5 * 40,
            weight_g=8_000 + index * 500,
            quantity=167 if index < 20 else 166,
            allowed_orientations=["LWH", "WLH"],
            stackable=False if index % 11 == 0 else True,
            max_layers=1 if index % 11 == 0 else 6,
            max_top_load_g=0 if index % 11 == 0 else 200_000,
            must_load=False,
        )
        for index in range(30)
    ]
    request = PackRequest(
        container=CONTAINER_PRESETS[2],
        cargo_items=cargo_items,
        item_gap_mm=5,
    )

    started = perf_counter()
    response = pack_order(request)
    elapsed = perf_counter() - started

    assert elapsed < 15
    assert len(response.solutions) == 3
    assert response.solutions[1].metrics.weight_imbalance_pct <= response.solutions[0].metrics.weight_imbalance_pct
    assert response.solutions[2].metrics.loading_steps <= response.solutions[0].metrics.loading_steps
    assert all(
        validate_solution(request.container, cargo_items, solution.placements).valid
        for solution in response.solutions
    )


def test_5000_small_boxes_finish_within_service_budget():
    cargo = CargoSpec(
        id="small-box",
        sku="SMALL-BOX",
        name="小型纸箱",
        kind="carton",
        length_mm=100,
        width_mm=100,
        height_mm=100,
        weight_g=1_000,
        quantity=5000,
        allowed_orientations=["LWH", "WLH"],
        stackable=True,
        max_layers=20,
        max_top_load_g=30_000,
    )
    request = PackRequest(container=CONTAINER_PRESETS[0], cargo_items=[cargo])

    started = perf_counter()
    response = pack_order(request)
    elapsed = perf_counter() - started

    assert elapsed < 15
    assert all(solution.loaded_counts["small-box"] == 5000 for solution in response.solutions)
    assert all(
        validate_solution(request.container, [cargo], solution.placements).valid
        for solution in response.solutions
    )


def test_five_mixed_pallet_skus_finish_within_service_budget():
    cargo_items = [
        CargoSpec(
            id="p0",
            sku="P0",
            name="托盘 0",
            kind="pallet",
            length_mm=1200,
            width_mm=500,
            height_mm=650,
            weight_g=100_000,
            quantity=16,
            allowed_orientations=["LWH", "WLH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
        CargoSpec(
            id="p1",
            sku="P1",
            name="托盘 1",
            kind="pallet",
            length_mm=1100,
            width_mm=700,
            height_mm=800,
            weight_g=200_000,
            quantity=12,
            allowed_orientations=["LWH", "WLH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
        CargoSpec(
            id="p2",
            sku="P2",
            name="托盘 2",
            kind="pallet",
            length_mm=1000,
            width_mm=600,
            height_mm=950,
            weight_g=400_000,
            quantity=20,
            allowed_orientations=["LWH", "WLH"],
            stackable=True,
            max_layers=3,
            max_top_load_g=500_000,
        ),
        CargoSpec(
            id="p3",
            sku="P3",
            name="托盘 3",
            kind="pallet",
            length_mm=1000,
            width_mm=800,
            height_mm=1100,
            weight_g=100_000,
            quantity=36,
            allowed_orientations=["LWH", "WLH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
        CargoSpec(
            id="p4",
            sku="P4",
            name="托盘 4",
            kind="pallet",
            length_mm=800,
            width_mm=800,
            height_mm=800,
            weight_g=200_000,
            quantity=20,
            allowed_orientations=["LWH", "WLH"],
            stackable=True,
            max_layers=2,
            max_top_load_g=500_000,
        ),
    ]
    request = PackRequest(
        container=CONTAINER_PRESETS[2],
        cargo_items=cargo_items,
        item_gap_mm=5,
    )

    started = perf_counter()
    response = pack_order(request)
    elapsed = perf_counter() - started

    assert elapsed < 12
    assert len(response.solutions) == 3
    assert all(
        validate_solution(request.container, cargo_items, solution.placements).valid
        for solution in response.solutions
    )
