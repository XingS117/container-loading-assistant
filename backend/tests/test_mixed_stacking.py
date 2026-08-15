import pytest

from app.models import CargoSpec, ContainerSpec, Orientation, PackRequest
from app.packing import (
    CompositeUnit,
    PackedStack,
    StackUnit,
    _build_stack_units,
    _expand_stacks,
    _merge_pallet_cartons,
    pack_order,
)
from app.validator import validate_solution


def mixed_container() -> ContainerSpec:
    return ContainerSpec(
        id="mixed",
        name="混装测试柜",
        inner_length_mm=6000,
        inner_width_mm=2400,
        inner_height_mm=2400,
        door_width_mm=2350,
        door_height_mm=2300,
        max_payload_g=10_000_000,
    )


def pallet_box(**overrides) -> CargoSpec:
    values = {
        "id": "pallet",
        "sku": "P-100",
        "name": "整托",
        "kind": "pallet",
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 1100,
        "weight_g": 400_000,
        "quantity": 1,
        "allowed_orientations": ["LWH"],
        "stackable": False,
        "max_layers": 1,
        "max_top_load_g": 500_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def carton_box(**overrides) -> CargoSpec:
    values = {
        "id": "carton",
        "sku": "C-200",
        "name": "散箱",
        "kind": "carton",
        "length_mm": 500,
        "width_mm": 400,
        "height_mm": 300,
        "weight_g": 20_000,
        "quantity": 4,
        "allowed_orientations": ["LWH", "WLH"],
        "stackable": True,
        "max_layers": 4,
        "max_top_load_g": 100_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def test_merge_pallet_cartons_creates_composite():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4)],
    )
    units = _build_stack_units(request)

    merged = _merge_pallet_cartons(request, units)

    composites = [unit for unit in merged if isinstance(unit, CompositeUnit)]
    assert composites, "可叠散箱应合并到可承重托盘上"
    composite = composites[0]
    assert composite.pallet.cargo.kind == "pallet"
    assert len(composite.on_top) >= 1
    # 整栈上托（不拆栈）：4 件栈高度放得下托盘顶面 → 全部上托
    assert composite.count == 1 + 4
    assert composite.total_weight_g == 400_000 + 4 * 20_000
    assert composite.length_mm == 1200 and composite.width_mm == 1000


def test_fragile_cartons_never_merge():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4, fragile=True)],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_zero_top_load_pallet_never_merges():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(max_top_load_g=0), carton_box(quantity=4)],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_carton_too_large_for_pallet_top_never_merges():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            carton_box(quantity=1, length_mm=1300, width_mm=1300, height_mm=500,
                       allowed_orientations=["LWH"]),
        ],
    )

    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    assert not any(isinstance(unit, CompositeUnit) for unit in merged)


def test_pure_carton_or_pallet_order_unchanged():
    carton_request = PackRequest(
        container=mixed_container(),
        cargo_items=[carton_box(quantity=4)],
    )
    pallet_request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box()],
    )

    merged_cartons = _merge_pallet_cartons(
        carton_request, _build_stack_units(carton_request)
    )
    merged_pallets = _merge_pallet_cartons(
        pallet_request, _build_stack_units(pallet_request)
    )

    assert all(isinstance(unit, StackUnit) for unit in merged_cartons)
    assert all(isinstance(unit, StackUnit) for unit in merged_pallets)


def test_expand_composite_places_cartons_on_pallet_top():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=4)],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))
    composite = next(unit for unit in merged if isinstance(unit, CompositeUnit))

    placements = _expand_stacks(
        request,
        [PackedStack(unit=composite, x_mm=0, y_mm=0, step=1)],
        "high_fill",
    )

    pallet_p = [p for p in placements if p.cargo_id == "pallet"]
    carton_p = [p for p in placements if p.cargo_id == "carton"]
    assert len(pallet_p) == 1
    # 整栈上托：4 件栈全部上托到托盘顶面
    assert len(carton_p) == 4
    assert pallet_p[0].z_mm == 0
    assert pallet_p[0].height_mm == 1100
    assert sorted(p.z_mm for p in carton_p) == [1100, 1400, 1700, 2000]
    assert all(p.step == 1 for p in placements)
    # 散箱件使用散箱自身尺寸与朝向（未旋转，LWH）
    assert all(p.length_mm == 500 for p in carton_p)
    assert all(p.width_mm == 400 for p in carton_p)
    assert all(p.rotation == Orientation.LWH for p in carton_p)


