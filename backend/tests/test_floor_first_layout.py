from collections import Counter

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import (
    _build_stack_units,
    _expand_stacks,
    _pure_pallet_floor_first_layout,
    _rectangle_components,
    pack_order,
)
from app.validator import validate_solution


def forty_hq() -> ContainerSpec:
    return ContainerSpec(
        id="40hq",
        name="40HQ",
        inner_length_mm=12032,
        inner_width_mm=2352,
        inner_height_mm=2698,
        door_width_mm=2340,
        door_height_mm=2585,
        max_payload_g=28_600_000,
    )


def abc_request() -> PackRequest:
    return PackRequest(
        container=forty_hq(),
        cargo_items=[
            CargoSpec(
                id="a",
                sku="A",
                name="A",
                kind="pallet",
                length_mm=650,
                width_mm=650,
                height_mm=1200,
                weight_g=150_000,
                quantity=30,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            CargoSpec(
                id="b",
                sku="B",
                name="B",
                kind="pallet",
                length_mm=890,
                width_mm=750,
                height_mm=1120,
                weight_g=280_000,
                quantity=30,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            CargoSpec(
                id="c",
                sku="C",
                name="C",
                kind="pallet",
                length_mm=1080,
                width_mm=800,
                height_mm=1250,
                weight_g=400_000,
                quantity=3,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=500_000,
            ),
        ],
    )


def _x_components(placements, gap: int = 0):
    intervals = sorted(
        (placement.x_mm, placement.x_mm + placement.length_mm)
        for placement in placements
    )
    components = []
    for start, end in intervals:
        if components and start <= components[-1][1] + gap:
            components[-1] = (components[-1][0], max(components[-1][1], end))
        else:
            components.append((start, end))
    return components


def test_rectangle_components_detects_width_axis_islands():
    rectangles = [
        (0, 0, 100, 100),
        (0, 150, 100, 100),
    ]

    assert _rectangle_components(rectangles, gap=0) == 2


def test_floor_first_keeps_unload_order_and_upper_region_contiguous():
    request = abc_request()
    request.cargo_items[0].unload_order = 1
    request.cargo_items[1].unload_order = 2
    request.cargo_items[2].unload_order = 3

    response = pack_order(request)

    for solution in response.solutions:
        upper = [placement for placement in solution.placements if placement.z_mm > 0]
        rectangles = [
            (
                placement.x_mm,
                placement.y_mm,
                placement.length_mm,
                placement.width_mm,
            )
            for placement in upper
        ]
        assert solution.metrics.loaded_pieces == 63
        assert _rectangle_components(rectangles, request.item_gap_mm + 1) == 1
        assert not any(
            placement.cargo_id == "c" and placement.z_mm > 0
            for placement in solution.placements
        )


def test_floor_first_uses_selected_quantity_limits():
    request = abc_request()
    limits = {"a": 6, "b": 6, "c": 2}
    units = _build_stack_units(request, limits, "stable")

    stacks = _pure_pallet_floor_first_layout(request, units, "stable")
    assert stacks is not None

    placements = _expand_stacks(request, stacks, "stable")
    loaded_counts = Counter(placement.cargo_id for placement in placements)
    assert loaded_counts == limits


def test_abc_returns_four_named_core_solutions_and_no_interstack():
    response = pack_order(abc_request())

    assert [solution.profile for solution in response.solutions] == [
        "high_fill",
        "stable",
        "easy",
        "strict_support",
    ]
    assert [solution.name for solution in response.solutions] == [
        "装载率优先",
        "重心稳妥",
        "易操作",
        "底层优先",
    ]
    assert all(solution.metrics.loaded_pieces == 63 for solution in response.solutions)


def test_abc_core_profiles_are_distinct_but_keep_the_same_loaded_set():
    response = pack_order(abc_request())

    signatures = {
        tuple(
            (
                placement.cargo_id,
                placement.x_mm,
                placement.y_mm,
                placement.z_mm,
                placement.rotation.value,
            )
            for placement in solution.placements
        )
        for solution in response.solutions
    }
    assert len(signatures) == 4
    assert response.solutions[0].metrics.loaded_pieces == 63
    assert response.solutions[1].metrics.loaded_pieces == 63
    assert response.solutions[2].metrics.loaded_pieces == 63
    assert response.solutions[3].metrics.loaded_pieces == 63

    bottom_counts = [
        sum(placement.z_mm == 0 for placement in solution.placements)
        for solution in response.solutions
    ]
    assert bottom_counts[3] > bottom_counts[0]


def test_abc_all_core_solutions_are_physically_valid_and_floor_first():
    request = abc_request()
    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
            item_gap_mm=request.item_gap_mm,
        )
        assert result.valid, [issue.code for issue in result.errors]

        bottom = [
            placement
            for placement in solution.placements
            if placement.z_mm == request.container.clearance_mm
        ]
        upper = [
            placement
            for placement in solution.placements
            if placement.z_mm > request.container.clearance_mm
        ]
        assert len(bottom) > len(upper)
        assert len(bottom) >= 30

        upper_components = _x_components(upper, request.item_gap_mm + 1)
        assert len(upper_components) == 1
        upper_center = sum(
            placement.x_mm + placement.length_mm / 2
            for placement in upper
        ) / len(upper)
        assert abs(upper_center - request.container.inner_length_mm / 2) <= 1800
        assert len({(placement.cargo_id, placement.instance_index) for placement in solution.placements}) == 63


def test_abc_upper_layer_is_not_an_isolated_single_piece():
    response = pack_order(abc_request())

    for solution in response.solutions:
        upper = [placement for placement in solution.placements if placement.z_mm > 0]
        counts = Counter(placement.cargo_id for placement in upper)
        assert sum(counts.values()) >= 2
        assert all(count >= 2 for count in counts.values())


def test_abc_uses_pb_pa_pb_floor_bands_and_same_sku_upper_support():
    request = abc_request()
    response = pack_order(request)

    for solution in response.solutions:
        bottom = [
            placement
            for placement in solution.placements
            if placement.z_mm == request.container.clearance_mm
        ]
        upper = [
            placement
            for placement in solution.placements
            if placement.z_mm > request.container.clearance_mm
        ]
        pa_bottom = [placement for placement in bottom if placement.cargo_id == "a"]
        pb_bottom = [placement for placement in bottom if placement.cargo_id == "b"]
        pc_bottom = [placement for placement in bottom if placement.cargo_id == "c"]

        pb_bands = _x_components(pb_bottom, request.item_gap_mm + 1)
        pa_band = _x_components(pa_bottom, request.item_gap_mm + 1)
        assert len(pb_bands) == 2
        assert len(pa_band) == 1
        assert pb_bands[0][1] <= pa_band[0][0]
        assert pa_band[0][1] <= pb_bands[1][0]
        assert min(placement.x_mm for placement in pc_bottom) >= max(
            placement.x_mm + placement.length_mm
            for placement in bottom
            if placement.cargo_id in {"a", "b"}
        )

        for elevated in upper:
            same_sku_support = [
                support
                for support in bottom
                if support.cargo_id == elevated.cargo_id
                and support.x_mm < elevated.x_mm + elevated.length_mm
                and support.x_mm + support.length_mm > elevated.x_mm
                and support.y_mm < elevated.y_mm + elevated.width_mm
                and support.y_mm + support.width_mm > elevated.y_mm
                and support.z_mm + support.height_mm == elevated.z_mm
            ]
            assert same_sku_support, (
                solution.profile,
                elevated.cargo_id,
                elevated.x_mm,
                elevated.y_mm,
            )


def test_legacy_interstack_request_flag_does_not_create_a_fifth_solution():
    request = abc_request()
    request.enable_interstack = True
    response = pack_order(request)

    assert len(response.solutions) == 4
    assert all(solution.profile != "interstack" for solution in response.solutions)
