# -*- coding: utf-8 -*-
"""三目标布局差异与无差异披露机制的回归测试。

背景：用户实测发现三个优化目标对部分订单输出完全相同的布局。修复后
（指纹 + stable 候选门 + easy 区域优先 + 平移兜底升级），本文件锁定：
- 平移归一指纹的语义（整体平移/仅步骤分组不同 → 指纹相同）
- stable 链与基线收敛时跳过候选、产出真正不同的配平布局
- easy 链区域布局优先（件数守恒 + 门端 + 顶层集中 + 步骤/区域上限四重门）
- 仍无法产生差异的场景（装不下等）作为披露基础成立（指纹允许相同）
"""
import re
from collections import Counter

import pytest

from app.models import (
    CargoSpec,
    ContainerSpec,
    Orientation,
    PackRequest,
    Placement,
)
from app.packing import (
    PackedStack,
    StackUnit,
    _build_stack_units,
    _center_lengthwise,
    _center_stacks,
    _easy_region_layout,
    _expand_stacks,
    _layout_fingerprint,
    _merge_pallet_cartons,
    _recenter_blocks,
    _stack_imbalance,
    pack_order,
)
from app.validator import validate_solution

FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


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


def carton(cid: str, quantity: int, **overrides) -> CargoSpec:
    values = {
        "id": cid,
        "sku": cid.upper(),
        "name": cid,
        "kind": "carton",
        "length_mm": 500,
        "width_mm": 400,
        "height_mm": 400,
        "weight_g": 10_000,
        "quantity": quantity,
        "allowed_orientations": ["LWH"],
        "stackable": True,
        "max_layers": 10,
        "max_top_load_g": 200_000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def pallet(pid: str, length_mm: int, width_mm: int, height_mm: int,
           weight_kg: int, quantity: int, **overrides) -> CargoSpec:
    values = {
        "id": pid,
        "sku": pid.upper(),
        "name": pid,
        "kind": "pallet",
        "length_mm": length_mm,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "weight_g": weight_kg * 1000,
        "quantity": quantity,
        "allowed_orientations": ["LWH"],
        "stackable": True,
        "max_layers": 2,
        "max_top_load_g": weight_kg * 2000,
        "fragile": False,
        "must_load": False,
    }
    values.update(overrides)
    return CargoSpec(**values)


def make_placement(cargo_id: str, x: int, y: int, z: int,
                   rotation: Orientation = Orientation.LWH) -> Placement:
    return Placement(
        id=f"{cargo_id}-0",
        cargo_id=cargo_id,
        instance_index=0,
        x_mm=x,
        y_mm=y,
        z_mm=z,
        length_mm=500,
        width_mm=400,
        height_mm=400,
        rotation=rotation,
        weight_g=10_000,
        step=1,
    )


def _door_limit(request: PackRequest) -> int:
    return (
        request.container.inner_length_mm
        - request.door_buffer_mm
        - request.container.clearance_mm
    )


def _counts(request: PackRequest, stacks) -> Counter:
    return Counter(p.cargo_id for p in _expand_stacks(request, stacks, "stable"))


# ---------------------------------------------------------------- 测试 1
def test_fingerprint_translation_invariant_step_independent():
    base = [
        make_placement("a", 0, 0, 0),
        make_placement("b", 500, 0, 0),
    ]
    translated = [
        make_placement("a", 3000, 700, 0),
        make_placement("b", 3500, 700, 0),
    ]
    restep = [
        make_placement("a", 0, 0, 0),
        make_placement("b", 500, 0, 0),
    ]
    restep[0].step = 5
    restep[1].step = 9
    # 实例编号洗牌：几何完全相同，仅 instance_index 分配不同（实测 stable 兜底
    # 平移会重排实例编号，1 SKU 30 托曾因此漏披露）
    reindexed = [
        make_placement("a", 0, 0, 0),
        make_placement("a", 500, 0, 0),
        make_placement("b", 1000, 0, 0),
    ]
    reindexed[0].instance_index = 3
    reindexed[1].instance_index = 1
    reindexed[2].instance_index = 0
    reindexed_base = [
        make_placement("a", 0, 0, 0),
        make_placement("a", 500, 0, 0),
        make_placement("b", 1000, 0, 0),
    ]
    reindexed_base[0].instance_index = 0
    reindexed_base[1].instance_index = 1
    reindexed_base[2].instance_index = 2

    assert _layout_fingerprint(base) == _layout_fingerprint(translated), "整体平移后指纹应不变"
    assert _layout_fingerprint(base) == _layout_fingerprint(restep), "仅步骤分组不同指纹应不变"
    assert _layout_fingerprint(reindexed) == _layout_fingerprint(reindexed_base), (
        "仅实例编号洗牌指纹应不变（编号不反映几何）"
    )

    moved_one = [
        make_placement("a", 50, 0, 0),
        make_placement("b", 500, 0, 0),
    ]
    assert _layout_fingerprint(base) != _layout_fingerprint(moved_one), "单件移动不是整体平移，指纹应变化"

    rotated = [
        make_placement("a", 0, 0, 0, rotation=Orientation.WLH),
        make_placement("b", 500, 0, 0),
    ]
    assert _layout_fingerprint(base) != _layout_fingerprint(rotated), "朝向不同指纹应变化"

    empty = _layout_fingerprint([])
    assert FINGERPRINT_RE.match(empty), "空列表也应返回合法指纹"


# ---------------------------------------------------------------- 测试 2
def test_build_solution_exposes_fingerprint(pack_by_goal):
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[carton("x", 8), carton("y", 8)],
    )
    for goal in ["high_fill", "stable", "easy"]:
        solution = pack_by_goal(request, goal)
        assert FINGERPRINT_RE.match(solution.layout_fingerprint), f"{goal} 应返回 12 位 hex 指纹"


