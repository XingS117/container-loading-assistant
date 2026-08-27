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


def screenshot_order_request() -> PackRequest:
    return pallet_request(
        [
            pallet("zt3", "ZT3", 1080, 800, 1050, 300, 25, stackable=True),
            pallet("sku-001", "SKU-001", 750, 750, 1000, 150, 20, stackable=True),
            pallet("sku-002", "SKU-002", 900, 1000, 800, 160, 16, stackable=True),
            pallet("sku-001-1200", "SKU-001", 1200, 600, 1000, 450, 4, stackable=True),
            pallet("sku-003", "SKU-003", 1250, 1000, 1050, 400, 2, stackable=True),
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

    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [50] * 3
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
        ) <= request.container.inner_length_mm - request.door_buffer_mm + 10


def test_five_sku_case_loads_all_fifty_five_pallets_and_validates():
    request = five_sku_request()
    response = pack_order(request)

    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [55] * 3
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
        ) <= request.container.inner_length_mm - request.door_buffer_mm + 10


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


def test_five_sku_customer_pair_directions_are_preserved():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        bottom = [placement for placement in solution.placements if placement.z_mm == 0]
        q4_x = next(
            placement.x_mm
            for placement in bottom
            if placement.cargo_id == "q4"
        )
        q3_q4 = [
            placement
            for placement in bottom
            if placement.x_mm == q4_x
        ]
        q2_q5_x = next(
            placement.x_mm
            for placement in bottom
            if placement.cargo_id == "q2"
            and any(
                other.cargo_id == "q5"
                and other.x_mm == placement.x_mm
                for other in bottom
            )
        )
        q1_q5_x = next(
            placement.x_mm
            for placement in bottom
            if placement.cargo_id == "q1"
            and any(
                other.cargo_id == "q5"
                and other.x_mm == placement.x_mm
                for other in bottom
            )
        )
        q2_q5 = [
            placement
            for placement in bottom
            if placement.cargo_id in {"q2", "q5"} and placement.x_mm == q2_q5_x
        ]
        q1_q5 = [
            placement
            for placement in bottom
            if placement.cargo_id in {"q1", "q5"} and placement.x_mm == q1_q5_x
        ]

        assert {placement.rotation.value for placement in q3_q4} == {"WLH"}
        assert {placement.rotation.value for placement in q2_q5} == {"WLH"}
        assert {placement.rotation.value for placement in q1_q5} == {"LWH"}


def test_five_sku_uses_customer_q2_two_across_orientation():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        q2_bottom = [
            placement
            for placement in solution.placements
            if placement.cargo_id == "q2" and placement.z_mm == 0
        ]
        q2_upper = [
            placement
            for placement in solution.placements
            if placement.cargo_id == "q2" and placement.z_mm > 0
        ]

        assert {placement.rotation.value for placement in q2_bottom} == {"WLH"}
        assert sorted(Counter(placement.x_mm for placement in q2_bottom).values()) == [
            1, 2, 2, 2, 2, 2, 2
        ], solution.profile
        assert sorted(Counter(placement.x_mm for placement in q2_upper).values()) == [
            2, 2, 2, 2, 2, 2
        ], solution.profile


def test_five_sku_follows_customer_head_to_door_row_plan():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        bottom = [placement for placement in solution.placements if placement.z_mm == 0]
        upper = [placement for placement in solution.placements if placement.z_mm > 0]

        q4_x = next(p.x_mm for p in bottom if p.cargo_id == "q4")
        q2_q5_x = next(
            p.x_mm
            for p in bottom
            if p.cargo_id == "q2"
            and any(
                other.cargo_id == "q5"
                and other.x_mm == p.x_mm
                for other in bottom
            )
        )
        q1_q5_x = next(
            p.x_mm
            for p in bottom
            if p.cargo_id == "q1"
            and any(
                other.cargo_id == "q5"
                and other.x_mm == p.x_mm
                for other in bottom
            )
        )

        q1_main_x = sorted(
            {
                p.x_mm
                for p in bottom
                if p.cargo_id == "q1" and p.x_mm != q1_q5_x
            }
        )
        q2_main_x = sorted(
            {
                p.x_mm
                for p in bottom
                if p.cargo_id == "q2" and p.x_mm != q2_q5_x
            }
        )
        q3_main_x = sorted(
            {
                p.x_mm
                for p in bottom
                if p.cargo_id == "q3" and p.x_mm != q4_x
            }
        )

        assert q1_main_x and q2_main_x and q3_main_x
        assert max(q1_main_x) < min(q2_main_x) < min(q3_main_x) < q4_x
        assert q4_x < q2_q5_x < q1_q5_x

        assert sorted(
            Counter(p.x_mm for p in bottom if p.cargo_id == "q1").values()
        ) == [1, 3, 3, 3, 3, 3], solution.profile
        assert sorted(
            Counter(p.x_mm for p in bottom if p.cargo_id == "q2").values()
        ) == [1, 2, 2, 2, 2, 2, 2], solution.profile
        assert sorted(
            Counter(p.x_mm for p in bottom if p.cargo_id == "q3").values()
        ) == [1, 2], solution.profile

        assert sorted(
            Counter(p.x_mm for p in upper if p.cargo_id == "q1").values()
        ) == [3, 3], solution.profile
        assert {
            p.x_mm for p in upper if p.cargo_id == "q1"
        } == set(q1_main_x[-2:]), solution.profile
        assert sorted(
            Counter(p.x_mm for p in upper if p.cargo_id == "q2").values()
        ) == [2, 2, 2, 2, 2, 2], solution.profile
        assert sorted(
            Counter(p.x_mm for p in upper if p.cargo_id == "q3").values()
        ) == [2], solution.profile


