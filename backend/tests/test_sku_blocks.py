import pytest

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import (
    _sku_block_layout,
    _build_sku_blocks,
    _build_stack_units,
    pack_order,
)
from app.validator import validate_solution


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
    # 柜宽 2352 / (650+gap0) = 3 列；30 托可叠 2 层
    # rows = min_rows（全部件叠满 2 层所需行数）= ceil(30/(3*2))=5；
    # flat_rows = 全部件平铺第 1 层所需行数 = ceil(30/3)=10（底层铺满）
    assert b.columns == 3
    assert b.layers == 2
    assert b.rows == 5
    assert b.flat_rows == 10
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


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_floor_layer_first_fills_bottom_before_stacking(pack_by_goal, goal):
    """规则回归：无论纯整托/纯散箱/混装，都是先把底层铺满，
    剩余件数才叠到第 2 层集中在中间（不能在底层未铺满时叠高）。"""
    req = _req([
        _pallet("p1", "A", 650, 650, 1200, 174, 30),
        _pallet("p2", "B", 890, 750, 1120, 303, 30),
        _pallet("p3", "C", 1080, 800, 1250, 427, 3),
    ])
    sol = pack_by_goal(req, goal)
    assert sol.metrics.loaded_pieces == 63, f"{sol.profile} 应全装 63 件"
    # 底层（z=0）件数 = 底位数：底层应优先铺满，并多于上层件数
    bottom = [p for p in sol.placements if p.z_mm == 0]
    upper = [p for p in sol.placements if p.z_mm > 0]
    assert len(bottom) > len(upper), (
        f"{sol.profile} 底层仅 {len(bottom)} 底位，未遵循先铺满底层"
    )
    # 剩余件数不超底层：底层铺满后才叠上层
    assert len(upper) < len(bottom), (
        f"{sol.profile} 上层 {len(upper)} 件过多（底层 {len(bottom)}），未先铺满底层"
    )
    # 上层件在各自 SKU 块内集中于中间（两头低中间高）
    center = 12032 / 2
    upper_cx = sum(p.x_mm + p.length_mm / 2 for p in upper) / len(upper)
    assert abs(upper_cx - center) <= 12032 * 0.35, (
        f"{sol.profile} 上层过于偏置（上层中心 {upper_cx:.0f}）"
    )


def test_three_goals_use_sku_blocks(pack_by_goal):
    req = _req([
        _pallet("p1", "A", 650, 650, 1000, 174, 30),
        _pallet("p2", "B", 890, 750, 1100, 303, 30),
        _pallet("p3", "C", 1080, 800, 1200, 427, 3),
        _pallet("p4", "D", 1220, 920, 1150, 532, 3),
        _pallet("p5", "E", 1050, 1050, 1100, 500, 3),
    ])
    solutions = {goal: pack_by_goal(req, goal) for goal in ["high_fill", "stable", "easy"]}
    for goal, s in solutions.items():
        # 全装 69 托
        assert s.metrics.loaded_pieces == 69, f"{goal} 应全装 69 托"
        # 门端缓冲生效：最远件 x + len <= 12032 - 300
        max_x = max(p.x_mm + p.length_mm for p in s.placements)
        assert max_x <= 12032 - 300
    # 三目标布局互不相同
    def sig(ps):
        return tuple((p.cargo_id, p.x_mm, p.y_mm) for p in ps)
    assert len({sig(s.placements) for s in solutions.values()}) == 3, "三目标布局应互不相同"


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


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_composite_rotated_pallet_footprint_synced(pack_by_goal, goal):
    # Critical-1：可旋转托盘（LWH↔WLH，原朝向 750×900 即 length<width）+ 散箱混装。
    # _build_sku_blocks 对占宽更小的朝向 swap 了块 footprint，但 CompositeUnit 放置
    # 分支曾不同步 pallet 长宽/朝向 → 块按 swap 尺寸排位而件按原尺寸展开 → OVERLAP
    # → PackingFailure。修复后 footprint 同步，布局校验通过。
    req = _req([
        CargoSpec(id="p1", sku="P", name="P", kind="pallet", length_mm=750,
            width_mm=900, height_mm=1100, weight_g=175000, quantity=4,
            allowed_orientations=["LWH", "WLH"], stackable=False,
            max_top_load_g=350000, fragile=False, must_load=False),
        CargoSpec(id="ca", sku="CA", name="CA", kind="carton", length_mm=700,
            width_mm=600, height_mm=300, weight_g=10000, quantity=24,
            allowed_orientations=["LWH", "WLH"], stackable=True, max_layers=3,
            max_top_load_g=50000000, fragile=False, must_load=False),
    ])
    s = pack_by_goal(req, goal)
    assert s.metrics.loaded_pieces == 28, f"{s.profile} 未全装 28 件"
    v = validate_solution(
        req.container, req.cargo_items, s.placements,
        item_gap_mm=req.item_gap_mm,
    )
    assert v.valid, f"{s.profile} 布局校验失败: {[e.code for e in v.errors]}"


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_balance_center_slot_respects_clearance(pack_by_goal, goal):
    # Critical-2：balance 中心槽起点只保证 ≥0 不保证 ≥clearance。单块总长
    # 11000 落在 (inner_length-4c-door_buffer, inner_length-2c-door_buffer] 区间时，
    # (usable_length-door_buffer-total_len)//2=166 < clearance=200 → 最左块越界
    # → OUT_OF_BOUNDS。修复后 cursor=max(c, …) 且最右越界时回退。
    req = _req([
        CargoSpec(id="w1", sku="W", name="W", kind="pallet", length_mm=1000,
            width_mm=1940, height_mm=1100, weight_g=100000, quantity=11,
            allowed_orientations=["LWH"], stackable=False, max_top_load_g=0,
            fragile=False, must_load=False),
    ])
    req.container.clearance_mm = 200
    units = _build_stack_units(req)
    blocks = _build_sku_blocks(req, units, "balance")
    assert blocks is not None and len(blocks) == 1
    assert blocks[0].block_length_mm == 11000
    stacks = _sku_block_layout(req, units, "balance")
    assert stacks is not None, "总长 11000 ≤ usable_length-door_buffer，应可用中心槽布局"
    assert min(s.x_mm for s in stacks) >= req.container.clearance_mm
    assert max(s.x_mm + s.length_mm for s in stacks) <= (
        req.container.inner_length_mm - 2 * req.container.clearance_mm - req.door_buffer_mm
    )
    s = pack_by_goal(req, goal)
    assert s.metrics.loaded_pieces == 11


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_door_buffer_disclosed_in_warnings(pack_by_goal, goal):
    # Important-3：规格 §3.2 要求柜门预留操作空间进 warnings/cons 披露
    req = _req([_carton("ca", "CA", 500, 400, 400, 10, 40)])
    s = pack_by_goal(req, goal)
    assert any("柜门预留操作空间" in w and "300" in w for w in s.warnings), s.warnings
    # door_buffer=0（关闭）时不披露
    req.door_buffer_mm = 0
    s0 = pack_by_goal(req, goal)
    assert not any("柜门预留操作空间" in w for w in s0.warnings), s0.warnings