# ---------------------------------------------------------------- 测试 3
def test_easy_region_first_differs_from_high_for_two_sku_cartons(pack_by_goal):
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[carton("x", 24), carton("y", 24)],
    )
    high = pack_by_goal(request, "high_fill")
    easy = pack_by_goal(request, "easy")

    assert easy.loaded_counts == high.loaded_counts, "easy 区域布局应装得与基线一样多"
    assert easy.layout_fingerprint != high.layout_fingerprint, "easy 区域布局应与基线几何不同"
    assert easy.metrics.cargo_zones == 2, "两 SKU 区域布局应为两个区域"
    assert easy.metrics.loading_steps <= max(4, len(request.cargo_items))

    assert validate_solution(
        request.container, request.cargo_items, easy.placements, request.item_gap_mm
    ).valid, "easy 布局必须通过几何校验"

    door_limit = _door_limit(request)
    assert max(p.x_mm + p.length_mm for p in easy.placements) <= door_limit, "easy 布局不得越过门端"


# ---------------------------------------------------------------- 测试 4
def test_easy_region_respects_door_buffer_gate(pack_by_goal):
    """门②：region 候选越过门端时不得被优先采用。

    注：band/块布局同列数时总深相同，region 超门端（门限 11732）的订单
    块布局必然也超自身门限（11432）——这类订单回退链会触发，最终布局
    可能与基线一样填满柜子（door_buffer 是软约束，装不下时允许超门端，
    由前端披露）。本测试用整托 4 SKU 装不下订单锁定门②前提成立且最终
    布局的守恒/区域上限约束仍成立。
    """
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[
            pallet("p1", 650, 650, 1200, 150, 20),
            pallet("p2", 890, 750, 1120, 280, 20),
            pallet("p3", 1080, 800, 1250, 400, 20),
            pallet("p4", 1220, 920, 1150, 530, 20),
        ],
    )
    units = _build_stack_units(request)
    merged = _merge_pallet_cartons(request, units)
    region = _easy_region_layout(request, merged)
    assert region is not None, "该订单区域候选应存在"
    region_placements = _expand_stacks(request, region, "easy")
    door_limit = _door_limit(request)
    assert max(p.x_mm + p.length_mm for p in region_placements) > door_limit, (
        "该订单区域布局应超过门端（否则测不到门②）"
    )

    high = pack_by_goal(request, "high_fill")
    easy = pack_by_goal(request, "easy")

    assert easy.loaded_counts == high.loaded_counts, "门②拒绝后走回退链也必须件数守恒"
    assert easy.metrics.cargo_zones <= max(4, len(request.cargo_items))
    assert easy.metrics.loading_steps <= max(4, len(request.cargo_items))
    assert validate_solution(
        request.container, request.cargo_items, easy.placements, request.item_gap_mm
    ).valid


