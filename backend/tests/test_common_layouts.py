from collections import Counter

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import _rectangle_components, pack_order
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


def pallet(
    cargo_id: str,
    sku: str,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    weight_kg: int,
    quantity: int,
    *,
    stackable: bool,
) -> CargoSpec:
    return CargoSpec(
        id=cargo_id,
        sku=sku,
        name=sku,
        kind="pallet",
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        weight_g=weight_kg * 1000,
        quantity=quantity,
        allowed_orientations=["LWH", "WLH"],
        stackable=stackable,
        max_layers=2 if stackable else 1,
        max_top_load_g=500_000,
        fragile=False,
        must_load=False,
    )


def pallet_request(items: list[CargoSpec]) -> PackRequest:
    return PackRequest(container=forty_hq(), cargo_items=items)


def four_sku_request() -> PackRequest:
    return pallet_request(
        [
            pallet("p1", "P1", 760, 760, 1000, 100, 18, stackable=True),
            pallet("p2", "P2", 1150, 1150, 1100, 150, 8, stackable=True),
            pallet("p3", "P3", 1100, 1100, 1000, 140, 12, stackable=True),
            pallet("p4", "P4", 1050, 1050, 800, 120, 12, stackable=True),
        ]
    )


def five_sku_request() -> PackRequest:
    return pallet_request(
        [
            pallet("q1", "Q1", 700, 700, 1100, 100, 22, stackable=True),
            pallet("q2", "Q2", 900, 750, 1080, 120, 25, stackable=True),
            pallet("q3", "Q3", 1080, 800, 1050, 140, 5, stackable=True),
            pallet("q4", "Q4", 1000, 800, 1000, 130, 1, stackable=False),
            pallet("q5", "Q5", 1220, 920, 1180, 180, 2, stackable=False),
        ]
    )


def overlaps_xy(left, right) -> bool:
    return (
        left.x_mm < right.x_mm + right.length_mm
        and left.x_mm + left.length_mm > right.x_mm
        and left.y_mm < right.y_mm + right.width_mm
        and left.y_mm + left.width_mm > right.y_mm
    )


def same_floor_row(left, right) -> bool:
    return (
        left.z_mm == right.z_mm == 0
        and left.x_mm == right.x_mm
        and (
            left.y_mm + left.width_mm == right.y_mm
            or right.y_mm + right.width_mm == left.y_mm
        )
    )


def test_four_sku_case_loads_all_fifty_pallets_and_validates():
    request = four_sku_request()
    response = pack_order(request)

    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [50] * 4
    for solution in response.solutions:
        validation = validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
            item_gap_mm=request.item_gap_mm,
        )
        assert validation.valid, [issue.code for issue in validation.errors]
        assert max(
            placement.x_mm + placement.length_mm
            for placement in solution.placements
        ) <= request.container.inner_length_mm - request.door_buffer_mm


def test_five_sku_case_loads_all_fifty_five_pallets_and_validates():
    request = five_sku_request()
    response = pack_order(request)

    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [55] * 4
    for solution in response.solutions:
        validation = validate_solution(
            request.container,
            request.cargo_items,
            solution.placements,
            item_gap_mm=request.item_gap_mm,
        )
        assert validation.valid, [issue.code for issue in validation.errors]
        assert max(
            placement.x_mm + placement.length_mm
            for placement in solution.placements
        ) <= request.container.inner_length_mm - request.door_buffer_mm


def test_five_sku_case_keeps_bottom_first_and_same_sku_support():
    request = five_sku_request()
    response = pack_order(request)

    for solution in response.solutions:
        bottom = [placement for placement in solution.placements if placement.z_mm == 0]
        upper = [placement for placement in solution.placements if placement.z_mm > 0]
        assert len(bottom) > len(upper)

        for elevated in upper:
            supports = [
                support
                for support in bottom
                if support.cargo_id == elevated.cargo_id
                and overlaps_xy(support, elevated)
                and support.z_mm + support.height_mm == elevated.z_mm
            ]
            assert supports, (
                solution.profile,
                elevated.cargo_id,
                elevated.x_mm,
                elevated.y_mm,
            )


def test_five_sku_case_pairs_remainder_products_in_floor_rows():
    response = pack_order(five_sku_request())
    expected_pairs = (("q3", "q4"), ("q2", "q5"), ("q1", "q5"))

    for solution in response.solutions:
        bottom = [placement for placement in solution.placements if placement.z_mm == 0]
        for left_id, right_id in expected_pairs:
            assert any(
                same_floor_row(left, right)
                for left in bottom
                if left.cargo_id == left_id
                for right in bottom
                if right.cargo_id == right_id
            ), (solution.profile, left_id, right_id)

        assert Counter(placement.cargo_id for placement in bottom)["q5"] == 2


def test_four_and_five_sku_upper_layers_are_centered_single_components():
    for request in (four_sku_request(), five_sku_request()):
        response = pack_order(request)
        center_x = request.container.inner_length_mm / 2
        for solution in response.solutions:
            upper = [
                placement
                for placement in solution.placements
                if placement.z_mm > request.container.clearance_mm
            ]
            assert upper
            assert _rectangle_components(
                [
                    (
                        placement.x_mm,
                        placement.y_mm,
                        placement.length_mm,
                        placement.width_mm,
                    )
                    for placement in upper
                ],
                request.item_gap_mm + 1,
            ) == 1, (solution.profile, solution.warnings)
            upper_center = sum(
                placement.x_mm + placement.length_mm / 2
                for placement in upper
            ) / len(upper)
            assert abs(upper_center - center_x) <= 1800, (
                solution.profile,
                upper_center,
            )
            assert not any(
                "上层货物被拆成" in warning for warning in solution.warnings
            )


def test_four_and_five_sku_profiles_have_four_distinct_layouts():
    for request in (four_sku_request(), five_sku_request()):
        response = pack_order(request)
        signatures = {
            tuple(
                (
                    placement.cargo_id,
                    placement.instance_index,
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
        assert all(solution.identical_to is None for solution in response.solutions)
