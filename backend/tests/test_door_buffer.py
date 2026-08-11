from app.models import ContainerSpec, PackRequest, CargoSpec


def _make_cargo():
    return CargoSpec(id="c1", sku="S1", name="箱", kind="carton",
        length_mm=500, width_mm=400, height_mm=400, weight_g=10000, quantity=1,
        allowed_orientations=["LWH"], stackable=True, max_layers=4,
        max_top_load_g=50000, fragile=False, must_load=False)


def test_door_buffer_default_300():
    req = PackRequest(
        container=ContainerSpec(id="c", name="c", inner_length_mm=12032,
            inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
            door_height_mm=2585, max_payload_g=28600000),
        cargo_items=[_make_cargo()],
    )
    assert req.door_buffer_mm == 300


def test_door_buffer_zero_allowed():
    req = PackRequest(
        container=ContainerSpec(id="c", name="c", inner_length_mm=12032,
            inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
            door_height_mm=2585, max_payload_g=28600000),
        cargo_items=[_make_cargo()], door_buffer_mm=0,
    )
    assert req.door_buffer_mm == 0
