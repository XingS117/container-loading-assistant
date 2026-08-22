from collections import Counter

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import pack_order


def five_sku_request() -> PackRequest:
    container = ContainerSpec(
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
        length_mm: int,
        width_mm: int,
        height_mm: int,
        quantity: int,
        stackable: bool,
    ) -> CargoSpec:
        return CargoSpec(
            id=cargo_id,
            sku=cargo_id,
            name=cargo_id,
            kind="pallet",
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            weight_g=100_000,
            quantity=quantity,
            allowed_orientations=["LWH", "WLH"],
            stackable=stackable,
            max_layers=2 if stackable else 1,
            max_top_load_g=500_000,
        )

    return PackRequest(
        container=container,
        cargo_items=[
            pallet("q1", 700, 700, 1100, 22, True),
            pallet("q2", 900, 750, 1080, 25, True),
            pallet("q3", 1080, 800, 1050, 5, True),
            pallet("q4", 1000, 800, 1000, 1, False),
            pallet("q5", 1220, 920, 1180, 2, False),
        ],
    )


def test_five_sku_returns_customer_template_as_a_formal_solution():
    response = pack_order(five_sku_request())

    assert len(response.solutions) == 3

    for solution in response.solutions:
        bottom = [p for p in solution.placements if p.z_mm == 0]
        upper = [p for p in solution.placements if p.z_mm > 0]
        x_groups = {
            x: Counter(p.cargo_id for p in bottom if p.x_mm == x)
            for x in sorted({p.x_mm for p in bottom})
        }
        assert list(x_groups.values())[:5] == [
            Counter({"q1": 3}),
            Counter({"q1": 3}),
            Counter({"q1": 3}),
            Counter({"q1": 3}),
            Counter({"q1": 3}),
        ]
        assert x_groups[3500] == Counter({"q2": 2})
        assert x_groups[8000] == Counter({"q3": 2})
        assert x_groups[8800] == Counter({"q3": 1, "q4": 1})
        assert x_groups[9600] == Counter({"q2": 1, "q5": 1})
        assert x_groups[10520] == Counter({"q1": 1, "q5": 1})
        assert {
            cargo_id: sorted(
                Counter(p.x_mm for p in upper if p.cargo_id == cargo_id).values()
            )
            for cargo_id in {"q1", "q2", "q3"}
        } == {
            "q1": [3, 3],
            "q2": [2, 2, 2, 2, 2, 2],
            "q3": [2],
        }