def test_five_sku_customer_plan_discloses_actual_door_reserve():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        max_end = max(
            placement.x_mm + placement.length_mm
            for placement in solution.placements
        )
        actual_reserve = five_sku_request().container.inner_length_mm - max_end

        assert actual_reserve == 292
        assert any("实际柜门预留 292mm" in warning for warning in solution.warnings), (
            solution.profile,
            solution.warnings,
        )


def test_five_sku_upper_rows_have_no_internal_gap():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        upper_by_row: dict[tuple[str, int], list] = {}
        for placement in solution.placements:
            if placement.z_mm <= 0:
                continue
            upper_by_row.setdefault(
                (placement.cargo_id, placement.x_mm),
                [],
            ).append(placement)

        for row_key, row in upper_by_row.items():
            row.sort(key=lambda placement: placement.y_mm)
            assert all(
                left.y_mm + left.width_mm + five_sku_request().item_gap_mm
                == right.y_mm
                for left, right in zip(row, row[1:])
            ), (
                solution.profile,
                row_key,
                [(placement.y_mm, placement.width_mm) for placement in row],
            )

        q3_upper = [
            placement
            for placement in solution.placements
            if placement.cargo_id == "q3" and placement.z_mm > 0
        ]
        assert sorted(Counter(placement.x_mm for placement in q3_upper).values()) == [2]


def test_four_and_five_sku_upper_layers_are_centered_single_components():
    for request in (four_sku_request(), five_sku_request()):
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
            floor_center = (
                min(placement.x_mm for placement in bottom)
                + max(placement.x_mm + placement.length_mm for placement in bottom)
            ) / 2
            assert abs(upper_center - floor_center) <= 1800, (
                solution.profile,
                upper_center,
            )
            assert not any(
                "上层货物被拆成" in warning for warning in solution.warnings
            )


def test_four_and_five_sku_floor_starts_at_container_head_and_is_contiguous():
    for request in (four_sku_request(), five_sku_request()):
        response = pack_order(request)
        clearance = request.container.clearance_mm
        for solution in response.solutions:
            bottom = [
                placement
                for placement in solution.placements
                if placement.z_mm == clearance
            ]
            assert min(placement.x_mm for placement in bottom) == clearance
            assert _rectangle_components(
                [
                    (
                        placement.x_mm,
                        placement.y_mm,
                        placement.length_mm,
                        placement.width_mm,
                    )
                    for placement in bottom
                ],
                request.item_gap_mm + 1,
            ) == 1, (solution.profile, solution.warnings)


def test_five_sku_layout_limits_transverse_floor_holes():
    response = pack_order(five_sku_request())

    for solution in response.solutions:
        assert solution.metrics.floor_largest_transverse_gap_mm <= 400, (
            solution.profile,
            solution.metrics.floor_largest_transverse_gap_mm,
        )


def test_screenshot_order_keeps_each_profile_transverse_floor_hole_small():
    request = screenshot_order_request()
    response = pack_order(request)

    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [63] * 3
    for solution in response.solutions:
        assert solution.metrics.floor_largest_transverse_gap_mm == 0, (
            solution.profile,
            solution.metrics.floor_largest_transverse_gap_mm,
            solution.loaded_counts,
        )


def test_four_and_five_sku_profiles_have_three_distinct_layouts():
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
        assert len(signatures) == 3
        assert all(solution.identical_to is None for solution in response.solutions)
