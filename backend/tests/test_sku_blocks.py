from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import (
    _sku_block_layout,
    _build_sku_blocks,
    _build_stack_units,
    pack_order,
)


def _req(items):
    return PackRequest(
        container=ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
            inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
            door_height_mm=2585, max_payload_g=28600000),
        cargo_items=items,
    )


def _pallet(id, sku, l, w, h, kg, qty):
    return CargoSpec(id=id, sku=sku, name=sku, kind="pallet", length_mm=l, width_mm=w,
        height_mm=h, weight_g=kg * 1000, quantity=qty, allowed_orientations=["LWH"],
        stackable=True, max_layers=2, max_top_load_g=kg * 2000, fragile=False, must_load=False)


def test_block_columns_rows_layers():
    req = _req([_pallet("p1", "A", 650, 650, 1000, 174, 30)])
    units = _build_stack_units(req)
    blocks = _build_sku_blocks(req, units, "fill")
    assert blocks is not None and len(blocks) == 1
    b = blocks[0]
    # 柜宽 2352 / (650+gap0) = 3 列；30 托 2 层 → rows = ceil(30/(3*2))=5
    assert b.columns == 3
    assert b.layers == 2
    assert b.rows == 5
    assert b.block_length_mm == 5 * 650
    assert b.pieces == 30


def test_block_length_uses_door_buffer():
    req = _req([_pallet("p1", "A", 650, 650, 1000, 174, 30)])
    req.door_buffer_mm = 0
    units = _build_stack_units(req)
    blocks = _build_sku_blocks(req, units, "fill")
    # door_buffer 不影响单块参数，但可用柜长截断在 Task 3 放置时生效
    assert blocks[0].block_length_mm == 5 * 650


def test_place_blocks_fill_order_and_door_buffer():
    req = _req([
        _pallet("p2", "B", 890, 750, 1100, 303, 30),
        _pallet("p1", "A", 650, 650, 1000, 174, 30),
    ])
    units = _build_stack_units(req)
    stacks = _sku_block_layout(req, units, "fill")
    assert stacks is not None
    # 体积降序：p2 (890×750) 在 p1 (650×650) 前面（靠柜头 x 小）
    p2 = [s for s in stacks if s.unit.cargo.id == "p2"]
    p1 = [s for s in stacks if s.unit.cargo.id == "p1"]
    assert max(s.x_mm for s in p2) < min(s.x_mm for s in p1)
    # 门端缓冲：最远件 x + length <= 柜长 - door_buffer
    max_x = max(s.x_mm + s.length_mm for s in stacks)
    assert max_x <= 12032 - req.door_buffer_mm


def test_place_blocks_balance_heavy_center():
    req = _req([
        _pallet("heavy", "H", 890, 750, 1100, 303, 30),
        _pallet("light", "L", 650, 650, 1000, 174, 30),
    ])
    units = _build_stack_units(req)
    stacks = _sku_block_layout(req, units, "balance")
    assert stacks is not None
    heavy = [s for s in stacks if s.unit.cargo.id == "heavy"]
    light = [s for s in stacks if s.unit.cargo.id == "light"]
    hc = sum(s.x_mm + s.length_mm / 2 for s in heavy) / len(heavy)
    lc = sum(s.x_mm + s.length_mm / 2 for s in light) / len(light)
    center = 12032 / 2
    assert abs(hc - center) < abs(lc - center), "重块应比轻块更居中"


def test_pack_order_three_solutions_use_sku_blocks():
    req = _req([
        _pallet("p1", "A", 650, 650, 1000, 174, 30),
        _pallet("p2", "B", 890, 750, 1100, 303, 30),
        _pallet("p3", "C", 1080, 800, 1200, 427, 3),
        _pallet("p4", "D", 1220, 920, 1150, 532, 3),
        _pallet("p5", "E", 1050, 1050, 1100, 500, 3),
    ])
    resp = pack_order(req)
    assert len(resp.solutions) == 3
    for s in resp.solutions:
        # 全装 69 托
        assert s.metrics.loaded_pieces == 69
        # 门端缓冲生效：最远件 x + len <= 12032 - 300
        max_x = max(p.x_mm + p.length_mm for p in s.placements)
        assert max_x <= 12032 - 300
    # 三方案布局互不相同
    a = resp.solutions[0].placements
    b = resp.solutions[1].placements
    c = resp.solutions[2].placements
    sig = lambda ps: tuple((p.cargo_id, p.x_mm, p.y_mm) for p in ps)
    assert len({sig(a), sig(b), sig(c)}) == 3, "三方案布局应互不相同"


def _carton(id, sku, l, w, h, kg, qty, layers=8):
    return CargoSpec(id=id, sku=sku, name=sku, kind="carton", length_mm=l, width_mm=w,
        height_mm=h, weight_g=kg * 1000, quantity=qty, allowed_orientations=["LWH", "WLH"],
        stackable=True, max_layers=layers, max_top_load_g=kg * 10000, fragile=False, must_load=False)


def test_pure_carton_fill_falls_back_to_layer_layout():
    # 706 件单 SKU 散箱：fill 的 SKU 块超长 → 回退分层铺满（装载率最优）
    req = _req([_carton("ca", "CA", 500, 400, 400, 10, 706)])
    units = _build_stack_units(req)
    layout = _sku_block_layout(req, units, "fill")
    assert layout is None, "706 件单块超长应返回 None 由调用方回退"
    resp = pack_order(req)
    assert resp.solutions[0].metrics.loaded_pieces == 706


def test_mixed_pallet_carton_blocks():
    req = _req([
        _pallet("p1", "A", 1200, 800, 1100, 175, 4),
        _carton("ca", "CA", 500, 400, 400, 10, 40),
    ])
    resp = pack_order(req)
    assert resp.solutions[0].metrics.loaded_pieces == 44