@pytest.mark.parametrize("goal", ["high_fill", "stable", "easy"])
def test_stackable_pallet_layers_honor_top_load_plus_one(pack_by_goal, goal):
    """承重层数 = 能承受的上层数 + 1（底层自身）。

    回归：_build_sku_blocks 曾用 max_top_load // weight（少 +1），把
    "可叠 2 层"误判成 1 层。280kg/承重 500kg 应叠 2 层（500//280+1=2），
    否则整托被强制平铺、占满柜长后其它 SKU 装不下（用户实测 63 托剩 14 件）。
    """
    def pallet(pid, sku, l, w, h, qty, kg, top_load_kg):
        return CargoSpec(id=pid, sku=sku, name=sku, kind="pallet", length_mm=l, width_mm=w,
            height_mm=h, weight_g=kg * 1000, quantity=qty, allowed_orientations=["LWH"],
            stackable=True, max_layers=5, max_top_load_g=top_load_kg * 1000,
            fragile=False, must_load=False, unload_order=0)

    req = _req([
        pallet("c1", "A", 650, 650, 1200, 30, 150, 500),
        pallet("c2", "B", 890, 750, 1120, 30, 280, 500),
        pallet("c3", "C", 1080, 800, 1250, 3, 400, 500),
    ])
    units = _build_stack_units(req)
    blocks = _build_sku_blocks(req, units, "fill")
    layers = {b.sku_id: b.layers for b in blocks}
    # 500//280+1=2、500//400+1=2：三类托盘都应叠 2 层（高度也允许 2 层）
    assert layers["c2"] == 2, f"c2 承重层数应为 2，实际 {layers['c2']}"
    assert layers["c3"] == 2, f"c3 承重层数应为 2，实际 {layers['c3']}"
    s = pack_by_goal(req, goal)
    assert s.metrics.loaded_pieces == 63, (
        f"{s.profile} 应全装 63 件，实际 {s.metrics.loaded_pieces}"
    )


def test_legacy_interstack_fields_do_not_create_formal_interstack_solution():
    """旧互叠字段仍可解析，但不再生成第五个正式方案。"""
    req = _req([
        CargoSpec(id="big", sku="BIG", name="大托", kind="pallet", length_mm=1200,
            width_mm=1000, height_mm=1100, weight_g=400000, quantity=20,
            allowed_orientations=["LWH"], stackable=True, max_layers=2,
            max_top_load_g=5000000, fragile=False, must_load=False),
        CargoSpec(id="small", sku="SMALL", name="小托", kind="pallet", length_mm=500,
            width_mm=400, height_mm=600, weight_g=100000, quantity=200,
            allowed_orientations=["LWH"], stackable=True, max_layers=3,
            max_top_load_g=1000000, fragile=False, must_load=False),
    ])
    req.support_coverage_min = 0.7
    req.overhang_ratio_max = 0.2
    resp = pack_order(req)
    assert len(resp.solutions) == 1
    s = resp.solutions[0]
    assert s.profile == "high_fill"
    v = validate_solution(
        req.container, req.cargo_items, s.placements,
        item_gap_mm=req.item_gap_mm,
    )
    assert v.valid, f"{s.profile} 布局校验失败: {[e.code for e in v.errors]}"


def test_disable_interstack_returns_single_solution():
    """关闭互叠开关后只返回默认目标的单个方案。"""
    req = _req([_pallet("p1", "A", 650, 650, 1000, 174, 30)])
    req.enable_interstack = False
    resp = pack_order(req)
    profiles = [s.profile for s in resp.solutions]
    assert profiles == ["high_fill"], profiles