def test_expand_composite_multiple_stacks_do_not_overlap():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[pallet_box(), carton_box(quantity=8)],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))
    composite = next(unit for unit in merged if isinstance(unit, CompositeUnit))
    # 8 个散箱拆成 2 个栈，限 1 层上托 → 2 个 on_top 栈（各 1 件）
    assert len(composite.on_top) == 2

    placements = _expand_stacks(
        request,
        [PackedStack(unit=composite, x_mm=0, y_mm=0, step=1)],
        "high_fill",
    )

    carton_p = [p for p in placements if p.cargo_id == "carton"]
    assert len(carton_p) == 8, "2 个整栈各 4 件全部上托"
    for p in carton_p:
        assert p.z_mm >= 1100
        # 每件完整落在托盘顶面（1200×1000）内
        assert 0 <= p.x_mm <= 1200
        assert 0 <= p.y_mm <= 1000
        assert p.x_mm + p.length_mm <= 1200
        assert p.y_mm + p.width_mm <= 1000
    # 两个栈的顶面偏移不同 → 互不重叠
    assert len({(p.x_mm, p.y_mm, p.z_mm) for p in carton_p}) == 8, "8 件互不重叠（2 栈各 4 层）"
    assert len({(p.x_mm, p.y_mm) for p in carton_p}) >= 2


def test_mixed_order_pallets_on_bottom_cartons_on_top():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[pallet_box(must_load=True), carton_box(quantity=8)],
    )

    response = pack_order(request)

    assert response.solutions[0].loaded_counts == {"pallet": 1, "carton": 8}
    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert pallet_p
        # 分层铺满：散箱铺在第 1 层（z=0）底面
        assert all(p.z_mm >= 1100 for p in carton_p), "散箱应上托到托盘顶面（轻在上）"


def test_stable_keeps_high_fill_piece_count_and_centers_pallets():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )

    response = pack_order(request)

    high_fill = response.solutions[0]
    stable = response.solutions[1]
    assert stable.metrics.loaded_pieces == high_fill.metrics.loaded_pieces
    pallet_p = [p for p in stable.placements if p.cargo_id == "pallet"]
    assert pallet_p, "stable 方案应装入整托"
    min_x = min(p.x_mm for p in pallet_p)
    assert min_x > 0, "stable 托盘带应居中（不贴柜头铺）"


def test_mixed_order_partial_pallet_top_loading():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200"),
            carton_box(quantity=40),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"pallet": 1, "pallet2": 1, "carton": 40}
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert len(carton_p) == 40
        # 分层铺满：散箱第 1 层铺底面（40 件底面放得下 → 全部铺底）
        on_floor = [p for p in carton_p if p.z_mm == container.clearance_mm]
        assert on_floor, "散箱第 1 层应铺底面"


def test_mixed_order_with_item_gap_valid():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(must_load=True),
            pallet_box(id="pallet2", sku="P-200"),
            carton_box(quantity=40),
        ],
        item_gap_mm=10,
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts == {"pallet": 1, "pallet2": 1, "carton": 40}
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        carton_p = [p for p in solution.placements if p.cargo_id == "carton"]
        assert len(carton_p) == 40
        # 分层铺满：散箱第 1 层铺底面，超出部分叠高
        assert any(p.z_mm == 0 for p in carton_p), "散箱第 1 层应铺底面（gap>0）"


def test_mixed_order_with_must_load_carton_and_mixed_unavailable():
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", length_mm=1100, width_mm=900),
            carton_box(quantity=8, must_load=True),
        ],
    )

    response = pack_order(request)

    high_fill = response.solutions[0]
    assert high_fill.loaded_counts["carton"] == 8
    assert high_fill.loaded_counts["pallet"] == 1
    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]