# ---------------------------------------------------------------- 测试 5
def test_easy_skips_region_when_unload_order_present(pack_by_goal):
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[
            carton("later", 60, unload_order=2),
            carton("first", 60, unload_order=1),
        ],
    )
    easy = pack_by_goal(request, "easy")

    later = [p for p in easy.placements if p.cargo_id == "later"]
    first = [p for p in easy.placements if p.cargo_id == "first"]
    assert later and first, "两类货物都应装入"
    assert max(p.x_mm for p in later) <= min(p.x_mm for p in first), (
        "有先卸后装约束时区域布局应跳过，后卸（order=2）装柜头、先卸（order=1）靠柜门"
    )


# ---------------------------------------------------------------- 测试 6
def test_easy_identical_layout_falls_back_and_discloses(pack_by_goal):
    """整托 4 SKU 装不下场景：easy 允许指纹 == high（前端披露的输入基础），
    但件数守恒与区域上限约束仍须成立。"""
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[
            pallet("p1", 650, 650, 1200, 150, 20),
            pallet("p2", 890, 750, 1120, 280, 20),
            pallet("p3", 1080, 800, 1250, 400, 20),
            pallet("p4", 1220, 920, 1150, 530, 20),
        ],
    )
    high = pack_by_goal(request, "high_fill")
    easy = pack_by_goal(request, "easy")

    assert easy.loaded_counts == high.loaded_counts, "披露场景也必须保持件数守恒"
    assert easy.metrics.cargo_zones <= max(4, len(request.cargo_items))
    assert easy.metrics.loading_steps <= max(4, len(request.cargo_items))
    assert validate_solution(
        request.container, request.cargo_items, easy.placements, request.item_gap_mm
    ).valid
    # 指纹允许与基线相同——这正是前端"布局几何相同"披露的输入


# ---------------------------------------------------------------- 测试 7
def test_stable_skips_candidate_identical_to_high(pack_by_goal):
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[
            pallet("p1", 650, 650, 1200, 150, 30),
            pallet("p2", 890, 750, 1120, 280, 30),
        ],
    )
    high = pack_by_goal(request, "high_fill")
    stable = pack_by_goal(request, "stable")

    assert stable.loaded_counts == high.loaded_counts, "stable 应与基线件数一致"
    assert stable.layout_fingerprint != high.layout_fingerprint, (
        "floor-first 候选与基线收敛时应被跳过，stable 须产出不同几何"
    )
    assert stable.metrics.length_imbalance_pct <= 5, "前后重心偏差应显著改善"
    assert stable.metrics.weight_imbalance_pct < high.metrics.weight_imbalance_pct, (
        "综合重心偏差应优于基线"
    )
    assert validate_solution(
        request.container, request.cargo_items, stable.placements, request.item_gap_mm
    ).valid


# ---------------------------------------------------------------- 测试 8
def _group_stacks(cargos: list[CargoSpec], span: int = 1000, start_x: int = 0) -> list[PackedStack]:
    """按 cargo 分组构造栈：每组 2 个栈、组内 span=span，组间间隔 span。"""

    def unit(cargo: CargoSpec, index: int) -> StackUnit:
        return StackUnit(
            id=f"{cargo.id}-{index}",
            cargo=cargo,
            orientation=Orientation.LWH,
            count=1,
            length_mm=cargo.length_mm,
            width_mm=cargo.width_mm,
            item_height_mm=cargo.height_mm,
            stack_height_mm=cargo.height_mm,
            total_weight_g=cargo.weight_g,
            first_instance_index=index,
            required=False,
        )

    stacks = []
    x = start_x
    for cargo in cargos:
        stacks.append(PackedStack(unit(cargo, 0), x_mm=x, y_mm=500))
        stacks.append(PackedStack(unit(cargo, 1), x_mm=x + span, y_mm=600))
        x += 2 * span
    return stacks