def test_easy_keeps_pallets_when_cartons_overflow():
    """回归：混装大订单端区放不下全部散箱时，easy 少装优先删散箱而保留托盘。

    容器 6000×2400×2400，2 个托盘（1200×1000）+ 300 散箱（max_layers=4，
    拆 75 栈，其中 8 栈叠上托盘顶）。托盘带两侧端区（各 2400×2400）最多
    各容纳 24 栈 500×400 散箱栈（共 48 栈），独立散箱栈远超端区容量，
    触发 _mixed_balance_layout(allow_partial=True) 丢弃溢出的散箱路径。
    """
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200"),
            carton_box(quantity=300),
        ],
    )

    response = pack_order(request)

    easy = response.solutions[2]
    # 少装/端区溢出时托盘不得被优先删掉
    assert easy.loaded_counts["pallet"] >= 1
    assert easy.loaded_counts["pallet2"] >= 1
    # 三个方案均通过装载校验
    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
    # 发生少装时通过 warnings 披露未装入件数
    if sum(easy.unloaded_counts.values()) > 0:
        assert any("未装入" in message for message in easy.warnings)


def test_overflow_cartons_to_end_zones_with_gap_valid():
    """C1 回归（新布局）：散箱溢出中间带后放入两端带剩余空间，
    item_gap>0 时不产生 OVERLAP / CLEARANCE_VIOLATION。

    20GP 风格柜 + 8 托盘（占两端带）+ 大量 315 散箱（中间带 63 栈满 →
    溢出到端带剩余空间）。
    """
    container = ContainerSpec(
        id="gp20",
        name="20GP",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=10_000_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(quantity=8, max_top_load_g=0),
            carton_box(quantity=520, max_layers=8),
        ],
        item_gap_mm=10,
    )

    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
    # high_fill 分层铺满装入（20GP 容量有限）；easy 允许少装
    assert response.solutions[0].loaded_counts["carton"] > 0




def test_easy_never_drops_required_cartons():
    """C2 回归：easy 的 allow_partial 不得丢弃必装散箱。

    2 托盘（max_top_load_g=0，不上托）+ 200 必装散箱：端区放不下全部散箱，
    修复前 allow_partial=True 直接丢弃 remaining（含必装）→ validator
    MUST_LOAD_MISSING → 500。修复后含必装则返回 None，easy 回退保护路径，
    必装 200 件全部装入。
    """
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(max_top_load_g=0),
            pallet_box(id="pallet2", sku="P-200", max_top_load_g=0),
            carton_box(quantity=200, must_load=True),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        assert solution.loaded_counts["carton"] >= 200, "必装散箱不得少装"
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]


def test_rotatable_pallet_composite_stays_unrotated():
    """I1 回归：CompositeUnit 托盘不得被旋转放置。

    长柜（12032×2450×2698）+ 可旋转托盘（LWH/WLH）+ 8 散箱（全上托）。
    修复前 CompositeUnit 候选含旋转 footprint，公共 footprint 选中旋转后托盘
    rotated=True，但 on_top 偏移基于未旋转托盘顶面 → 散箱悬空 →
    UNSUPPORTED → 500。修复后复合单位只保留未旋转候选。
    """
    container = ContainerSpec(
        id="long",
        name="长柜",
        inner_length_mm=12032,
        inner_width_mm=2450,
        inner_height_mm=2698,
        door_width_mm=2400,
        door_height_mm=2585,
        max_payload_g=10_000_000,
    )
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(allowed_orientations=["LWH", "WLH"]),
            carton_box(quantity=8),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallet_p = [p for p in solution.placements if p.cargo_id == "pallet"]
        assert pallet_p
        # 托盘按未旋转 footprint（1200×1000）放置，on_top 偏移坐标系一致
        assert all(
            (p.length_mm, p.width_mm) == (1200, 1000) for p in pallet_p
        ), "托盘不得旋转放置"


def test_zones_include_pallet_top_cartons():
    """I2 回归：zones 应包含上叠散箱（规格 3.4 各自成区）。

    1 托盘 + 8 散箱全上托：修复前 _compute_zones 过滤 z != floor_z 的
    placement，zones 缺散箱 SKU → 打印报告区域说明缺失。修复后托盘与其上
    叠散箱（step 相同、cargo_id 不同）各自成区。
    """
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[pallet_box(), carton_box(quantity=8)],
    )

    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        carton_zones = [z for z in solution.zones if z.cargo_id == "carton"]
        assert carton_zones, "散箱应在 zones 中各自成区（分层铺满）"
        pallet_steps = {z.step for z in solution.zones if z.cargo_id == "pallet"}
        assert pallet_steps  # 托盘自成区；散箱各自成区（不再要求同 step）