def test_recenter_blocks_centers_heaviest_group():
    """三组栈重排后最重组应居中（三槽时中间槽距柜中心最近）。"""
    heavy = pallet("heavy", 500, 400, 400, 300, 1)
    mid = pallet("mid", 500, 400, 400, 150, 1)
    light = pallet("light", 500, 400, 400, 100, 1)
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[heavy, mid, light],
    )
    stacks = _group_stacks([heavy, mid, light], start_x=1000)
    before = _stack_imbalance(request, stacks)
    result = _recenter_blocks(request, stacks)

    assert result is not None
    assert _counts(request, result) == _counts(request, stacks), "recenter 不得丢件"
    assert _stack_imbalance(request, result) <= before, "重块居中后配平不劣化"

    def group_center(cargo_id: str) -> float:
        group = [s for s in result if s.unit.cargo.id == cargo_id]
        return (min(s.x_mm for s in group) + max(s.x_mm + s.length_mm for s in group)) / 2

    target = request.container.inner_length_mm / 2
    heavy_dev = abs(group_center("heavy") - target)
    mid_dev = abs(group_center("mid") - target)
    light_dev = abs(group_center("light") - target)
    assert heavy_dev <= mid_dev, "最重组应比次重组更靠近柜长中心"
    assert heavy_dev <= light_dev, "最重组应比最轻组更靠近柜长中心"

    for old in stacks:
        match = [s for s in result if s.unit.id == old.unit.id]
        assert len(match) == 1
        assert match[0].y_mm == old.y_mm, "recenter 不得移动 y"
        assert match[0].z_mm == old.z_mm, "recenter 不得移动 z"

    door_limit = _door_limit(request)
    assert max(s.x_mm + s.length_mm for s in result) <= door_limit, "recenter 不得越过门端"


# ---------------------------------------------------------------- 测试 9
def test_recenter_blocks_returns_none_when_unload_or_single_group_or_overlong():
    # 先卸后装约束 → None（注意：约束必须在栈的 cargo 上，recenter 检查栈）
    heavy_unload = pallet("heavy", 500, 400, 400, 300, 1, unload_order=2)
    light_unload = pallet("light", 500, 400, 400, 100, 1, unload_order=1)
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[heavy_unload, light_unload],
    )
    assert _recenter_blocks(request, _group_stacks([heavy_unload, light_unload])) is None

    # 单一组 → None
    heavy = pallet("heavy", 500, 400, 400, 300, 1)
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[heavy],
    )
    assert _recenter_blocks(request, _group_stacks([heavy])) is None

    # 组总长超门限可用长度 → None（各组 span 6000，总和超限）
    def unit(cargo: CargoSpec, index: int) -> StackUnit:
        return StackUnit(
            id=f"{cargo.id}-{index}",
            cargo=cargo,
            orientation=Orientation.LWH,
            count=1,
            length_mm=6000,
            width_mm=cargo.width_mm,
            item_height_mm=cargo.height_mm,
            stack_height_mm=cargo.height_mm,
            total_weight_g=cargo.weight_g,
            first_instance_index=index,
            required=False,
        )

    light = pallet("light", 500, 400, 400, 100, 1)
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[heavy, light],
    )
    overlong = [
        PackedStack(unit(heavy, 0), x_mm=0, y_mm=0),
        PackedStack(unit(light, 0), x_mm=6000, y_mm=0),
    ]
    assert _recenter_blocks(request, overlong) is None


# ---------------------------------------------------------------- 测试 10
def test_center_lengthwise_clamps_to_door_and_clearance():
    request = PackRequest(
        container=forty_hq(),
        door_buffer_mm=300,
        cargo_items=[pallet("a", 2000, 500, 400, 100, 1)],
    )
    door_limit = _door_limit(request)
    c = request.container.clearance_mm

    def unit(cargo: CargoSpec, index: int) -> StackUnit:
        return StackUnit(
            id=f"{cargo.id}-{index}",
            cargo=cargo,
            orientation=Orientation.LWH,
            count=1,
            length_mm=cargo.length_mm,
            width_mm=cargo.width_mm,
            item_height_mm=cargo.height_mm,
            stack_height_mm=cargo.height_mm,
            total_weight_g=cargo.weight_g,
            first_instance_index=index,
            required=False,
        )

    cargo = pallet("a", 2000, 500, 400, 100, 1)
    # 贴柜头的小布局 → 居中平移
    stacks = [PackedStack(unit(cargo, 0), x_mm=0, y_mm=0)]
    moved = _center_lengthwise(request, stacks)
    assert min(s.x_mm for s in moved) >= c, "不得越过 clearance 下界"
    assert max(s.x_mm + s.length_mm for s in moved) <= door_limit, "不得越过门端"
    assert max(s.x_mm + s.length_mm for s in moved) > 2000, "应发生居中平移"

    # 总长超过可用柜长 → 原样返回
    long_cargo = pallet("b", 12000, 2350, 400, 100, 1)
    long_stacks = [PackedStack(unit(long_cargo, 0), x_mm=0, y_mm=0)]
    unchanged = _center_lengthwise(request, long_stacks)
    assert [s.x_mm for s in unchanged] == [s.x_mm for s in long_stacks], (
        "超长布局应原样返回，由调用方门端检查拒绝"
    )