def test_stackable_pallet_can_stack_when_height_and_load_allow():
    """整托配置为可叠放（stackable=True）时允许垂直叠放：
    按高度（柜内剩余空间）与顶部承重（max_top_load）自动判定层数。"""
    container = ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
        inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
        door_height_mm=2585, max_payload_g=28600000, clearance_mm=0)
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(stackable=True, max_layers=3, quantity=30,
                       allowed_orientations=["LWH"], must_load=True),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        pallets = [p for p in solution.placements if p.cargo_id == "pallet"]
        assert len(pallets) == 30, "30 个整托都应装入（第 1 层铺满 + 叠高）"
        assert any(p.z_mm > 0 for p in pallets), "可叠整托超出底面后应叠高（z>0）"


def test_non_stackable_pallet_rejects_pallet_above():
    """不可叠整托上方再放整托 → PALLET_STACKING（安全兜底）。"""
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(stackable=False, max_layers=1, quantity=2, must_load=True),
            carton_box(quantity=4),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        pallets = [p for p in solution.placements if p.cargo_id == "pallet"]
        assert all(p.z_mm == 0 for p in pallets), "不可叠整托不得叠放"


def test_pallet_top_merge_splits_oversized_stacks():
    """托盘上方空间不够整栈时拆层上托：能放的层数叠托盘顶面，其余保留独立栈。"""
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(height_mm=1400, max_top_load_g=500_000),
            carton_box(quantity=8, max_layers=8),  # 8 层×300=2400 > 托盘上方(2400-1400=1000)
        ],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    composites = [u for u in merged if isinstance(u, CompositeUnit)]
    assert composites, "应生成复合单位（拆层上托）"
    composite = composites[0]
    on_top_total = sum(stack.count for stack, _, _ in composite.on_top)
    assert on_top_total >= 1, "应有散箱叠上托盘"
    # 全部散箱件数 = 上托 + 独立
    independent = [
        u for u in merged if isinstance(u, StackUnit) and u.cargo.id == "carton"
    ]
    independent_total = sum(u.count for u in independent)
    assert on_top_total + independent_total == 8, "拆层不得丢件"


def test_split_stacks_do_not_duplicate_instance_indices():
    """拆层后的上托栈与独立栈 instance_index 不得重叠（validator 防重复）。"""
    container = mixed_container()
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(height_mm=1400, max_top_load_g=500_000),
            carton_box(quantity=8, max_layers=8),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        indices = [
            p.instance_index
            for p in solution.placements
            if p.cargo_id == "carton"
        ]
        assert len(indices) == len(set(indices)), "instance_index 不得重复"


def test_rotatable_pallets_mixed_order_no_layout_failure():
    """整托允许旋转（LWH+WLH，前端"保持正放"真实提交）+ 混装散箱：
    不得产生 OVERLAP/UNSUPPORTED（_pack_units 曾对 CompositeUnit 旋转导致上叠散箱错位）。"""
    container = ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
        inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
        door_height_mm=2585, max_payload_g=28600000, clearance_mm=0)
    items = [
        pallet_box(id="pa", sku="PA", height_mm=1400, weight_g=175000, quantity=10,
                   allowed_orientations=["LWH", "WLH"]),
        pallet_box(id="pb", sku="PB", height_mm=1600, weight_g=100000, quantity=8,
                   allowed_orientations=["LWH", "WLH"]),
        pallet_box(id="pc", sku="PC", height_mm=1800, weight_g=95000, quantity=6,
                   allowed_orientations=["LWH", "WLH"]),
        carton_box(id="ca", sku="CA", quantity=200,
                   allowed_orientations=["LWH", "WLH", "LHW", "WHL"]),
        carton_box(id="cb", sku="CB", length_mm=340, width_mm=320, height_mm=500,
                   weight_g=4200, quantity=150, max_layers=6, max_top_load_g=50000,
                   allowed_orientations=["LWH", "WLH", "LHW", "WHL"]),
        carton_box(id="cc", sku="CC", length_mm=420, width_mm=420, height_mm=420,
                   weight_g=9000, quantity=100, max_layers=5, max_top_load_g=60000,
                   allowed_orientations=["LWH", "WLH", "LHW", "WHL"]),
    ]
    request = PackRequest(container=container, cargo_items=items)

    response = pack_order(request)

    assert len(response.solutions) == 4
    for solution in response.solutions:
        result = validate_solution(
            container, items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]


def test_rotatable_pallet_overflow_does_not_crash():
    """CompositeUnit 溢出到中间带/端带时不得触发 replace 旋转崩溃
    （生产 500 复现：TypeError: CompositeUnit.__init__() got unexpected keyword 'length_mm'）。"""
    container = ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
        inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
        door_height_mm=2585, max_payload_g=28600000, clearance_mm=0)
    request = PackRequest(
        container=container,
        cargo_items=[
            pallet_box(allowed_orientations=["LWH", "WLH"], quantity=24),
            carton_box(quantity=200),
        ],
    )

    response = pack_order(request)

    assert len(response.solutions) == 4
    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]


def test_unload_order_later_unloaded_goes_to_container_head():
    """先卸后装：卸货顺序大的（后卸）货物先装进柜头（x 小），
    卸货顺序小的（先卸）货物靠柜门（x 大）。"""
    container = ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
        inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
        door_height_mm=2585, max_payload_g=28600000, clearance_mm=0)
    request = PackRequest(
        container=container,
        cargo_items=[
            carton_box(id="later", sku="LATER", quantity=60,
                       length_mm=500, width_mm=400, height_mm=400,
                       unload_order=2),
            carton_box(id="first", sku="FIRST", quantity=60,
                       length_mm=500, width_mm=400, height_mm=400,
                       unload_order=1),
        ],
    )

    response = pack_order(request)

    for solution in response.solutions:
        later = [p for p in solution.placements if p.cargo_id == "later"]
        first = [p for p in solution.placements if p.cargo_id == "first"]
        assert later and first, "两类货物都应装入"
        later_max_x = max(p.x_mm for p in later)
        first_min_x = min(p.x_mm for p in first)
        assert later_max_x <= first_min_x, "后卸（order=2）应装进柜头，先卸（order=1）靠柜门"


def test_upper_layer_stacks_toward_container_center():
    """分层铺满：第 1 层铺满柜底，叠高层（顶层）集中在柜长中间（两头低中间高）。"""
    container = ContainerSpec(id="40hq", name="40HQ", inner_length_mm=12032,
        inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
        door_height_mm=2585, max_payload_g=28600000, clearance_mm=0)
    request = PackRequest(
        container=container,
        cargo_items=[
            carton_box(quantity=630, length_mm=500, width_mm=400, height_mm=400,
                       max_layers=8, max_top_load_kg=50),
        ],
    )

    response = pack_order(request)
    center = 12032 / 2

    for solution in response.solutions:
        result = validate_solution(
            container, request.cargo_items, solution.placements, request.item_gap_mm
        )
        assert result.valid, [error.code for error in result.errors]
        by_z: dict[int, list] = {}
        for p in solution.placements:
            by_z.setdefault(p.z_mm, []).append(p)
        z_max = max(by_z)
        floor = by_z[0]
        top = by_z[z_max]
        # 第 1 层铺满柜长（从柜头到柜门）；easy 区域化（每 SKU 一带）允许略短
        floor_x_min = min(p.x_mm for p in floor)
        floor_x_max = max(p.x_mm + p.length_mm for p in floor)
        threshold = 9000 if solution.profile == "easy" else 11000
        assert floor_x_max - floor_x_min > threshold, "第 1 层应铺满柜长"
        # 顶层（最后一层）重量集中在柜长中间：质心贴近柜长中心
        top_center = sum(p.x_mm + p.length_mm / 2 for p in top) / len(top)
        assert abs(top_center - center) <= 1500, "顶层应集中在柜长中间（质心居中）"
