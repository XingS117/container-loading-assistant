from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Literal

from rectpack import (
    GuillotineBssfSas,
    MaxRectsBaf,
    MaxRectsBssf,
)

from .models import (
    CargoSpec,
    CenterOfGravity,
    Orientation,
    PackRequest,
    PackResponse,
    PackingSolution,
    Placement,
    SolutionMetrics,
    Zone,
)
from .validator import ValidationResult, validate_solution


class PackingFailure(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __reduce__(self):
        return type(self), (self.code, self.message, self.hint)


#: 布局校验错误码 → 用户可执行的调整建议（中文）。
#: 校验失败且错误码全部命中此表时，返回 422 并附建议；否则视为内部缺陷返回 500。
LAYOUT_ADVICE: dict[str, str] = {
    "UNKNOWN_CARGO": "请求数据异常，请刷新页面后重新提交",
    "DUPLICATE_INSTANCE": "请求数据异常，请刷新页面后重新提交",
    "INSTANCE_OUT_OF_RANGE": "货物数量与输入不符，请检查各货物的数量",
    "ORIENTATION_NOT_ALLOWED": "货物使用了未允许的摆放朝向，请调整“摆放朝向”",
    "DIMENSIONS_MISMATCH": "请求数据异常，请刷新页面后重新提交",
    "WEIGHT_MISMATCH": "货物重量与输入不符，请检查各货物的重量",
    "OUT_OF_BOUNDS": "货物超出柜体有效边界，请检查货物尺寸或减少数量",
    "DOOR_FIT": "部分货物按当前朝向无法通过柜门，请调整“摆放朝向”或改小货物尺寸",
    "OVERLAP": "货物在柜内发生空间重叠，请检查货物尺寸或减少数量",
    "CLEARANCE_VIOLATION": "货物之间的间隙小于设置值，请减小“货物间隙”或调整货物尺寸",
    "PAYLOAD_EXCEEDED": "装载总重量超过柜体最大载重，请减少货物数量或更换更大柜型",
    "MUST_LOAD_MISSING": "必装货物未能全部装入，请减少其他货物数量或更换更大柜型",
    "UNSUPPORTED": "高层货物底面未得到完整支撑，请调整货物尺寸/数量或摆放方式",
    "PALLET_STACKING": "整托上方不能叠放整托，如需叠放请开启该整托的“可叠”选项并设置层数/顶部承重",
    "STACKING_SPEC_MISMATCH": "只有相同规格参数的整托货物才能相互叠放，请调整叠放位置或拆分底层货物",
    "NON_STACKABLE": "不可叠放货物的上方不应有其他货物，请取消该货物的“可叠”选项",
    "FRAGILE_STACKING": "易碎货物上方不能叠放其他货物，请移除其上方的货物或关闭“可叠”",
    "MAX_LAYERS_EXCEEDED": "货物堆叠层数超过限制，请调大“最大层数”或减少数量",
    "TOP_LOAD_EXCEEDED": "货物顶部承重不足，请调大“顶部承重”或减少该位置货物数量",
}

SOFT_LAYOUT_FALLBACK_WARNING = "未完全满足上层连续集中要求，当前为次优方案，请现场复核"
PROFILE_NAMES = ("high_fill", "stable", "easy")


def _request_for_profile(
    request: PackRequest,
    profile: Literal["high_fill", "stable", "easy"],
) -> PackRequest:
    """Overlay one profile's advisory fields without changing request physics."""
    hint = request.ai_layout_hint
    if not isinstance(hint, dict):
        return request
    profiles = hint.get("profiles")
    profile_hint = profiles.get(profile) if isinstance(profiles, dict) else None
    if not isinstance(profile_hint, dict):
        return request
    merged = dict(hint)
    for key in ("sku_order", "orientations", "row_groups", "zone_order", "max_zones"):
        if key in profile_hint:
            merged[key] = profile_hint[key]
    return request.model_copy(update={"ai_layout_hint": merged})


@dataclass(frozen=True)
class StackUnit:
    id: str
    cargo: CargoSpec
    orientation: Orientation
    count: int
    length_mm: int
    width_mm: int
    item_height_mm: int
    stack_height_mm: int
    total_weight_g: int
    first_instance_index: int
    required: bool

    @property
    def volume_mm3(self) -> int:
        return (
            self.cargo.length_mm
            * self.cargo.width_mm
            * self.cargo.height_mm
            * self.count
        )


@dataclass(frozen=True)
class CompositeUnit:
    """A pallet (count=1) with carton stacks packed on its top surface.

    Each on_top entry records the carton stack together with its (x, y)
    offset inside the pallet top surface, so multi-stack top layouts keep
    distinct footprints when expanded.
    """
    pallet: StackUnit
    on_top: tuple[tuple[StackUnit, int, int], ...] = ()

    @property
    def id(self) -> str:
        return self.pallet.id

    @property
    def cargo(self) -> CargoSpec:
        return self.pallet.cargo

    @property
    def required(self) -> bool:
        return self.pallet.required

    @property
    def orientation(self) -> Orientation:
        return self.pallet.orientation

    @property
    def length_mm(self) -> int:
        return self.pallet.length_mm

    @property
    def width_mm(self) -> int:
        return self.pallet.width_mm

    @property
    def count(self) -> int:
        return self.pallet.count + sum(stack.count for stack, _, _ in self.on_top)

    @property
    def total_weight_g(self) -> int:
        return self.pallet.total_weight_g + sum(stack.total_weight_g for stack, _, _ in self.on_top)

    @property
    def volume_mm3(self) -> int:
        return self.pallet.volume_mm3 + sum(stack.volume_mm3 for stack, _, _ in self.on_top)


@dataclass(frozen=True)
class PackedStack:
    unit: StackUnit
    x_mm: int
    y_mm: int
    rotated: bool = False
    step: int | None = None
    # 列起点 z 偏移（相对 clearance，互叠布局用）：默认 0 = 从柜底起，
    # 互叠时上层件叠在其它 SKU 件顶面（z_mm = 下方支撑件顶面高度）
    z_mm: int = 0

    @property
    def length_mm(self) -> int:
        return self.unit.width_mm if self.rotated else self.unit.length_mm

    @property
    def width_mm(self) -> int:
        return self.unit.length_mm if self.rotated else self.unit.width_mm

    @property
    def orientation(self) -> Orientation:
        if not self.rotated:
            return self.unit.orientation
        return SWAP_ORIENTATIONS[self.unit.orientation]


@dataclass
class Block:
    """一个 SKU 的集中装载块：块内网格 columns×rows，每底位叠 layers 件。

    rows = 当前行数（底层底位数 = columns×rows）；flat_rows = 全平铺行数
    （全部件放第 1 层所需行数）。布局时先平铺底层、剩余件叠上层集中在中间。
    """
    sku_id: str
    cargo: CargoSpec
    length_mm: int
    width_mm: int
    height_mm: int
    layers: int
    columns: int
    rows: int
    flat_rows: int
    block_length_mm: int
    block_width_mm: int
    pieces: int
    total_weight_g: int


SWAP_ORIENTATIONS = {
    Orientation.LWH: Orientation.WLH,
    Orientation.WLH: Orientation.LWH,
    Orientation.LHW: Orientation.HLW,
    Orientation.HLW: Orientation.LHW,
    Orientation.WHL: Orientation.HWL,
    Orientation.HWL: Orientation.WHL,
}


PACK_ALGOS = (MaxRectsBssf, MaxRectsBaf, GuillotineBssfSas)
GENERIC_CANDIDATE_LIMIT = 2_500
AI_GUIDED_CANDIDATE_LIMIT = 1_200
FLOOR_COMBINATION_LIMIT = 128


def _stack_capacity(
    cargo: CargoSpec,
    orientation: Orientation,
    available_height: int,
    gap: int,
) -> int:
    _, _, item_height = cargo.dimensions_for(orientation)
    # Horizontal clearance must not create a vertical air gap between stacked items.
    by_height = available_height // item_height
    if not cargo.stackable or cargo.fragile:
        return min(1, by_height)
    by_load = cargo.max_top_load_g // cargo.weight_g + 1
    return max(0, min(cargo.max_layers, by_height, by_load))


def _best_orientation(
    request: PackRequest,
    cargo: CargoSpec,
    quantity: int,
    profile: Literal["high_fill", "stable"] = "high_fill",
) -> Orientation | None:
    c = request.container.clearance_mm
    available_length = request.container.inner_length_mm - 2 * c
    available_width = request.container.inner_width_mm - 2 * c
    available_height = request.container.inner_height_mm - 2 * c
    choices: list[tuple[float, int, int, str, Orientation]] = []
    for orientation in cargo.allowed_orientations:
        length, width, height = cargo.dimensions_for(orientation)
        capacity = _stack_capacity(
            cargo,
            orientation,
            available_height,
            request.item_gap_mm,
        )
        if (
            capacity < 1
            or length > available_length
            or width > available_width
            or width > request.container.door_width_mm - 2 * c
            or height > request.container.door_height_mm - 2 * c
        ):
            continue
        floor_per_piece = (length * width) / capacity
        full_stacks, remainder = divmod(quantity, capacity)
        vertical_cg = height * (
            full_stacks * capacity**2 + remainder**2
        ) / (2 * quantity)
        primary = vertical_cg if profile == "stable" else floor_per_piece
        secondary = floor_per_piece if profile == "stable" else height
        choices.append((primary, secondary, max(length, width), orientation.value, orientation))
    if not choices:
        return None
    choices.sort()
    return choices[0][-1]


def _hinted_orientation(
    request: PackRequest,
    cargo: CargoSpec,
) -> Orientation | None:
    hint = request.ai_layout_hint or {}
    orientations = hint.get("orientations")
    value = orientations.get(cargo.id) if isinstance(orientations, dict) else None
    if not isinstance(value, str):
        return None
    try:
        orientation = Orientation(value)
    except ValueError:
        return None
    if orientation not in cargo.allowed_orientations:
        return None
    c = request.container.clearance_mm
    length, width, height = cargo.dimensions_for(orientation)
    if (
        _stack_capacity(
            cargo,
            orientation,
            request.container.inner_height_mm - 2 * c,
            request.item_gap_mm,
        ) < 1
        or length > request.container.inner_length_mm - 2 * c
        or width > request.container.inner_width_mm - 2 * c
        or width > request.container.door_width_mm - 2 * c
        or height > request.container.door_height_mm - 2 * c
    ):
        return None
    return orientation


def _build_stack_units(
    request: PackRequest,
    quantity_limits: dict[str, int] | None = None,
    profile: Literal["high_fill", "stable"] = "high_fill",
) -> list[StackUnit]:
    available_height = request.container.inner_height_mm - 2 * request.container.clearance_mm
    units: list[StackUnit] = []
    for cargo in request.cargo_items:
        target_quantity = quantity_limits.get(cargo.id, cargo.quantity) if quantity_limits else cargo.quantity
        orientation = _hinted_orientation(request, cargo) or _best_orientation(
            request,
            cargo,
            target_quantity,
            profile,
        )
        if orientation is None:
            if cargo.must_load:
                raise PackingFailure(
                    "MUST_LOAD_UNSATISFIED",
                    f"必装货物 {cargo.sku} 无法按允许方向通过柜门或放入柜内",
                    hint="请调整该货物的“摆放朝向”或改小尺寸，或更换更大柜型",
                )
            continue
        length, width, height = cargo.dimensions_for(orientation)
        capacity = _stack_capacity(
            cargo,
            orientation,
            available_height,
            request.item_gap_mm,
        )
        remaining = target_quantity
        instance_index = 0
        stack_index = 0
        while remaining:
            count = min(capacity, remaining)
            units.append(
                StackUnit(
                    id=f"{cargo.id}-stack-{stack_index}",
                    cargo=cargo,
                    orientation=orientation,
                    count=count,
                    length_mm=length,
                    width_mm=width,
                    item_height_mm=height,
                    stack_height_mm=count * height,
                    total_weight_g=count * cargo.weight_g,
                    first_instance_index=instance_index,
                    required=cargo.must_load,
                )
            )
            remaining -= count
            instance_index += count
            stack_index += 1
    return units


def _build_sku_blocks(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    strategy: str,
) -> list[Block] | None:
    """按 SKU 分组构建 Block。同 SKU 件数合并；layers 由高度/承重/可叠决定。"""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_width = request.container.inner_width_mm - 2 * c
    available_height = request.container.inner_height_mm - 2 * c
    by_sku: dict[str, list[StackUnit | CompositeUnit]] = {}
    for unit in units:
        by_sku.setdefault(unit.cargo.id, []).append(unit)
    blocks: list[Block] = []
    for sku_id, group in by_sku.items():
        cargo = group[0].cargo
        has_composite = any(isinstance(u, CompositeUnit) for u in group)
        # 底面件数：CompositeUnit 只算托盘件（count=pallet.count=1），
        # 上托散箱不占底面位置（随托盘展开，轻在上）
        total = sum(
            (u.pallet.count if isinstance(u, CompositeUnit) else u.count) for u in group
        )
        unit = group[0]
        if isinstance(unit, CompositeUnit):
            unit = unit.pallet  # CompositeUnit 取托盘栈计算高度/footprint
        # footprint：占宽最小的朝向（受门宽约束，简化取 LWH 或旋转后更窄者）
        length_mm = unit.length_mm
        width_mm = unit.width_mm
        swapped = SWAP_ORIENTATIONS.get(unit.orientation)
        if (
            # 仅 L/W 交换（LWH↔WLH，高度轴不变）：保证旋转后 item_height 与块高一致，
            # 避免 LHW/WHL 这类换高轴旋转造成 layers 仍按未旋转高度计算而超柜高
            unit.orientation in (Orientation.LWH, Orientation.WLH)
            and swapped in cargo.allowed_orientations
            and unit.length_mm <= request.container.door_width_mm - 2 * c
            and unit.length_mm < unit.width_mm
            # 含 CompositeUnit（托盘+上托散箱）的 SKU 组禁止 footprint swap：
            # CompositeUnit 不旋转，on_top 偏移基于未旋转托盘顶面（与 _layer_layout
            # 约束一致）；swap 后托盘顶面变小而散箱偏移未随旋转同步 → 散箱超出托盘
            # 顶面 → validator UNSUPPORTED（公网实测 LAYOUT_NOT_FEASIBLE，
            # hint=高层货物底面未得到完整支撑）。保持原 footprint 即可消除。
            and not has_composite
        ):
            # 旋转后更窄（原长变宽）：取占宽最小朝向
            length_mm, width_mm = unit.width_mm, unit.length_mm
        # 叠高层数：可叠 + 高度 + 承重（按 max_layers、柜高与 max_top_load 取最小）
        if cargo.stackable and not cargo.fragile:
            max_by_height = available_height // unit.item_height_mm
            # 承重层数 = 能承受的上层数 + 1（底层自身），与 _stack_capacity 的
            # by_load = max_top_load_g // weight_g + 1 保持一致；缺失 +1 会把
            # "可叠 2 层"误判成 1 层（如 280kg/承重500kg），导致整托被强制平铺、
            # 占满柜长后其它 SKU 装不下。
            max_by_load = cargo.max_top_load_g // cargo.weight_g + 1
            layers = max(1, min(cargo.max_layers, max_by_height, max_by_load))
        else:
            layers = 1
        if has_composite:
            # CompositeUnit 已带上托散箱（占满托盘上方空间），块内每底位整托放置不可叠
            layers = 1
        columns = max(1, usable_width // (width_mm + gap))
        # 行数：min_rows = 全部件叠满 layers 层所需行数（块长下限）；
        # flat_rows = 全部件平铺第 1 层所需行数（块长上限，底层铺满）。
        # 布局时在柜长预算内尽量多铺底层（rows 趋向 flat_rows），
        # 剩余件数叠到第 2 层集中在中间 —— 遵循"先铺满底层"规则。
        min_rows = max(1, (total + columns * layers - 1) // (columns * layers))
        flat_rows = max(min_rows, (total + columns - 1) // columns)
        rows = min_rows
        block_length = rows * length_mm + max(0, rows - 1) * gap
        block_width = columns * width_mm + max(0, columns - 1) * gap
        blocks.append(Block(
            sku_id=sku_id, cargo=cargo, length_mm=length_mm, width_mm=width_mm,
            height_mm=unit.item_height_mm, layers=layers, columns=columns,
            rows=rows, flat_rows=flat_rows, block_length_mm=block_length,
            block_width_mm=block_width,
            pieces=total, total_weight_g=sum(u.total_weight_g for u in group),
        ))
    return blocks


def _grow_block_rows(
    blocks: list[Block],
    budget: int,
    gap: int,
    weight_first: bool = False,
) -> None:
    """在柜长预算内尽量给每块加行（铺满底层）。

    每块当前 rows = min_rows（全部件叠满 layers 层）。预算有余时按优先级给块
    加行：默认按"每毫米柜长可增加的底位数"（columns/length）降序（装得多）；
    weight_first=True（更稳妥）时按块总重降序（重块先铺底层 → 重块居中配平
    能力不被小 footprint 块拉偏）。加 1 行 → 底层多 columns 个底位、块长增加
    length+gap。剩余预算用完即停，尽量把底层铺满、剩余件数叠到第 2 层集中
    在中间。
    """
    total_len = sum(b.block_length_mm + gap for b in blocks) - gap
    if weight_first:
        order = sorted(blocks, key=lambda b: (-b.total_weight_g, b.sku_id))
    else:
        order = sorted(
            blocks,
            key=lambda b: (-(b.columns / b.length_mm), b.sku_id),
        )
    for b in order:
        while b.rows < b.flat_rows:
            added = b.length_mm + gap
            if total_len + added > budget:
                break
            b.rows += 1
            b.block_length_mm += added
            total_len += added


def _sku_block_layout(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    strategy: str,
) -> list[PackedStack] | None:
    """SKU 块布局主入口：构建块 → 铺满底层行数优化 → 策略排序 → 逐块放置。

    接受 StackUnit 与 CompositeUnit（托盘+上托散箱）：CompositeUnit 块内整托
    放置（每底位 1 个托盘件），上托散箱不占底面位置，由 _expand_stacks 展开。
    块排序遵循 cargo.unload_order（后卸先进柜头）。

    布局遵循"先铺满底层"规则：在柜长预算内每块尽量平铺（rows 趋向
    flat_rows），底层铺满后再把剩余件数叠到第 2 层，集中在距柜长中心
    近的底位（两头低中间高，重心居中）。
    """
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    door_buffer = request.door_buffer_mm
    usable_width = request.container.inner_width_mm - 2 * c
    blocks = _build_sku_blocks(request, units, strategy)
    if not blocks:
        return None
    by_sku: dict[str, list[StackUnit | CompositeUnit]] = {}
    for unit in units:
        by_sku.setdefault(unit.cargo.id, []).append(unit)
    # fill 策略：若单 SKU 块叠满 layers 层仍超长（如 706 件散箱），返回 None
    # 由调用方回退分层铺满
    if strategy == "fill":
        for b in blocks:
            if b.block_length_mm > usable_length - door_buffer:
                return None
    hint = request.ai_layout_hint or {}
    hinted_order = hint.get("sku_order")
    ai_order = (
        {cargo_id: index for index, cargo_id in enumerate(hinted_order)}
        if isinstance(hinted_order, list)
        else {}
    )
    if strategy == "fill":
        ordered = sorted(
            blocks,
            key=lambda b: (
                -b.cargo.unload_order,
                ai_order.get(b.sku_id, len(ai_order)),
                -b.block_length_mm * b.block_width_mm,
                b.sku_id,
            ),
        )
    elif strategy == "easy":
        order_map = {item.id: i for i, item in enumerate(request.cargo_items)}
        ordered = sorted(
            blocks,
            key=lambda b: (
                -b.cargo.unload_order,
                ai_order.get(b.sku_id, order_map.get(b.sku_id, 10**9)),
                b.sku_id,
            ),
        )
    else:  # balance
        if any(b.cargo.unload_order for b in blocks):
            # 先卸后装硬约束优先：unload_order 降序从柜头铺（不做中心槽配平）
            ordered = sorted(
                blocks,
                key=lambda b: (
                    -b.cargo.unload_order,
                    ai_order.get(b.sku_id, len(ai_order)),
                    -b.total_weight_g,
                    b.sku_id,
                ),
            )
            center_slots = False
        else:
            # 无先卸后装约束：保持重块从柜长中心向外（中心槽配平）
            ordered = sorted(
                blocks,
                key=lambda b: (
                    -b.total_weight_g,
                    ai_order.get(b.sku_id, len(ai_order)),
                    b.sku_id,
                ),
            )
            center_slots = True
    # 铺满底层：柜长预算内尽量给每块加行（行数从 min_rows 趋向 flat_rows）。
    # 排序基于 grow 前的块长（策略语义稳定：fill 体积降序、easy 输入顺序、
    # balance 重块居中）；grow 只改变块内行数与块长，不改变块间顺序。
    # balance（更稳妥）重块优先铺底层，避免小 footprint 块拉偏重心。
    _grow_block_rows(
        blocks,
        usable_length - door_buffer,
        gap,
        weight_first=(strategy == "balance" and center_slots),
    )
    total_len = sum(b.block_length_mm + gap for b in ordered) - gap
    if total_len > usable_length - door_buffer:
        return None
    block_x: dict[str, int] = {}
    if strategy == "balance" and center_slots:
        slots = len(ordered)
        # 物理槽从左到右 = 离中心越近越靠中间：最重块居中，次重向两侧
        order_idx = sorted(range(slots), key=lambda i: (abs(i - (slots - 1) / 2), i))
        slot_to_weight = [order_idx.index(slot) for slot in range(slots)]
        slot_x: list[int] = []
        # 中心槽起点除 ≥0 外还须 ≥clearance（否则最左块 x<clearance → OUT_OF_BOUNDS，Critical-2）
        cursor = max(c, (usable_length - door_buffer - total_len) // 2)
        for slot in range(slots):
            slot_x.append(cursor)
            cursor += ordered[slot_to_weight[slot]].block_length_mm + gap
        # 起点被 clearance 下界抬升后最右可能越界 → 返回 None 交由回退链
        if slot_x[-1] + ordered[slot_to_weight[-1]].block_length_mm > usable_length - door_buffer:
            return None
        for weight_pos, slot in enumerate(order_idx):
            block_x[ordered[weight_pos].sku_id] = slot_x[slot]
    else:
        cursor = c
        for b in ordered:
            block_x[b.sku_id] = cursor
            cursor += b.block_length_mm + gap
    placed: list[PackedStack] = []
    step = 1
    for b in ordered:
        group = by_sku[b.sku_id]
        has_composite = any(isinstance(s, CompositeUnit) for s in group)
        instance_pool: list[int] = []
        for stack in group:
            if isinstance(stack, CompositeUnit):
                pallet_stack = stack.pallet
                for i in range(pallet_stack.count):
                    instance_pool.append(pallet_stack.first_instance_index + i)
            else:
                for i in range(stack.count):
                    instance_pool.append(stack.first_instance_index + i)
        if has_composite:
            # 托盘块整托放置：每底位一个 CompositeUnit（pallet + on_top），
            # 上托散箱不占底面位置，由 _expand_stacks 展开；块内不可叠。
            # group 可能混合 CompositeUnit 与未上托的 StackUnit，按类型分别取件。
            group_idx = 0
            piece_idx = 0
            x_cursor = block_x[b.sku_id]
            for r in range(b.rows):
                y_cursor = c
                for col in range(b.columns):
                    remaining = b.pieces - piece_idx
                    if remaining <= 0 or group_idx >= len(group):
                        break
                    base = group[group_idx]
                    take = min(
                        base.pallet.count if isinstance(base, CompositeUnit) else base.count,
                        remaining,
                    )
                    piece_idx += take
                    first = instance_pool[piece_idx - take]
                    if isinstance(base, CompositeUnit):
                        pallet = base.pallet
                        if (pallet.length_mm, pallet.width_mm) != (b.length_mm, b.width_mm):
                            pallet = replace(
                                pallet,
                                length_mm=b.length_mm,
                                width_mm=b.width_mm,
                                orientation=SWAP_ORIENTATIONS.get(pallet.orientation, pallet.orientation),
                            )
                        replaced_pallet = replace(
                            pallet,
                            count=take,
                            stack_height_mm=take * b.height_mm,
                            total_weight_g=take * base.cargo.weight_g,
                            first_instance_index=first,
                        )
                        unit = replace(base, pallet=replaced_pallet)
                    else:
                        if (base.length_mm, base.width_mm) != (b.length_mm, b.width_mm):
                            base = replace(
                                base,
                                length_mm=b.length_mm,
                                width_mm=b.width_mm,
                                orientation=SWAP_ORIENTATIONS.get(base.orientation, base.orientation),
                            )
                        unit = replace(
                            base,
                            count=take,
                            stack_height_mm=take * b.height_mm,
                            total_weight_g=take * base.cargo.weight_g,
                            first_instance_index=first,
                        )
                    placed.append(PackedStack(unit=unit, x_mm=x_cursor, y_mm=y_cursor, step=step))
                    y_cursor += b.width_mm + gap
                    group_idx += 1
                x_cursor += b.length_mm + gap
            step += 1
            continue
        # 可叠 StackUnit 块：先铺满底层，剩余件数叠到第 2 层集中在中间。
        # 底层底位数 = columns×rows（行数已按柜长预算尽量平铺）；每底位
        # base 件（= pieces//bottom，受 layers 上限约束），剩余 rem 件
        # 分散 +1 到距柜长中心近的底位 —— 中间高、两头低，重心居中。
        bottom = min(b.columns * b.rows, b.pieces)
        base = min(b.layers, b.pieces // bottom) if bottom else 0
        rem = b.pieces - base * bottom
        counts = [base] * bottom
        if rem > 0:
            center = request.container.inner_length_mm / 2
            slot_order = sorted(
                range(bottom),
                key=lambda i: abs(
                    block_x[b.sku_id]
                    + (i // b.columns) * (b.length_mm + gap)
                    + b.length_mm / 2
                    - center
                ),
            )
            for i in slot_order:
                if rem <= 0:
                    break
                counts[i] += 1
                rem -= 1
        piece_idx = 0
        x_cursor = block_x[b.sku_id]
        for r in range(b.rows):
            y_cursor = c
            for col in range(b.columns):
                slot = r * b.columns + col
                if slot >= bottom:
                    break
                count = counts[slot]
                if count <= 0:
                    break
                first = instance_pool[piece_idx]
                piece_idx += count
                base = group[0]
                if (base.length_mm, base.width_mm) != (b.length_mm, b.width_mm):
                    base = replace(
                        base,
                        length_mm=b.length_mm,
                        width_mm=b.width_mm,
                        orientation=SWAP_ORIENTATIONS.get(base.orientation, base.orientation),
                    )
                unit = replace(
                    base,
                    count=count,
                    stack_height_mm=count * b.height_mm,
                    total_weight_g=count * base.cargo.weight_g,
                    first_instance_index=first,
                )
                placed.append(PackedStack(unit=unit, x_mm=x_cursor, y_mm=y_cursor, step=step))
                y_cursor += b.width_mm + gap
            x_cursor += b.length_mm + gap
        step += 1
    return placed


def _select_payload_units(
    request: PackRequest,
    units: list[StackUnit],
    strategy: str,
) -> list[StackUnit]:
    capacity = request.container.max_payload_g
    selected: list[StackUnit] = []
    for unit in _ordered_units(units, strategy):
        if unit.total_weight_g <= capacity:
            selected.append(unit)
            capacity -= unit.total_weight_g
            continue
        possible_count = capacity // unit.cargo.weight_g
        if possible_count > 0 and not unit.required:
            selected.append(
                replace(
                    unit,
                    count=possible_count,
                    stack_height_mm=possible_count * unit.item_height_mm,
                    total_weight_g=possible_count * unit.cargo.weight_g,
                )
            )
            capacity -= possible_count * unit.cargo.weight_g
        if unit.required:
            raise PackingFailure(
                "MUST_LOAD_UNSATISFIED",
                f"必装货物 {unit.cargo.sku} 导致总重量超过柜体载重",
                hint="请减少其他货物数量，或更换载重更大的柜型",
            )
    return selected


def _try_add_to_pallet_top(
    packer,
    carton: StackUnit,
    request: PackRequest,
) -> tuple[object, StackUnit] | tuple[None, None]:
    """Add a carton stack to a pallet-top bin; rotation only if allowed.

    Returns (rect, placed_unit); placed_unit is a rotated variant when the
    stack was placed rotated, so expansion uses consistent dimensions.
    """
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    door_usable_width = request.container.door_width_mm - 2 * c
    rect = packer.add_rect(carton.length_mm + gap, carton.width_mm + gap, rid=carton.id)
    if rect is not None:
        return rect, carton
    if isinstance(carton, CompositeUnit):
        # CompositeUnit（托盘+上叠散箱）不允许旋转：on_top 偏移基于未旋转托盘顶面
        return None, None
    swapped_orientation = SWAP_ORIENTATIONS.get(carton.orientation)
    if (
        swapped_orientation in carton.cargo.allowed_orientations
        and carton.length_mm <= door_usable_width
    ):
        rect = packer.add_rect(carton.width_mm + gap, carton.length_mm + gap, rid=carton.id)
        if rect is not None:
            return rect, replace(
                carton,
                length_mm=carton.width_mm,
                width_mm=carton.length_mm,
                orientation=swapped_orientation,
            )
    return None, None


def _merge_pallet_cartons(
    request: PackRequest,
    units: list[StackUnit],
) -> list[CompositeUnit | StackUnit]:
    """Merge stackable carton stacks onto pallet tops (greedy, deterministic)."""
    pallets = [unit for unit in units if unit.cargo.kind == "pallet"]
    cartons = [unit for unit in units if unit.cargo.kind == "carton"]
    if not pallets or not cartons:
        return list(units)
    c = request.container.clearance_mm
    available_height = request.container.inner_height_mm - 2 * c
    pallets.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    # 重的在下面、轻的在上面：重量小的散箱优先上托到托盘顶面，
    # 重量大的散箱保留独立栈铺底层（降低整柜重心）。
    cartons.sort(key=lambda unit: (unit.total_weight_g, unit.id))
    merged: list[CompositeUnit | StackUnit] = []
    remaining: list[StackUnit] = cartons
    for pallet in pallets:
        if pallet.cargo.max_top_load_g <= 0:
            merged.append(pallet)
            continue
        gap = request.item_gap_mm
        packer = MaxRectsBssf(
            pallet.length_mm + gap, pallet.width_mm + gap, rot=False
        )
        assigned: list[tuple[StackUnit, int, int]] = []
        load_left = pallet.cargo.max_top_load_g
        height_left = available_height - pallet.stack_height_mm
        still: list[StackUnit] = []
        for carton in remaining:
            if carton.cargo.fragile:
                still.append(carton)
                continue
            if carton.total_weight_g > load_left:
                still.append(carton)
                continue
            # 整栈上托（不拆栈，避免 instance 空洞）：整栈高度放得下
            # 托盘顶面才上托；顶面空间/承重不足的整栈保留独立栈铺底层
            if carton.stack_height_mm > height_left:
                still.append(carton)
                continue
            on_top_unit = replace(carton, stack_height_mm=carton.stack_height_mm)
            rect, placed_carton = _try_add_to_pallet_top(
                packer, on_top_unit, request
            )
            if rect is None:
                still.append(carton)
                continue
            assigned.append((placed_carton, int(rect.x), int(rect.y)))
            load_left -= placed_carton.total_weight_g
        remaining = still
        if assigned:
            merged.append(CompositeUnit(pallet=pallet, on_top=tuple(assigned)))
        else:
            merged.append(pallet)
    merged.extend(remaining)
    # 上托会打乱同 SKU 件 instance 的连续性（柱高分配依赖连续编号），
    # 返回前按 SKU 重新编号：该 SKU 的件（上托件 + 独立栈件）从 0 起连续分配
    next_idx: dict[str, int] = {}

    def renumber(stack: StackUnit) -> StackUnit:
        sku = stack.cargo.id
        nxt = next_idx.get(sku, 0)
        next_idx[sku] = nxt + stack.count
        return replace(stack, first_instance_index=nxt)

    renumbered: list[CompositeUnit | StackUnit] = []
    for unit in merged:
        if isinstance(unit, CompositeUnit):
            # 托盘本身也参与重排（同 SKU 托盘件连续编号）
            renumbered.append(
                replace(
                    unit,
                    pallet=renumber(unit.pallet),
                    on_top=tuple(
                        (renumber(stack), ox, oy) for stack, ox, oy in unit.on_top
                    ),
                )
            )
        else:
            renumbered.append(renumber(unit))
    return renumbered


def _ordered_units(
    units: list[StackUnit],
    strategy: str,
    ai_order: list[str] | None = None,
) -> list[StackUnit]:
    required = [unit for unit in units if unit.required]
    optional = [unit for unit in units if not unit.required]
    key_map = {
        "volume": lambda unit: (-unit.volume_mm3, -unit.length_mm * unit.width_mm, unit.id),
        "footprint": lambda unit: (-unit.length_mm * unit.width_mm, -unit.volume_mm3, unit.id),
        "weight": lambda unit: (-unit.total_weight_g, -unit.volume_mm3, unit.id),
        "lightweight": lambda unit: (unit.total_weight_g, -unit.volume_mm3, unit.id),
        "pieces": lambda unit: (-unit.count, unit.total_weight_g, unit.id),
        "sku": lambda unit: (unit.cargo.id, -unit.length_mm * unit.width_mm, unit.id),
        "unload": lambda unit: (
            -unit.cargo.unload_order,
            -unit.volume_mm3,
            unit.id,
        ),
    }
    if strategy == "ai":
        # The AI chooses SKU precedence; local geometry still chooses coordinates.
        ai_ranks = {
            cargo_id: index
            for index, cargo_id in enumerate(ai_order or [])
            if isinstance(cargo_id, str)
        }
        key_map["ai"] = lambda unit: (
            ai_ranks.get(unit.cargo.id, len(ai_ranks)),
            -unit.volume_mm3,
            unit.id,
        )
    key = key_map[strategy]
    return sorted(required, key=key) + sorted(optional, key=key)


def _pack_units(
    request: PackRequest,
    units: list[StackUnit],
    pack_algo,
    order: str,
) -> list[PackedStack]:
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    bin_length = request.container.inner_length_mm - 2 * c + gap
    bin_width = request.container.inner_width_mm - 2 * c + gap
    packing_bin = pack_algo(bin_length, bin_width, rot=False)
    packed: list[PackedStack] = []
    ai_order = (request.ai_layout_hint or {}).get("sku_order")
    for unit in _ordered_units(units, order, ai_order if isinstance(ai_order, list) else None):
        swapped_orientation = PackedStack(
            unit=unit,
            x_mm=0,
            y_mm=0,
            rotated=True,
        ).orientation
        packing_bin.rot = (
            not isinstance(unit, CompositeUnit)
            and swapped_orientation in unit.cargo.allowed_orientations
        )
        rect = packing_bin.add_rect(
            unit.length_mm + gap,
            unit.width_mm + gap,
            rid=unit.id,
        )
        if rect is None:
            continue
        rotated = (
            unit.length_mm != unit.width_mm
            and int(rect.width) == unit.width_mm + gap
            and int(rect.height) == unit.length_mm + gap
        )
        packed.append(
            PackedStack(
                unit=unit,
                x_mm=c + int(rect.x),
                y_mm=c + int(rect.y),
                rotated=rotated,
            )
        )
    packed.sort(key=lambda stack: stack.unit.id)
    return packed


def _candidate_score(
    request: PackRequest,
    stacks: list[PackedStack],
) -> tuple[float, ...]:
    placements = _expand_stacks(request, stacks, "high_fill")
    return _layout_quality_score(request, placements, "high_fill")


def _bounded_pallet_layout_candidate(
    request: PackRequest,
    baseline: list[PackedStack],
    target_counts: Counter[str],
    profile: Literal["high_fill", "stable", "easy"],
) -> list[PackedStack] | None:
    """Search nearby pallet counts for a dense candidate without a large floor hole.

    A small optional-cargo exchange can turn a free-form MaxRects result into a
    continuous floor layout. The search is intentionally bounded and every
    candidate still goes through the normal validator.
    """
    if not baseline or not 4 <= len(request.cargo_items) <= 5:
        return None
    baseline_quality = _layout_quality(request, _expand_stacks(request, baseline, profile))
    if baseline_quality.floor_largest_transverse_gap_mm <= 400:
        return None

    item_by_id = {item.id: item for item in request.cargo_items}
    cargo_ids = list(item_by_id)
    if set(target_counts) != set(cargo_ids):
        target_counts = Counter({cargo_id: target_counts[cargo_id] for cargo_id in cargo_ids})
    target_pieces = sum(target_counts.values())
    if target_pieces <= 0:
        return None

    # Explore one-piece exchanges first, then their nearby combinations. This
    # keeps the bounded search useful without reopening the full subset search.
    variants: list[dict[str, int]] = [dict(target_counts)]
    seen = {tuple(variants[0].get(cargo_id, 0) for cargo_id in cargo_ids)}
    frontier = variants[:]
    for _ in range(3):
        next_frontier: list[dict[str, int]] = []
        for current in frontier:
            for source_id in cargo_ids:
                source_item = item_by_id[source_id]
                minimum = source_item.quantity if source_item.must_load else 0
                if current.get(source_id, 0) <= minimum:
                    continue
                for destination_id in cargo_ids:
                    if destination_id == source_id:
                        continue
                    if current.get(destination_id, 0) >= item_by_id[destination_id].quantity:
                        continue
                    variant = dict(current)
                    variant[source_id] -= 1
                    variant[destination_id] = variant.get(destination_id, 0) + 1
                    key = tuple(variant.get(cargo_id, 0) for cargo_id in cargo_ids)
                    if key in seen:
                        continue
                    seen.add(key)
                    variants.append(variant)
                    next_frontier.append(variant)
                    if len(variants) >= FLOOR_COMBINATION_LIMIT:
                        break
                if len(variants) >= FLOOR_COMBINATION_LIMIT:
                    break
            if len(variants) >= FLOOR_COMBINATION_LIMIT:
                break
        frontier = next_frontier
        if not frontier or len(variants) >= FLOOR_COMBINATION_LIMIT:
            break

    candidates: list[tuple[tuple[float, ...], list[PackedStack]]] = []
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - request.container.clearance_mm
    for counts in variants:
        candidate_items = [
            item.model_copy(update={"quantity": counts[item.id]})
            for item in request.cargo_items
            if counts[item.id] > 0
        ]
        candidate_request = request.model_copy(update={"cargo_items": candidate_items})
        units = _build_stack_units(candidate_request)
        merged = _merge_pallet_cartons(candidate_request, units)
        for pack_algo in PACK_ALGOS:
            for order in ("volume", "footprint", "weight", "lightweight", "sku", "ai"):
                stacks = _pack_units(candidate_request, merged, pack_algo, order)
                placements = _expand_stacks(candidate_request, stacks, profile)
                if len(placements) != target_pieces:
                    continue
                if max(
                    (placement.x_mm + placement.length_mm for placement in placements if placement.z_mm == 0),
                    default=0,
                ) > door_limit:
                    continue
                if not validate_solution(
                    request.container,
                    request.cargo_items,
                    placements,
                    item_gap_mm=request.item_gap_mm,
                ).valid:
                    continue
                quality = _layout_quality(request, placements)
                if quality.floor_largest_transverse_gap_mm > 400:
                    continue
                layout_score = _layout_quality_score(request, placements, profile)
                if profile == "high_fill":
                    # Equal-piece high-fill candidates trade a negligible
                    # volume delta for a genuinely continuous floor first.
                    score = (
                        float(len(placements)),
                        -float(quality.floor_largest_transverse_gap_mm),
                        -float(quality.floor_internal_gap_mm),
                        -float(quality.floor_bbox_void_pct),
                        layout_score[1],
                    ) + layout_score[2:]
                else:
                    score = layout_score
                candidates.append((
                    score
                    + (
                        -float(quality.floor_largest_transverse_gap_mm),
                        -float(quality.floor_bbox_void_pct),
                        -float(len(placements)),
                    ),
                    stacks,
                ))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _required_satisfied(request: PackRequest, stacks: list[PackedStack]) -> bool:
    """True if every must_load cargo is placed at its full quantity.

    Counts by expanded cargo piece (CompositeUnit pallet + on_top stacks
    counted separately) instead of by stack id, so must_load cartons merged
    onto pallet tops are not misjudged as missing.
    """
    loaded: Counter[str] = Counter()
    for stack in stacks:
        unit = stack.unit
        if isinstance(unit, CompositeUnit):
            loaded[unit.pallet.cargo.id] += unit.pallet.count
            for on_top, _, _ in unit.on_top:
                loaded[on_top.cargo.id] += on_top.count
        else:
            loaded[unit.cargo.id] += unit.count
    return all(
        loaded[item.id] >= item.quantity
        for item in request.cargo_items
        if item.must_load
    )


def _high_fill_candidate(request: PackRequest, units: list[StackUnit]) -> list[PackedStack]:
    candidates: list[list[PackedStack]] = []
    for payload_strategy in ("volume", "footprint", "pieces", "lightweight"):
        pool = _select_payload_units(request, units, payload_strategy)
        merged = _merge_pallet_cartons(request, pool)
        for algo in PACK_ALGOS:
            for order in ("volume", "footprint", "weight", "lightweight"):
                packed = _pack_units(request, merged, algo, order)
                if _required_satisfied(request, packed):
                    candidates.append(packed)
    # 分层铺满优先：多策略载重池尝试分层布局，取评分最高者
    layer_candidates: list[list[PackedStack]] = []
    for strategy in ("volume", "footprint", "pieces", "lightweight"):
        pool = _select_payload_units(request, units, strategy)
        merged = _merge_pallet_cartons(request, pool)
        mixed = _layer_layout(request, merged, band_grid=False)
        if mixed is not None and _required_satisfied(request, mixed):
            layer_candidates.append(mixed)
    all_candidates = candidates + layer_candidates
    if not all_candidates:
        missing_required = [unit for unit in units if unit.required]
        if missing_required:
            cargo_names = "、".join(
                sorted({unit.cargo.sku for unit in missing_required})
            )
            raise PackingFailure(
                "MUST_LOAD_UNSATISFIED",
                f"必装货物 {cargo_names} 无法全部放入当前柜型",
                hint="请减少其他货物数量或更换更大柜型，确保必装货物能全部装入",
            )
        raise PackingFailure(
            "LAYOUT_NOT_FEASIBLE",
            "当前柜型未找到物理安全的可选货物布局",
            hint="系统已尽力搜索安全方案，请减少货物数量或更换更大柜型",
        )
    best_score = max(
        _candidate_score(request, candidate)
        for candidate in all_candidates
    )
    best_candidates = [
        candidate
        for candidate in all_candidates
        if _candidate_score(request, candidate) == best_score
    ]
    return min(best_candidates, key=lambda candidate: _stack_imbalance(request, candidate))


def _repack_same_units(
    request: PackRequest,
    selected: list[StackUnit],
    profile: Literal["stable", "easy"],
) -> list[PackedStack]:
    candidates = [
        _pack_units(request, selected, algo, order)
        for algo in PACK_ALGOS
        for order in (("weight", "footprint", "volume") if profile == "stable" else ("sku", "footprint", "volume"))
    ]
    complete = [candidate for candidate in candidates if len(candidate) == len(selected)]
    if not complete:
        return []
    if profile == "stable":
        centered = [_center_stacks(request, candidate) for candidate in complete]
        return min(centered, key=lambda candidate: _stack_imbalance(request, candidate))
    return min(complete, key=_cargo_transitions)


def _stack_imbalance(request: PackRequest, stacks: list[PackedStack]) -> float:
    total_weight = sum(stack.unit.total_weight_g for stack in stacks)
    if not total_weight:
        return 0
    cg_x = sum((stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g for stack in stacks) / total_weight
    cg_y = sum((stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g for stack in stacks) / total_weight
    return max(
        abs(cg_x - request.container.inner_length_mm / 2) / (request.container.inner_length_mm / 2),
        abs(cg_y - request.container.inner_width_mm / 2) / (request.container.inner_width_mm / 2),
    )


def _cargo_transitions(stacks: list[PackedStack]) -> int:
    ordered = sorted(stacks, key=lambda stack: (stack.x_mm, stack.y_mm, stack.unit.cargo.id))
    return sum(
        first.unit.cargo.id != second.unit.cargo.id
        for first, second in zip(ordered, ordered[1:])
    )


def _center_stacks(request: PackRequest, stacks: list[PackedStack]) -> list[PackedStack]:
    if not stacks:
        return stacks
    c = request.container.clearance_mm
    total_weight = sum(stack.unit.total_weight_g for stack in stacks)
    cg_x = sum(
        (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
        for stack in stacks
    ) / total_weight
    cg_y = sum(
        (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
        for stack in stacks
    ) / total_weight
    target_x = request.container.inner_length_mm / 2
    target_y = request.container.inner_width_mm / 2
    min_x = min(stack.x_mm for stack in stacks)
    min_y = min(stack.y_mm for stack in stacks)
    max_x = max(stack.x_mm + stack.length_mm for stack in stacks)
    max_y = max(stack.y_mm + stack.width_mm for stack in stacks)
    dx = round(target_x - cg_x)
    dy = round(target_y - cg_y)
    dx = max(c - min_x, min(dx, request.container.inner_length_mm - c - max_x))
    dy = max(c - min_y, min(dy, request.container.inner_width_mm - c - max_y))
    return [replace(stack, x_mm=stack.x_mm + dx, y_mm=stack.y_mm + dy) for stack in stacks]


def _compact_floor_candidate(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: str,
) -> list[PackedStack] | None:
    """Pack bottom stacks leftward while preserving their rows and stack heights."""
    # Validator collision checks are pairwise; keep large orders on their existing
    # linear candidate path instead of turning a cosmetic refinement into a timeout.
    if len(stacks) > 160 or sum(stack.unit.count for stack in stacks) > 160:
        return None
    floor = sorted(
        (stack for stack in stacks if stack.z_mm == 0),
        key=lambda stack: (stack.y_mm, stack.x_mm, stack.unit.id),
    )
    if len(floor) < 2:
        return None
    gap = request.item_gap_mm
    clearance = request.container.clearance_mm
    compacted: dict[str, PackedStack] = {}
    for stack in floor:
        x_mm = clearance
        for previous in compacted.values():
            if _rects_overlap_y(previous, stack.y_mm, stack.width_mm):
                x_mm = max(x_mm, previous.x_mm + previous.length_mm + gap)
        compacted[stack.unit.id] = replace(stack, x_mm=x_mm)
    candidate = [
        compacted.get(stack.unit.id, stack)
        for stack in stacks
    ]
    if all(
        candidate_stack.x_mm == original_stack.x_mm
        and candidate_stack.y_mm == original_stack.y_mm
        for candidate_stack, original_stack in zip(candidate, stacks)
    ):
        return None
    placements = _expand_stacks(request, candidate, profile)
    if not validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=request.item_gap_mm,
    ).valid:
        return None
    return candidate


def _prefer_compact_candidate(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: str,
) -> list[PackedStack]:
    if not request.ai_layout_hint:
        return stacks
    compact = _compact_floor_candidate(request, stacks, profile)
    if compact is None:
        return stacks
    current_score = _layout_quality_score(
        request,
        _expand_stacks(request, stacks, profile),
        profile,
    )
    compact_score = _layout_quality_score(
        request,
        _expand_stacks(request, compact, profile),
        profile,
    )
    current_quality = _layout_quality(
        request,
        _expand_stacks(request, stacks, profile),
    )
    compact_quality = _layout_quality(
        request,
        _expand_stacks(request, compact, profile),
    )
    if current_quality.upper_count and (
        compact_quality.upper_components > current_quality.upper_components
        or compact_quality.upper_isolated_count > current_quality.upper_isolated_count
        or compact_quality.upper_center_deviation_mm > current_quality.upper_center_deviation_mm
    ):
        # Do not trade a readable, supported upper core for cosmetic floor compaction.
        return stacks
    return compact if compact_score >= current_score else stacks


def _preserves_upper_quality(
    request: PackRequest,
    current: list[PackedStack],
    candidate: list[PackedStack],
) -> bool:
    """Reject a compact/easy candidate that breaks an existing upper core."""
    current_quality = _layout_quality(
        request,
        _expand_stacks(request, current, "easy"),
    )
    candidate_quality = _layout_quality(
        request,
        _expand_stacks(request, candidate, "easy"),
    )
    if not current_quality.upper_count:
        return True
    return (
        candidate_quality.upper_components <= current_quality.upper_components
        and candidate_quality.upper_isolated_count <= current_quality.upper_isolated_count
        and candidate_quality.upper_center_deviation_mm <= current_quality.upper_center_deviation_mm
    )


def _swap_balance(
    request: PackRequest,
    stacks: list[PackedStack],
    max_passes: int = 4,
) -> list[PackedStack]:
    """Swap stacks with identical footprints to lower length-axis imbalance first."""
    if len(stacks) < 2:
        return stacks
    target_x = request.container.inner_length_mm / 2
    target_y = request.container.inner_width_mm / 2
    current = list(stacks)
    # 存在先卸后装（unload_order）约束时禁止跨 SKU 交换，保护分区；
    # 无约束时允许同 footprint 跨 SKU 交换（重块/轻块互换居中配平）。
    has_unload_order = any(item.unload_order for item in request.cargo_items)

    def deviations(candidate: list[PackedStack]) -> tuple[float, float]:
        total_weight = sum(stack.unit.total_weight_g for stack in candidate) or 1
        cg_x = sum(
            (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
            for stack in candidate
        ) / total_weight
        cg_y = sum(
            (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
            for stack in candidate
        ) / total_weight
        return abs(cg_x - target_x) / (target_x or 1), abs(cg_y - target_y) / (target_y or 1)

    for _ in range(max_passes):
        current_x, current_y = deviations(current)
        best_swap: tuple[int, int] | None = None
        best_dev: tuple[float, float] | None = None
        groups: dict[tuple[int, int, str], list[int]] = defaultdict(list)
        for index, stack in enumerate(current):
            # 有先卸后装约束时同 footprint 且同 SKU 才可交换（保护分区）；
            # 无约束时允许同 footprint 跨 SKU 交换（配平）。
            group_key = (
                stack.length_mm,
                stack.width_mm,
                stack.unit.cargo.id if has_unload_order else "",
            )
            groups[group_key].append(index)
        total_weight = sum(stack.unit.total_weight_g for stack in current) or 1
        cg_x = sum(
            (stack.x_mm + stack.length_mm / 2) * stack.unit.total_weight_g
            for stack in current
        ) / total_weight
        cg_y = sum(
            (stack.y_mm + stack.width_mm / 2) * stack.unit.total_weight_g
            for stack in current
        ) / total_weight
        for indexes in groups.values():
            for first in range(len(indexes)):
                for second in range(first + 1, len(indexes)):
                    i, j = indexes[first], indexes[second]
                    a, b = current[i], current[j]
                    weight_a = a.unit.total_weight_g
                    weight_b = b.unit.total_weight_g
                    center_ax = a.x_mm + a.length_mm / 2
                    center_bx = b.x_mm + b.length_mm / 2
                    center_ay = a.y_mm + a.width_mm / 2
                    center_by = b.y_mm + b.width_mm / 2
                    next_cg_x = cg_x + (weight_a - weight_b) * (center_bx - center_ax) / total_weight
                    next_cg_y = cg_y + (weight_a - weight_b) * (center_by - center_ay) / total_weight
                    next_dev = (
                        abs(next_cg_x - target_x) / (target_x or 1),
                        abs(next_cg_y - target_y) / (target_y or 1),
                    )
                    if best_dev is None or next_dev < best_dev:
                        best_dev = next_dev
                        best_swap = (i, j)
        if best_swap is None or best_dev >= (current_x, current_y):
            break
        i, j = best_swap
        original_i = current[i]
        current[i] = replace(original_i, x_mm=current[j].x_mm, y_mm=current[j].y_mm)
        current[j] = replace(current[j], x_mm=original_i.x_mm, y_mm=original_i.y_mm)
    return current


def _refine_grid_length_balance(
    stacks: list[PackedStack],
    target_x: float,
    max_passes: int = 4,
) -> list[PackedStack]:
    """Swap stacks inside the same grid column to center the length-axis CG."""
    current = list(stacks)
    total_weight = sum(stack.unit.total_weight_g for stack in current)
    if not total_weight:
        return current

    def moment(stack: PackedStack) -> float:
        return stack.unit.total_weight_g * (stack.x_mm + stack.length_mm / 2 - target_x)

    for _ in range(max_passes):
        residual = sum(moment(stack) for stack in current)
        if abs(residual) < 1:
            break
        columns: dict[int, list[int]] = defaultdict(list)
        for index, stack in enumerate(current):
            columns[stack.y_mm].append(index)
        best_swap: tuple[int, int] | None = None
        best_gain = 0.0
        for indexes in columns.values():
            for first in range(len(indexes)):
                for second in range(first + 1, len(indexes)):
                    i, j = indexes[first], indexes[second]
                    weight_i = current[i].unit.total_weight_g
                    weight_j = current[j].unit.total_weight_g
                    center_i = current[i].x_mm + current[i].length_mm / 2
                    center_j = current[j].x_mm + current[j].length_mm / 2
                    next_residual = residual + (weight_i - weight_j) * (center_j - center_i)
                    gain = abs(residual) - abs(next_residual)
                    if gain > best_gain:
                        best_gain = gain
                        best_swap = (i, j)
        if best_swap is None or best_gain <= 0:
            break
        i, j = best_swap
        original_i = current[i]
        current[i] = replace(original_i, x_mm=current[j].x_mm)
        current[j] = replace(current[j], x_mm=original_i.x_mm)
    return current


def _stable_balance_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """混合尺寸整托的配平布局：按 SKU 分块，重 SKU 块居中、轻 SKU 块两端
    （按 SKU 总重量从中心向外排），块内网格排布。用于"更稳妥"方案，
    使方案与"装得多"（混合铺满）布局明显不同。"""
    if not units or any(unit.count != 1 or unit.cargo.kind != "pallet" for unit in units):
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    by_sku: dict[str, list[StackUnit]] = {}
    for unit in units:
        by_sku.setdefault(unit.cargo.id, []).append(unit)
    sku_order = sorted(
        by_sku,
        key=lambda sid: (-sum(u.total_weight_g for u in by_sku[sid]), sid),
    )
    blocks: list[tuple[str, list[StackUnit], int, int]] = []  # (sku, group, cols, block_len)
    for sid in sku_order:
        group = by_sku[sid]
        cols = max(1, usable_width // group[0].width_mm)
        rows = (len(group) + cols - 1) // cols
        block_len = rows * group[0].length_mm + max(0, rows - 1) * gap
        blocks.append((sid, group, cols, block_len))
    total_len = sum(b[3] for b in blocks)
    if total_len > usable_length:
        return None
    # 槽位分配：最重块放中间槽，其余左右交替向外
    n = len(blocks)
    slot_order = sorted(
        range(n), key=lambda i: (abs(i - (n - 1) / 2), i)
    )
    block_x: dict[int, int] = {}
    shift = (usable_length - total_len) // 2
    cursor = shift
    for slot in range(n):
        block_x[slot] = cursor
        cursor += blocks[slot][3] + gap
    placed: list[PackedStack] = []
    for slot in range(n):
        sid, group, cols, _ = blocks[slot]
        x0 = block_x[slot]
        rows = (len(group) + cols - 1) // cols
        row_order = sorted(range(rows), key=lambda r: (abs(r - (rows - 1) / 2), r))
        # 行内按重量降序 + 奇偶反向（y 向配平）
        row_units: dict[int, list[StackUnit]] = {}
        idx = 0
        for r in row_order:
            row_units[r] = group[idx:idx + cols]
            idx += cols
        for r in range(rows):
            units_in_row = sorted(row_units[r], key=lambda u: -u.total_weight_g)
            if r % 2 == 1:
                units_in_row.reverse()
            y_cursor = 0
            for unit in units_in_row:
                placed.append(
                    PackedStack(unit=unit, x_mm=c + x0, y_mm=c + y_cursor, step=1)
                )
                y_cursor += unit.width_mm + gap
            x0 += group[0].length_mm + gap
    return placed


def _pallet_grid_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Balance single-layer pallets in a weight-aware grid, one step per row."""
    if not units or any(unit.count != 1 or unit.cargo.kind != "pallet" for unit in units):
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c

    options: list[set[tuple[int, int]]] = []
    for unit in units:
        candidates = {(unit.length_mm, unit.width_mm)}
        swapped_orientation = SWAP_ORIENTATIONS.get(unit.orientation)
        if swapped_orientation in unit.cargo.allowed_orientations:
            swapped = (unit.width_mm, unit.length_mm)
            if swapped[1] <= door_usable_width:
                candidates.add(swapped)
        options.append(candidates)
    common_footprints = set.intersection(*options)
    if not common_footprints:
        return None

    best: tuple[tuple[int, int, int], tuple[int, int]] | None = None
    for footprint in sorted(common_footprints):
        along_length, across_width = footprint
        if across_width + gap > usable_width:
            continue
        columns = usable_width // (across_width + gap)
        if columns < 1:
            continue
        rows = (len(units) + columns - 1) // columns
        total_length = rows * along_length + (rows - 1) * gap
        if total_length > usable_length:
            continue
        score = (columns, -total_length, along_length)
        if best is None or score > best[0]:
            best = (score, footprint)
    if best is None:
        return None

    along_length, across_width = best[1]
    columns = usable_width // (across_width + gap)
    rows = (len(units) + columns - 1) // columns
    oriented = []
    for unit in units:
        rotated = (unit.width_mm, unit.length_mm) == (along_length, across_width)
        oriented.append((unit, rotated))
    oriented.sort(key=lambda item: (-item[0].total_weight_g, item[0].id))

    row_order = sorted(
        range(rows),
        key=lambda row: (abs(row - (rows - 1) / 2), row),
    )
    column_weights = [0] * columns
    assignments: list[tuple[int, int, int]] = []
    index = 0
    for row in row_order:
        for _ in range(columns):
            if index >= len(oriented):
                break
            column = min(
                range(columns),
                key=lambda col: (column_weights[col], col),
            )
            column_weights[column] += oriented[index][0].total_weight_g
            assignments.append((row, column, index))
            index += 1

    placed: list[PackedStack] = []
    for row, column, unit_index in assignments:
        unit, rotated = oriented[unit_index]
        placed.append(
            PackedStack(
                unit=unit,
                x_mm=c + row * (along_length + gap),
                y_mm=c + column * (across_width + gap),
                rotated=rotated,
            )
        )
    placed = _refine_grid_length_balance(placed, request.container.inner_length_mm / 2)
    return [
        replace(
            stack,
            step=round((stack.x_mm - c) / (along_length + gap)) + 1,
        )
        for stack in placed
    ]


def _rectangle_components(
    rectangles: list[tuple[int, int, int, int]],
    gap: int = 0,
) -> int:
    """Return connected components for (x, y, length, width) rectangles."""
    if not rectangles:
        return 0

    parent = list(range(len(rectangles)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, (x, y, length, width) in enumerate(rectangles):
        x_end = x + length
        y_end = y + width
        for right in range(left):
            other_x, other_y, other_length, other_width = rectangles[right]
            if (
                min(x_end, other_x + other_length) - max(x, other_x) >= -gap
                and min(y_end, other_y + other_width) - max(y, other_y) >= -gap
            ):
                union(left, right)

    return len({find(index) for index in range(len(rectangles))})


@dataclass(frozen=True)
class LayoutQuality:
    floor_count: int
    upper_count: int
    floor_x_components: int
    floor_coverage_pct: float
    floor_internal_gap_mm: int
    floor_largest_gap_mm: int
    floor_bbox_void_pct: float
    floor_largest_transverse_gap_mm: int
    upper_components: int
    upper_center_mm: float
    upper_center_deviation_mm: float
    upper_isolated_count: int
    sku_transitions: int


def _interval_components(
    intervals: list[tuple[int, int]],
    tolerance: int,
) -> int:
    if not intervals:
        return 0
    components = 0
    current_end: int | None = None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end + tolerance:
            components += 1
            current_end = end
        else:
            current_end = max(current_end, end)
    return components


def _interval_union_length(
    intervals: list[tuple[int, int]],
    tolerance: int,
) -> int:
    if not intervals:
        return 0
    total = 0
    current_start: int | None = None
    current_end: int | None = None
    for start, end in sorted(intervals):
        if current_start is None:
            current_start, current_end = start, end
            continue
        if start <= current_end + tolerance:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    return total + (current_end - current_start)


def _floor_largest_transverse_gap_mm(
    request: PackRequest,
    floor: list[Placement],
) -> int:
    """Measure the largest uncovered Y span through any X slice of the floor."""
    if not floor:
        return 0
    tolerance = request.item_gap_mm + 1
    x_edges = sorted({
        edge
        for item in floor
        for edge in (item.x_mm, item.x_mm + item.length_mm)
    })
    largest = 0
    for start, end in zip(x_edges, x_edges[1:]):
        if end <= start:
            continue
        intervals = sorted(
            (
                item.y_mm,
                item.y_mm + item.width_mm,
            )
            for item in floor
            if item.x_mm <= start and item.x_mm + item.length_mm >= end
        )
        previous_end: int | None = None
        for interval_start, interval_end in intervals:
            if interval_end <= interval_start:
                continue
            if previous_end is not None and interval_start > previous_end + tolerance:
                largest = max(largest, interval_start - previous_end)
            previous_end = max(previous_end or interval_end, interval_end)
    return largest


def _layout_quality(
    request: PackRequest,
    placements: list[Placement],
) -> LayoutQuality:
    clearance = request.container.clearance_mm
    tolerance = request.item_gap_mm + 1
    floor = [
        placement
        for placement in placements
        if placement.z_mm == clearance
    ]
    upper = [
        placement
        for placement in placements
        if placement.z_mm > clearance
    ]
    floor_intervals = [
        (placement.x_mm, placement.x_mm + placement.length_mm)
        for placement in floor
    ]
    floor_components = _interval_components(floor_intervals, tolerance)
    if floor_intervals:
        floor_start = min(start for start, _ in floor_intervals)
        floor_end = max(end for _, end in floor_intervals)
        floor_span = floor_end - floor_start
        floor_coverage_pct = round(
            _interval_union_length(floor_intervals, tolerance)
            / floor_span
            * 100,
            2,
        ) if floor_span else 100.0
    else:
        floor_coverage_pct = 0.0

    sorted_floor_intervals = sorted(floor_intervals)
    floor_internal_gap_mm = 0
    floor_largest_gap_mm = 0
    previous_end: int | None = None
    for start, end in sorted_floor_intervals:
        if previous_end is not None and start > previous_end:
            gap = start - previous_end
            floor_internal_gap_mm += gap
            floor_largest_gap_mm = max(floor_largest_gap_mm, gap)
        previous_end = max(previous_end or end, end)
    if floor:
        floor_min_x = min(placement.x_mm for placement in floor)
        floor_max_x = max(placement.x_mm + placement.length_mm for placement in floor)
        floor_min_y = min(placement.y_mm for placement in floor)
        floor_max_y = max(placement.y_mm + placement.width_mm for placement in floor)
        bbox_area = (floor_max_x - floor_min_x) * (floor_max_y - floor_min_y)
        occupied_area = sum(placement.length_mm * placement.width_mm for placement in floor)
        floor_bbox_void_pct = round(
            max(0.0, 1.0 - occupied_area / bbox_area) * 100,
            2,
        ) if bbox_area else 0.0
    else:
        floor_bbox_void_pct = 0.0
    floor_largest_transverse_gap_mm = _floor_largest_transverse_gap_mm(
        request,
        floor,
    )

    upper_rectangles = [
        (
            placement.x_mm,
            placement.y_mm,
            placement.length_mm,
            placement.width_mm,
        )
        for placement in upper
    ]
    if len(upper_rectangles) > 300:
        # The large-order path approximates connectivity on the length axis.
        upper_components = _interval_components(
            [
                (x, x + length)
                for x, _, length, _ in upper_rectangles
            ],
            tolerance,
        )
    else:
        upper_components = _rectangle_components(upper_rectangles, tolerance)
    if floor:
        floor_center = (
            min(placement.x_mm for placement in floor)
            + max(placement.x_mm + placement.length_mm for placement in floor)
        ) / 2
    else:
        floor_center = request.container.inner_length_mm / 2
    if upper:
        upper_center = sum(
            placement.x_mm + placement.length_mm / 2
            for placement in upper
        ) / len(upper)
        upper_center_deviation = abs(upper_center - floor_center)
    else:
        upper_center = request.container.inner_length_mm / 2
        upper_center_deviation = 0.0

    if len(upper_rectangles) > 300:
        # Large orders use an X-axis approximation to keep diagnostics linearithmic.
        # Exact pairwise component checks are retained for normal-sized orders.
        isolated_count = 0 if upper_components <= 1 else upper_components
    else:
        isolated_count = 0
        for index, rectangle in enumerate(upper_rectangles):
            has_neighbor = any(
                index != other_index
                and _rectangle_components(
                    [rectangle, other_rectangle],
                    tolerance,
                ) == 1
                for other_index, other_rectangle in enumerate(upper_rectangles)
            )
            if not has_neighbor:
                isolated_count += 1

    ordered = sorted(
        placements,
        key=lambda placement: (
            placement.z_mm > clearance,
            placement.x_mm,
            placement.y_mm,
            placement.z_mm,
        ),
    )
    sku_transitions = sum(
        first.cargo_id != second.cargo_id
        for first, second in zip(ordered, ordered[1:])
    )
    return LayoutQuality(
        floor_count=len(floor),
        upper_count=len(upper),
        floor_x_components=floor_components,
        floor_coverage_pct=floor_coverage_pct,
        floor_internal_gap_mm=floor_internal_gap_mm,
        floor_largest_gap_mm=floor_largest_gap_mm,
        floor_bbox_void_pct=floor_bbox_void_pct,
        floor_largest_transverse_gap_mm=floor_largest_transverse_gap_mm,
        upper_components=upper_components,
        upper_center_mm=upper_center,
        upper_center_deviation_mm=upper_center_deviation,
        upper_isolated_count=isolated_count,
        sku_transitions=sku_transitions,
    )


def _layout_quality_score(
    request: PackRequest,
    placements: list[Placement],
    profile: str,
) -> tuple[float, ...]:
    quality = _layout_quality(request, placements)
    floor_first = int(
        quality.upper_count == 0
        or quality.floor_count >= quality.upper_count
    )
    floor_continuous = int(
        quality.floor_count == 0
        or quality.floor_x_components == 1
    )
    upper_continuous = int(
        quality.upper_count == 0
        or quality.upper_components == 1
    )
    upper_compact = int(
        quality.upper_count == 0
        or quality.upper_isolated_count == 0
    )
    common = (
        floor_first,
        floor_continuous,
        quality.floor_coverage_pct,
        -quality.floor_largest_gap_mm,
        -quality.floor_internal_gap_mm,
        -quality.floor_largest_transverse_gap_mm,
        -quality.floor_bbox_void_pct,
        upper_continuous,
        upper_compact,
        -quality.upper_center_deviation_mm,
    )
    loaded = float(len(placements))
    ai_score = _ai_strategy_score(request, placements)
    if profile == "stable":
        metrics = _metrics(request, placements)
        return (loaded,) + common + (
            -metrics.weight_imbalance_pct,
            -quality.sku_transitions,
        ) + ai_score
    if profile == "easy":
        return (loaded,) + common + (
            -quality.sku_transitions,
        ) + ai_score
    return (loaded,) + (
        float(sum(
            item.length_mm * item.width_mm * item.height_mm
            for item in placements
        )),
    ) + common + ai_score


def _candidate_selection_score(
    request: PackRequest,
    placements: list[Placement],
    profile: str,
) -> tuple[float, ...]:
    score = _layout_quality_score(request, placements, profile)
    if len(request.cargo_items) < 6:
        return score
    quality = _layout_quality(request, placements)
    return (
        score[0],
        int(quality.upper_count == 0 or quality.upper_components == 1),
        int(quality.upper_count == 0 or quality.upper_isolated_count == 0),
        -float(quality.upper_components),
        -float(quality.upper_isolated_count),
        -quality.upper_center_deviation_mm,
    ) + score[1:]


def _ai_strategy_score(
    request: PackRequest,
    placements: list[Placement],
) -> tuple[float, ...]:
    """Score an AI hint only after physical and profile priorities are equal.

    The hint can prefer a cabinet-length SKU order and legal orientations, but
    it cannot make an unsafe candidate win because this tuple is appended after
    the deterministic layout quality terms.
    """
    hint = request.ai_layout_hint
    if not isinstance(hint, dict):
        return ()
    raw_order = hint.get("sku_order")
    raw_orientations = hint.get("orientations")
    hinted_order = [item_id for item_id in raw_order or [] if isinstance(item_id, str)]
    hinted_orientations = raw_orientations if isinstance(raw_orientations, dict) else {}
    if not hinted_order and not hinted_orientations:
        return ()

    floor = [
        placement
        for placement in placements
        if placement.z_mm == request.container.clearance_mm
    ]
    first_x: dict[str, int] = {}
    for placement in floor:
        first_x[placement.cargo_id] = min(
            first_x.get(placement.cargo_id, placement.x_mm),
            placement.x_mm,
        )
    present_order = [item_id for item_id in hinted_order if item_id in first_x]
    order_score = 0.0
    pair_count = 0
    for index, left_id in enumerate(present_order):
        for right_id in present_order[index + 1:]:
            pair_count += 1
            if first_x[left_id] <= first_x[right_id]:
                order_score += 1.0
    if pair_count:
        order_score /= pair_count

    orientation_matches = 0
    orientation_total = 0
    for placement in placements:
        expected = hinted_orientations.get(placement.cargo_id)
        if not isinstance(expected, str):
            continue
        orientation_total += 1
        if placement.rotation.value == expected:
            orientation_matches += 1
    orientation_score = (
        orientation_matches / orientation_total
        if orientation_total
        else 0.0
    )
    return order_score, orientation_score


def _upper_layout_diagnostics(
    request: PackRequest,
    placements: list[Placement],
) -> tuple[int, float, bool]:
    """Return upper-layer components, center position, and isolated status."""
    quality = _layout_quality(request, placements)
    if not quality.upper_count:
        return 0, 0.0, False
    return (
        quality.upper_components,
        quality.upper_center_mm,
        quality.upper_isolated_count > 0,
    )


def _upper_layout_quality_ok(
    request: PackRequest,
    placements: list[Placement],
) -> bool:
    """Require one upper component centered over the occupied floor region."""
    quality = _layout_quality(request, placements)
    if quality.upper_count == 0:
        return True
    return (
        quality.floor_count >= quality.upper_count
        and quality.floor_x_components <= 1
        and quality.upper_components == 1
        and quality.upper_isolated_count == 0
        and quality.upper_center_deviation_mm <= 1800
    )


def _same_sku_band_layout(
    request: PackRequest,
    by_cargo: dict[str, StackUnit],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
    strategy: Literal["fill", "stable", "easy", "strict"],
) -> list[PackedStack] | None:
    """Build a deterministic PB-PA-PB-PC style pallet layout.

    This narrow candidate handles three pure-pallet SKUs with two stackable
    products and one non-stackable product. It gives the common mixed-pallet
    case an explicit floor sequence that generic 2D packing cannot express.
    """
    if len(by_cargo) != 3:
        return None
    if any(
        unit.cargo.unload_order
        for unit in by_cargo.values()
    ):
        return None

    stackable_ids = [
        cargo_id
        for cargo_id, capacity in capacity_by_cargo.items()
        if capacity >= 2 and by_cargo[cargo_id].cargo.stackable
    ]
    non_stackable_ids = [
        cargo_id
        for cargo_id, capacity in capacity_by_cargo.items()
        if capacity == 1 or not by_cargo[cargo_id].cargo.stackable
    ]
    if len(stackable_ids) != 2 or len(non_stackable_ids) != 1:
        return None

    # The larger stackable footprint becomes the bridge SKU. For A/B/C this
    # selects B, leaving A as the middle band.
    bridge_id, middle_id = sorted(
        stackable_ids,
        key=lambda cargo_id: (
            -by_cargo[cargo_id].length_mm * by_cargo[cargo_id].width_mm,
            -by_cargo[cargo_id].cargo.weight_g,
            cargo_id,
        ),
    )
    door_id = non_stackable_ids[0]
    gap = request.item_gap_mm
    c = request.container.clearance_mm
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c
    usable_width = request.container.inner_width_mm - 2 * c

    def columns(unit: StackUnit) -> int:
        return max(1, (usable_width + gap) // (unit.width_mm + gap))

    def minimum_floor_count(cargo_id: str) -> int:
        quantity = quantity_by_cargo[cargo_id]
        capacity = capacity_by_cargo[cargo_id]
        return (quantity + capacity - 1) // capacity

    bridge_unit = by_cargo[bridge_id]
    middle_unit = by_cargo[middle_id]
    door_unit = by_cargo[door_id]
    swapped_door_orientation = SWAP_ORIENTATIONS.get(door_unit.orientation)
    if swapped_door_orientation in door_unit.cargo.allowed_orientations:
        current_length, _, _ = door_unit.cargo.dimensions_for(
            door_unit.orientation
        )
        swapped_length, swapped_width, swapped_height = door_unit.cargo.dimensions_for(
            swapped_door_orientation
        )
        if swapped_length > current_length:
            door_unit = replace(
                door_unit,
                orientation=swapped_door_orientation,
                length_mm=swapped_length,
                width_mm=swapped_width,
                item_height_mm=swapped_height,
                stack_height_mm=swapped_height,
            )
    bridge_columns = columns(bridge_unit)
    middle_columns = columns(middle_unit)
    door_columns = columns(door_unit)
    door_floor = quantity_by_cargo[door_id]

    def band_length(unit: StackUnit, count: int, across: int) -> int:
        rows = max(1, (count + across - 1) // across)
        return rows * unit.length_mm + max(0, rows - 1) * gap

    def bridge_split(count: int) -> tuple[int, int] | None:
        rows = max(1, (count + bridge_columns - 1) // bridge_columns)
        left_rows = max(
            1,
            (rows + 1) // 2 if strategy == "stable" else rows // 2,
        )
        left_count = min(count - 1, left_rows * bridge_columns)
        right_count = count - left_count
        if left_count <= 0 or right_count <= 0:
            return None
        return left_count, right_count

    floor_candidates: list[tuple[int, int, int, int, int]] = []
    for candidate_bridge_floor in range(
        minimum_floor_count(bridge_id),
        quantity_by_cargo[bridge_id] + 1,
    ):
        split = bridge_split(candidate_bridge_floor)
        if split is None:
            continue
        candidate_left_count, candidate_right_count = split
        bridge_left_length = band_length(
            bridge_unit,
            candidate_left_count,
            bridge_columns,
        )
        bridge_right_length = band_length(
            bridge_unit,
            candidate_right_count,
            bridge_columns,
        )
        for candidate_middle_floor in range(
            minimum_floor_count(middle_id),
            quantity_by_cargo[middle_id] + 1,
        ):
            candidate_total_length = (
                bridge_left_length
                + band_length(
                    middle_unit,
                    candidate_middle_floor,
                    middle_columns,
                )
                + bridge_right_length
                + band_length(door_unit, door_floor, door_columns)
            )
            if candidate_total_length > door_limit:
                continue
            floor_area = (
                candidate_bridge_floor
                * bridge_unit.length_mm
                * bridge_unit.width_mm
                + candidate_middle_floor
                * middle_unit.length_mm
                * middle_unit.width_mm
                + door_floor * door_unit.length_mm * door_unit.width_mm
            )
            floor_candidates.append(
                (
                    candidate_total_length,
                    floor_area,
                    candidate_bridge_floor + candidate_middle_floor + door_floor,
                    candidate_bridge_floor,
                    candidate_middle_floor,
                )
            )
    if not floor_candidates:
        return None
    _, _, _, bridge_floor, middle_floor = max(floor_candidates)
    bridge_split_result = bridge_split(bridge_floor)
    if bridge_split_result is None:
        return None
    left_bridge_count, right_bridge_count = bridge_split_result

    bridge_left_length = band_length(
        bridge_unit,
        left_bridge_count,
        bridge_columns,
    )
    middle_length = band_length(middle_unit, middle_floor, middle_columns)
    bridge_right_length = band_length(
        bridge_unit,
        right_bridge_count,
        bridge_columns,
    )
    door_length = band_length(door_unit, door_floor, door_columns)
    total_length = (
        bridge_left_length
        + middle_length
        + bridge_right_length
        + door_length
    )
    # Customer-style floor bands run from the container head toward the doors.
    # Keep the non-stackable band at the door end without leaving a head-side void.
    start_x = c
    if start_x + total_length > door_limit:
        return None

    floor_stacks: list[PackedStack] = []
    next_instance: Counter[str] = Counter()

    def y_start(unit: StackUnit, across: int) -> int:
        occupied_width = (
            across * unit.width_mm
            + max(0, across - 1) * gap
        )
        spare_width = max(0, usable_width - occupied_width)
        if strategy == "stable":
            return c + spare_width // 2
        if strategy == "easy":
            return c + spare_width // 4
        return c

    def add_band(
        unit: StackUnit,
        count: int,
        x_start: int,
    ) -> int:
        across = columns(unit)
        rows = max(1, (count + across - 1) // across)
        band_y_start = y_start(unit, across)
        for index in range(count):
            row, column = divmod(index, across)
            instance_index = next_instance[unit.cargo.id]
            one_piece = replace(
                unit,
                id=f"{unit.cargo.id}-band-floor-{instance_index}",
                count=1,
                stack_height_mm=unit.item_height_mm,
                total_weight_g=unit.cargo.weight_g,
                first_instance_index=instance_index,
            )
            next_instance[unit.cargo.id] += 1
            floor_stacks.append(
                PackedStack(
                    unit=one_piece,
                    x_mm=x_start + row * (unit.length_mm + gap),
                    y_mm=band_y_start + column * (unit.width_mm + gap),
                    step=1,
                )
            )
        return rows * unit.length_mm + max(0, rows - 1) * gap

    x_cursor = start_x
    add_band(bridge_unit, left_bridge_count, x_cursor)
    x_cursor += bridge_left_length
    add_band(middle_unit, middle_floor, x_cursor)
    x_cursor += middle_length
    add_band(bridge_unit, right_bridge_count, x_cursor)
    x_cursor += bridge_right_length
    add_band(door_unit, door_floor, x_cursor)

    final_stacks = list(floor_stacks)
    center_x = request.container.inner_length_mm / 2
    for cargo_id in (middle_id, bridge_id):
        remaining = quantity_by_cargo[cargo_id] - next_instance[cargo_id]
        if remaining <= 0:
            continue
        candidates = [
            stack
            for stack in floor_stacks
            if stack.unit.cargo.id == cargo_id
        ]
        if strategy in {"strict", "easy"}:
            by_x: dict[int, list[PackedStack]] = defaultdict(list)
            for stack in sorted(
                candidates,
                key=lambda item: (
                    item.x_mm,
                    item.y_mm,
                    item.unit.first_instance_index,
                ),
            ):
                by_x[stack.x_mm].append(stack)
            supports = [
                row[0]
                for _, row in sorted(by_x.items())
            ]
            selected_ids = {id(stack) for stack in supports}
            supports.extend(
                sorted(
                    (
                        stack
                        for stack in candidates
                        if id(stack) not in selected_ids
                    ),
                    key=lambda stack: (
                        abs(stack.x_mm + stack.length_mm / 2 - center_x),
                        abs(stack.y_mm - c),
                        stack.unit.first_instance_index,
                    ),
                )
            )
        else:
            supports = sorted(
                candidates,
                key=lambda stack: (
                    abs(stack.x_mm + stack.length_mm / 2 - center_x),
                    abs(stack.y_mm - c),
                    stack.unit.first_instance_index,
                ),
            )
        extra_capacity = capacity_by_cargo[cargo_id] - 1
        for index, support in enumerate(supports):
            if remaining <= 0:
                break
            extra = min(extra_capacity, remaining)
            upper = replace(
                support.unit,
                id=f"{cargo_id}-band-upper-{index}",
                count=extra,
                stack_height_mm=extra * support.unit.item_height_mm,
                total_weight_g=extra * support.unit.cargo.weight_g,
                first_instance_index=next_instance[cargo_id],
            )
            final_stacks.append(
                replace(
                    support,
                    unit=upper,
                    z_mm=support.unit.item_height_mm,
                    step=2,
                )
            )
            remaining -= extra
            next_instance[cargo_id] += extra
    if remaining:
            return None

    placements = _expand_stacks(request, final_stacks, "high_fill")
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=gap,
    )
    if not validation.valid:
        return None
    return final_stacks


def _floor_orientation_options(
    request: PackRequest,
    unit: StackUnit,
) -> list[tuple[Orientation, int, int, int]]:
    """Return legal floor orientations as (orientation, length, width, columns)."""
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_width = request.container.inner_width_mm - 2 * c
    door_width = request.container.door_width_mm - 2 * c
    available_height = request.container.inner_height_mm - 2 * c
    options: list[tuple[Orientation, int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    for orientation in unit.cargo.allowed_orientations:
        length, width, height = unit.cargo.dimensions_for(orientation)
        capacity = _stack_capacity(
            unit.cargo,
            orientation,
            available_height,
            gap,
        )
        if (
            capacity < 1
            or length > request.container.inner_length_mm - 2 * c
            or width > usable_width
            or width > door_width
            or height > request.container.door_height_mm - 2 * c
        ):
            continue
        columns = (usable_width + gap) // (width + gap)
        if columns < 1:
            continue
        key = (length, width, height)
        if key in seen:
            continue
        seen.add(key)
        options.append((orientation, length, width, columns))
    return sorted(
        options,
        key=lambda option: (
            -option[3],
            option[1],
            option[2],
            option[0].value,
        ),
    )


def _mixed_floor_band_layout(
    request: PackRequest,
    by_cargo: dict[str, StackUnit],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
    strategy: Literal["fill", "stable", "easy", "strict"],
) -> list[PackedStack] | None:
    """Generate customer-style floor bands for 4/5 pure-pallet SKU orders.

    The candidate keeps complete SKU bands for the main quantities and combines
    small remainder pallets into one floor row when their footprints fit across
    the container width. Upper pallets are added only above same-SKU floor slots.
    """
    if not 4 <= len(by_cargo) <= 6:
        return None
    if any(unit.cargo.unload_order for unit in by_cargo.values()):
        return None

    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_width = request.container.inner_width_mm - 2 * c
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c
    cargo_ids = list(by_cargo)
    options_by_cargo = {
        cargo_id: _floor_orientation_options(request, by_cargo[cargo_id])
        for cargo_id in cargo_ids
    }
    if any(not options for options in options_by_cargo.values()):
        return None

    floor_required = {
        cargo_id: (
            quantity_by_cargo[cargo_id] + capacity_by_cargo[cargo_id] - 1
        ) // capacity_by_cargo[cargo_id]
        for cargo_id in cargo_ids
    }
    if any(floor_required[cargo_id] < 1 for cargo_id in cargo_ids):
        return None

    non_stackable = [
        cargo_id
        for cargo_id in cargo_ids
        if capacity_by_cargo[cargo_id] == 1
        or not by_cargo[cargo_id].cargo.stackable
    ]
    stackable = [
        cargo_id
        for cargo_id in cargo_ids
        if cargo_id not in non_stackable
    ]
    pair_ids: list[tuple[str, str]] = []
    if (
        len(cargo_ids) == 5
        and len(non_stackable) == 2
        and len(stackable) == 3
    ):
        first_single, second_single = sorted(
            non_stackable,
            key=lambda cargo_id: (
                quantity_by_cargo[cargo_id],
                by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            ),
        )
        first_stackable = min(
            stackable,
            key=lambda cargo_id: (
                floor_required[cargo_id],
                by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            ),
        )
        remaining_stackable = sorted(
            (cargo_id for cargo_id in stackable if cargo_id != first_stackable),
            key=lambda cargo_id: (
                -floor_required[cargo_id],
                cargo_id,
            ),
        )
        pair_ids = [
            (first_stackable, first_single),
            (remaining_stackable[0], second_single),
            (remaining_stackable[1], second_single),
        ]

    customer_five_pair_orientations: dict[
        tuple[str, str],
        tuple[Orientation, Orientation],
    ] = {}
    customer_five_main_orientations: dict[str, Orientation] = {}
    customer_five_template = False
    if len(pair_ids) == 3:
        customer_five_pair_orientations = {
            pair_ids[0]: (Orientation.WLH, Orientation.WLH),
            pair_ids[1]: (Orientation.WLH, Orientation.WLH),
            pair_ids[2]: (Orientation.LWH, Orientation.LWH),
        }
        customer_five_main_orientations = {
            pair_ids[2][0]: Orientation.LWH,
            pair_ids[1][0]: Orientation.WLH,
            pair_ids[0][0]: Orientation.WLH,
        }
        customer_five_template = (
            quantity_by_cargo[pair_ids[2][0]] >= 16
            and quantity_by_cargo[pair_ids[1][0]] >= 13
            and quantity_by_cargo[pair_ids[0][0]] >= 3
        )

    pair_options: list[
        tuple[str, str, tuple[Orientation, int, int, int], tuple[Orientation, int, int, int]]
    ] = []
    for left_id, right_id in pair_ids:
        choices = [
            (left_option, right_option)
            for left_option in options_by_cargo[left_id]
            for right_option in options_by_cargo[right_id]
            if left_option[2] + gap + right_option[2] <= usable_width
        ]
        if not choices:
            return None
        preferred = customer_five_pair_orientations.get((left_id, right_id))
        if preferred:
            preferred_choices = [
                choice
                for choice in choices
                if choice[0][0] == preferred[0]
                and choice[1][0] == preferred[1]
            ]
            if preferred_choices:
                left_option, right_option = preferred_choices[0]
            else:
                left_option, right_option = min(
                    choices,
                    key=lambda choice: (
                        max(choice[0][1], choice[1][1]),
                        -(choice[0][2] + gap + choice[1][2]),
                        choice[0][0].value,
                        choice[1][0].value,
                    ),
                )
        else:
            left_option, right_option = min(
                choices,
                key=lambda choice: (
                    max(choice[0][1], choice[1][1]),
                    -(choice[0][2] + gap + choice[1][2]),
                    choice[0][0].value,
                    choice[1][0].value,
                ),
            )
        pair_options.append((left_id, right_id, left_option, right_option))

    pair_counts: Counter[str] = Counter()
    for left_id, right_id, _, _ in pair_options:
        pair_counts[left_id] += 1
        pair_counts[right_id] += 1
    if any(
        pair_counts[cargo_id] > floor_required[cargo_id]
        for cargo_id in cargo_ids
    ):
        return None

    main_counts = {
        cargo_id: floor_required[cargo_id] - pair_counts[cargo_id]
        for cargo_id in cargo_ids
    }

    def main_option(cargo_id: str) -> tuple[Orientation, int, int, int]:
        count = main_counts[cargo_id]
        preferred_orientation = customer_five_main_orientations.get(cargo_id)
        if preferred_orientation:
            preferred_options = [
                option
                for option in options_by_cargo[cargo_id]
                if option[0] == preferred_orientation
            ]
            if preferred_options:
                return preferred_options[0]
        return min(
            options_by_cargo[cargo_id],
            key=lambda option: (
                (
                    ((count + option[3] - 1) // option[3]) * option[1]
                    + max(0, (count + option[3] - 1) // option[3] - 1) * gap
                ),
                -option[3],
                option[1],
            option[2],
        ),
    )

    if customer_five_template:
        # Customer's proven five-SKU pattern:
        # Q1: five complete 3-across rows before its door-side remainder,
        # Q2: six complete 2-across rows before its door-side remainder,
        # Q3: one complete 2-across row before the Q3+Q4 remainder row.
        template_rows = {
            pair_ids[2][0]: 5,
            pair_ids[1][0]: 6,
            pair_ids[0][0]: 1,
        }
        for cargo_id, row_count in template_rows.items():
            option = main_option(cargo_id)
            requested = row_count * option[3]
            available = quantity_by_cargo[cargo_id] - pair_counts[cargo_id]
            main_counts[cargo_id] = min(requested, available)

    if pair_options:
        door_pair_stackable = pair_options[-1][0]
        if door_pair_stackable in stackable:
            option = main_option(door_pair_stackable)
            current_count = main_counts[door_pair_stackable]
            current_rows = (
                current_count + option[3] - 1
            ) // option[3]
            next_rows = (
                current_count + 1 + option[3] - 1
            ) // option[3]
            if current_rows == next_rows:
                # Keep a spare support in the door-side band so the upper
                # layer does not end with an isolated pallet by the doors.
                main_counts[door_pair_stackable] += 1

    ordered_main_ids = [
        cargo_id for cargo_id in cargo_ids if main_counts[cargo_id] > 0
    ]
    if customer_five_template:
        ordered_main_ids = [
            pair_ids[2][0],
            pair_ids[1][0],
            pair_ids[0][0],
        ]
        ordered_main_ids = [
            cargo_id for cargo_id in ordered_main_ids if main_counts[cargo_id] > 0
        ]
    elif strategy in {"fill", "easy"}:
        ordered_main_ids.sort(
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            )
        )
    elif strategy == "stable":
        ordered_main_ids.sort(
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.weight_g,
                cargo_id,
            )
        )
    elif strategy == "strict":
        ordered_main_ids.sort(
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            )
        )

    def band_y_start(width: int) -> int:
        if strategy == "stable":
            spare_width = max(0, usable_width - width)
            return c + spare_width // 2
        return c

    floor_stacks: list[PackedStack] = []
    floor_by_cargo: dict[str, list[PackedStack]] = defaultdict(list)
    next_instance: Counter[str] = Counter()
    x_cursor = c
    step = 1

    def add_floor_unit(
        cargo_id: str,
        option: tuple[Orientation, int, int, int],
        x_mm: int,
        y_mm: int,
        step_number: int,
    ) -> PackedStack:
        orientation, length, width, _ = option
        _, _, height = by_cargo[cargo_id].cargo.dimensions_for(orientation)
        instance_index = next_instance[cargo_id]
        unit = replace(
            by_cargo[cargo_id],
            id=f"{cargo_id}-mixed-floor-{instance_index}",
            orientation=orientation,
            count=1,
            length_mm=length,
            width_mm=width,
            item_height_mm=height,
            stack_height_mm=height,
            total_weight_g=by_cargo[cargo_id].cargo.weight_g,
            first_instance_index=instance_index,
        )
        next_instance[cargo_id] += 1
        stack = PackedStack(
            unit=unit,
            x_mm=x_mm,
            y_mm=y_mm,
            step=step_number,
        )
        floor_stacks.append(stack)
        floor_by_cargo[cargo_id].append(stack)
        return stack

    for cargo_id in ordered_main_ids:
        option = main_option(cargo_id)
        _, length, width, columns = option
        count = main_counts[cargo_id]
        rows = (count + columns - 1) // columns
        occupied_width = columns * width + max(0, columns - 1) * gap
        y_start = band_y_start(occupied_width)
        for index in range(count):
            row, column = divmod(index, columns)
            y_mm = y_start + column * (width + gap)
            add_floor_unit(
                cargo_id,
                option,
                x_cursor + row * (length + gap),
                y_mm,
                step,
            )
        x_cursor += rows * length + max(0, rows - 1) * gap
        step += 1

    for left_id, right_id, left_option, right_option in pair_options:
        row_length = max(left_option[1], right_option[1])
        row_width = left_option[2] + gap + right_option[2]
        y_start = band_y_start(row_width)
        left = add_floor_unit(left_id, left_option, x_cursor, y_start, step)
        add_floor_unit(
            right_id,
            right_option,
            x_cursor,
            y_start + left.width_mm + gap,
            step,
        )
        x_cursor += row_length + gap
        step += 1

    door_tolerance = 10 if customer_five_template else 0
    if not floor_stacks or x_cursor - gap > door_limit + door_tolerance:
        return None

    final_stacks = list(floor_stacks)

    def choose_row_filled_supports(
        candidates: list[PackedStack],
        required_count: int,
    ) -> list[PackedStack] | None:
        """Choose same-SKU supports by complete X rows before spreading."""
        if required_count <= 0:
            return []
        rows: list[list[PackedStack]] = []
        by_x: dict[int, list[PackedStack]] = defaultdict(list)
        for stack in candidates:
            by_x[stack.x_mm].append(stack)
        for x_mm in sorted(by_x):
            rows.append(sorted(by_x[x_mm], key=lambda stack: stack.y_mm))

        # State: required count -> (single-row count, partial-row count,
        # used-row count, X center distance, Y center distance,
        # selected supports).
        states: dict[
            int,
            tuple[int, int, int, float, float, list[PackedStack]],
        ] = {
            0: (0, 0, 0, 0.0, 0.0, [])
        }
        target_x = request.container.inner_length_mm / 2
        target_y = request.container.inner_width_mm / 2
        for row in rows:
            next_states = dict(states)
            capacity = len(row)
            for current_count, current in states.items():
                max_take = min(capacity, required_count - current_count)
                for take in range(1, max_take + 1):
                    for start in range(capacity - take + 1):
                        selected = row[start:start + take]
                        row_center_x = (
                            row[0].x_mm + row[0].length_mm / 2
                        )
                        row_center = sum(
                            stack.y_mm + stack.unit.width_mm / 2
                            for stack in selected
                        ) / take
                        candidate = (
                            current[0] + int(take == 1),
                            current[1] + int(take < capacity),
                            current[2] + 1,
                            current[3] + abs(row_center_x - target_x),
                            current[4] + abs(row_center - target_y),
                            current[5] + selected,
                        )
                        total = current_count + take
                        previous = next_states.get(total)
                        if previous is None or candidate[:5] < previous[:5]:
                            next_states[total] = candidate
            states = next_states
        selected_state = states.get(required_count)
        return selected_state[5] if selected_state else None

    for cargo_id in cargo_ids:
        remaining = quantity_by_cargo[cargo_id] - next_instance[cargo_id]
        extra_capacity = capacity_by_cargo[cargo_id] - 1
        if remaining <= 0 or extra_capacity <= 0:
            continue
        supports = choose_row_filled_supports(
            floor_by_cargo[cargo_id],
            remaining,
        )
        if supports is None:
            return None
        for index, support in enumerate(supports):
            if remaining <= 0:
                break
            extra = min(extra_capacity, remaining)
            upper = replace(
                support.unit,
                id=f"{cargo_id}-mixed-upper-{index}",
                count=extra,
                stack_height_mm=extra * support.unit.item_height_mm,
                total_weight_g=extra * support.unit.cargo.weight_g,
                first_instance_index=next_instance[cargo_id],
            )
            final_stacks.append(
                replace(
                    support,
                    unit=upper,
                    z_mm=support.unit.item_height_mm,
                    step=support.step,
                )
            )
            remaining -= extra
            next_instance[cargo_id] += extra
        if remaining:
            return None

    if strategy in {"easy", "strict"}:
        max_y_end = max(
            stack.y_mm + stack.unit.width_mm
            for stack in final_stacks
        )
        spare_width = max(0, c + usable_width - max_y_end)
        shift = spare_width // (2 if strategy == "easy" else 1)
        if shift:
            final_stacks = [
                replace(stack, y_mm=stack.y_mm + shift)
                for stack in final_stacks
            ]

    placements = _expand_stacks(request, final_stacks, "high_fill")
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=gap,
    )
    if not validation.valid:
        return None
    return final_stacks


def _choose_generic_upper_supports(
    request: PackRequest,
    floor_stacks: list[PackedStack],
    required_count: int,
) -> list[PackedStack] | None:
    """Choose complete or centered X rows before selecting partial supports."""
    if required_count <= 0:
        return []
    rows_by_x: dict[int, list[PackedStack]] = defaultdict(list)
    for stack in floor_stacks:
        rows_by_x[stack.x_mm].append(stack)
    rows = [
        sorted(row, key=lambda stack: stack.y_mm)
        for _, row in sorted(rows_by_x.items())
    ]
    target_x = request.container.inner_length_mm / 2
    rows.sort(
        key=lambda row: abs(row[0].x_mm + row[0].unit.length_mm / 2 - target_x)
    )
    selected: list[PackedStack] = []
    remaining = required_count
    for row in rows:
        if remaining <= 0:
            break
        if remaining >= len(row):
            selected.extend(row)
            remaining -= len(row)
            continue
        start = max(0, (len(row) - remaining) // 2)
        selected.extend(row[start:start + remaining])
        remaining = 0
    return selected if remaining == 0 else None


def _complete_generic_floor_band(
    request: PackRequest,
    floor_stacks: list[PackedStack],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
) -> list[PackedStack] | None:
    """Add same-SKU upper pieces to a generic floor-band candidate."""
    final_stacks = list(floor_stacks)
    floor_by_cargo: dict[str, list[PackedStack]] = defaultdict(list)
    next_instance: Counter[str] = Counter()
    for stack in floor_stacks:
        floor_by_cargo[stack.unit.cargo.id].append(stack)
        next_instance[stack.unit.cargo.id] += 1

    for cargo_id, floor_for_cargo in floor_by_cargo.items():
        remaining = quantity_by_cargo[cargo_id] - len(floor_for_cargo)
        extra_capacity = capacity_by_cargo[cargo_id] - 1
        if remaining <= 0:
            continue
        if extra_capacity <= 0:
            return None
        supports = _choose_generic_upper_supports(
            request,
            floor_for_cargo,
            (remaining + extra_capacity - 1) // extra_capacity,
        )
        if supports is None:
            return None
        for index, support in enumerate(supports):
            if remaining <= 0:
                break
            extra = min(extra_capacity, remaining)
            upper = replace(
                support.unit,
                id=f"{cargo_id}-generic-upper-{index}",
                count=extra,
                stack_height_mm=extra * support.unit.item_height_mm,
                total_weight_g=extra * support.unit.cargo.weight_g,
                first_instance_index=next_instance[cargo_id],
            )
            final_stacks.append(
                replace(
                    support,
                    unit=upper,
                    z_mm=support.unit.item_height_mm,
                    step=2,
                )
            )
            remaining -= extra
            next_instance[cargo_id] += extra
        if remaining:
            return None
    return final_stacks


def _generic_floor_band_layout(
    request: PackRequest,
    by_cargo: dict[str, StackUnit],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
    strategy: Literal["fill", "stable", "easy", "strict"],
    candidate_limit: int = GENERIC_CANDIDATE_LIMIT,
) -> list[PackedStack] | None:
    """Search finite customer-style rows for non-template pallet mixes."""
    if not by_cargo or len(by_cargo) > 8:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c
    usable_width = request.container.inner_width_mm - 2 * c
    options_by_cargo = {
        cargo_id: _floor_orientation_options(request, unit)
        for cargo_id, unit in by_cargo.items()
    }
    if any(not options for options in options_by_cargo.values()):
        return None

    minimum_floor = {
        cargo_id: (
            quantity_by_cargo[cargo_id] + capacity_by_cargo[cargo_id] - 1
        ) // capacity_by_cargo[cargo_id]
        for cargo_id in by_cargo
    }

    def count_options(cargo_id: str) -> list[int]:
        minimum = minimum_floor[cargo_id]
        quantity = quantity_by_cargo[cargo_id]
        return sorted({
            minimum,
            min(quantity, minimum + 1),
            min(quantity, minimum + 3),
            quantity,
        })

    cargo_ids = list(by_cargo)
    ai_hint = request.ai_layout_hint or {}
    ai_guided = bool(ai_hint)
    candidate_limit = max(
        1,
        min(
            candidate_limit,
            AI_GUIDED_CANDIDATE_LIMIT if ai_guided else GENERIC_CANDIDATE_LIMIT,
        ),
    )
    order_variants: list[list[str]] = []
    preferred_orders = [
        cargo_ids,
        sorted(
            cargo_ids,
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.unload_order,
                -by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            ),
        ),
        sorted(
            cargo_ids,
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.weight_g,
                cargo_id,
            ),
        ),
    ]
    if strategy == "stable":
        preferred_orders.reverse()
    elif strategy == "easy":
        preferred_orders = [preferred_orders[0], preferred_orders[1]]
    hinted_order = ai_hint.get("sku_order")
    if isinstance(hinted_order, list):
        hinted_ids = [
            cargo_id for cargo_id in hinted_order
            if isinstance(cargo_id, str) and cargo_id in by_cargo
        ]
        hinted_ids.extend(cargo_id for cargo_id in cargo_ids if cargo_id not in hinted_ids)
        if set(hinted_ids) == set(cargo_ids):
            preferred_orders.insert(0, hinted_ids)
    if ai_guided and isinstance(hinted_order, list):
        preferred_orders = [preferred_orders[0]]
    for order in preferred_orders:
        if order not in order_variants:
            order_variants.append(order)

    hinted_orientations = ai_hint.get("orientations")
    option_variants = []
    for cargo_id in cargo_ids:
        options = options_by_cargo[cargo_id][:3]
        if isinstance(hinted_orientations, dict):
            options = sorted(
                options,
                key=lambda option: (
                    option[0].value != hinted_orientations.get(cargo_id),
                    option[0].value,
                ),
            )
        option_variants.append(options[:1] if ai_guided else options)

    preferred_row_groups = {
        frozenset(group)
        for group in ai_hint.get("row_groups", [])
        if isinstance(group, list)
        and len(group) == 2
        and all(isinstance(cargo_id, str) and cargo_id in by_cargo for cargo_id in group)
    }
    candidates: list[tuple[tuple[float, ...], list[PackedStack]]] = []
    examined_candidates = 0
    for chosen_options in itertools.product(*option_variants):
        option_by_cargo = dict(zip(cargo_ids, chosen_options))
        columns_by_cargo = {
            cargo_id: option_by_cargo[cargo_id][3]
            for cargo_id in cargo_ids
        }
        count_lists = [count_options(cargo_id) for cargo_id in cargo_ids]
        for chosen_counts in itertools.product(*count_lists):
            counts = dict(zip(cargo_ids, chosen_counts))
            if any(
                counts[cargo_id] < minimum_floor[cargo_id]
                or counts[cargo_id] > quantity_by_cargo[cargo_id]
                for cargo_id in cargo_ids
            ):
                continue
            for cargo_order in order_variants:
                examined_candidates += 1
                if examined_candidates > candidate_limit:
                    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None
                full_rows: list[tuple[tuple[str, int], ...]] = []
                partial_rows: list[tuple[str, int]] = []
                for cargo_id in cargo_order:
                    columns = columns_by_cargo[cargo_id]
                    full_count, remainder = divmod(counts[cargo_id], columns)
                    full_rows.extend(
                        ((cargo_id, columns),)
                        for _ in range(full_count)
                    )
                    if remainder:
                        partial_rows.append((cargo_id, remainder))

                mixed_rows: list[tuple[tuple[str, int], ...]] = []
                pending = list(partial_rows)
                while pending:
                    cargo_id, count = pending.pop(0)
                    option = option_by_cargo[cargo_id]
                    best_index: int | None = None
                    best_key: tuple[int, int, int] | None = None
                    for index, (other_id, other_count) in enumerate(pending):
                        other_option = option_by_cargo[other_id]
                        width = (
                            count * option[2]
                            + other_count * other_option[2]
                            + gap
                        )
                        if width <= usable_width:
                            key = (
                                int(frozenset((cargo_id, other_id)) not in preferred_row_groups),
                                usable_width - width,
                                index,
                            )
                            if best_key is None or key < best_key:
                                best_index, best_key = index, key
                    if best_index is None:
                        mixed_rows.append(((cargo_id, count),))
                    else:
                        other_id, other_count = pending.pop(best_index)
                        mixed_rows.append(
                            ((cargo_id, count), (other_id, other_count))
                        )

                rows = full_rows + mixed_rows
                floor_stacks: list[PackedStack] = []
                next_instance: Counter[str] = Counter()
                x_cursor = c
                valid_rows = True
                for row_index, row in enumerate(rows):
                    row_length = max(
                        option_by_cargo[cargo_id][1]
                        for cargo_id, _ in row
                    )
                    row_width = sum(
                        count * option_by_cargo[cargo_id][2]
                        for cargo_id, count in row
                    ) + max(0, len(row) - 1) * gap
                    if x_cursor + row_length > door_limit or row_width > usable_width:
                        valid_rows = False
                        break
                    y_cursor = c
                    if strategy == "stable":
                        y_cursor += max(0, usable_width - row_width) // 2
                    elif strategy == "easy":
                        y_cursor += max(0, usable_width - row_width) // 4
                    for cargo_id, count in row:
                        orientation, length, width, _ = option_by_cargo[cargo_id]
                        _, _, height = by_cargo[cargo_id].cargo.dimensions_for(orientation)
                        for _ in range(count):
                            instance_index = next_instance[cargo_id]
                            one_piece = replace(
                                by_cargo[cargo_id],
                                id=f"{cargo_id}-generic-floor-{instance_index}",
                                orientation=orientation,
                                count=1,
                                length_mm=length,
                                width_mm=width,
                                item_height_mm=height,
                                stack_height_mm=height,
                                total_weight_g=by_cargo[cargo_id].cargo.weight_g,
                                first_instance_index=instance_index,
                            )
                            floor_stacks.append(
                                PackedStack(
                                    unit=one_piece,
                                    x_mm=x_cursor,
                                    y_mm=y_cursor,
                                    step=1,
                                )
                            )
                            next_instance[cargo_id] += 1
                            y_cursor += width + gap
                    x_cursor += row_length + gap
                if not valid_rows or len(floor_stacks) != sum(counts.values()):
                    continue
                completed = _complete_generic_floor_band(
                    request,
                    floor_stacks,
                    quantity_by_cargo,
                    capacity_by_cargo,
                )
                if completed is None:
                    continue
                placements = _expand_stacks(request, completed, "high_fill")
                validation = validate_solution(
                    request.container,
                    request.cargo_items,
                    placements,
                    item_gap_mm=gap,
                )
                if not validation.valid:
                    continue
                score = _layout_quality_score(
                    request,
                    placements,
                    {
                        "fill": "high_fill",
                        "stable": "stable",
                        "easy": "easy",
                        "strict": "easy",
                    }[strategy],
                ) + (
                    float(sum(counts.values())),
                    -float(len(rows)),
                    -float(x_cursor),
                )
                candidates.append((score, completed))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _shelf_mixed_floor_layout(
    request: PackRequest,
    by_cargo: dict[str, StackUnit],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
    strategy: Literal["fill", "stable", "easy", "strict"],
) -> list[PackedStack] | None:
    """Build non-overlapping mixed SKU floor rows before using free-form packing.

    MaxRects is useful as a safety fallback, but its rectangles can start at
    different X positions and create a large transverse visual hole. This
    candidate uses a small shelf search so every row is continuous across Y and
    rows never overlap on X. Upper pieces are then added only above same-SKU
    floor supports.
    """
    if not 4 <= len(by_cargo) <= 5:
        return None

    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_width = request.container.inner_width_mm - 2 * c
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c
    floor_required = {
        cargo_id: (
            quantity_by_cargo[cargo_id] + capacity_by_cargo[cargo_id] - 1
        ) // capacity_by_cargo[cargo_id]
        for cargo_id in by_cargo
        if capacity_by_cargo[cargo_id] > 0
    }
    if len(floor_required) != len(by_cargo):
        return None

    options_by_cargo = {
        cargo_id: _floor_orientation_options(request, unit)
        for cargo_id, unit in by_cargo.items()
    }
    if any(not options for options in options_by_cargo.values()):
        return None

    request_order = [item.id for item in request.cargo_items if item.id in by_cargo]
    order_variants = [
        sorted(
            by_cargo,
            key=lambda cargo_id: (
                -options_by_cargo[cargo_id][0][2],
                -options_by_cargo[cargo_id][0][1],
                cargo_id,
            ),
        ),
        sorted(
            by_cargo,
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.weight_g,
                -by_cargo[cargo_id].cargo.length_mm
                * by_cargo[cargo_id].cargo.width_mm,
                cargo_id,
            ),
        ),
        request_order,
    ]
    if strategy == "easy":
        order_variants = [request_order, order_variants[0]]
    elif strategy == "stable":
        order_variants = [order_variants[1], order_variants[0], request_order]

    candidates: list[tuple[tuple[float, ...], list[PackedStack]]] = []
    cargo_ids = list(by_cargo)
    orientation_options = [options_by_cargo[cargo_id][:2] for cargo_id in cargo_ids]
    for chosen_options in itertools.product(*orientation_options):
        option_by_cargo = dict(zip(cargo_ids, chosen_options))
        for cargo_order in order_variants:
            rows: list[list[tuple[str, tuple[Orientation, int, int, int]]]] = []
            row_widths: list[int] = []
            row_lengths: list[int] = []
            # Put each SKU's floor pieces into the tightest existing shelf.
            # This keeps the search deterministic while mixing SKU widths.
            items = [
                (cargo_id, option_by_cargo[cargo_id])
                for cargo_id in cargo_order
                for _ in range(floor_required[cargo_id])
            ]
            if strategy == "fill":
                items.sort(key=lambda item: (-item[1][2], -item[1][1], item[0]))
            for cargo_id, option in items:
                _, length, width, _ = option
                best_row: int | None = None
                best_remaining: int | None = None
                for row_index, used_width in enumerate(row_widths):
                    required_width = used_width + gap + width if rows[row_index] else width
                    if required_width > usable_width:
                        continue
                    remaining = usable_width - required_width
                    if best_remaining is None or remaining < best_remaining:
                        best_row = row_index
                        best_remaining = remaining
                if best_row is None:
                    rows.append([(cargo_id, option)])
                    row_widths.append(width)
                    row_lengths.append(length)
                else:
                    rows[best_row].append((cargo_id, option))
                    row_widths[best_row] += gap + width
                    row_lengths[best_row] = max(row_lengths[best_row], length)

            total_length = sum(row_lengths) + max(0, len(rows) - 1) * gap
            if not rows or c + total_length > door_limit:
                continue

            floor_stacks: list[PackedStack] = []
            next_instance: Counter[str] = Counter()
            x_cursor = c
            for row_index, row in enumerate(rows):
                y_cursor = c
                if strategy == "stable":
                    y_cursor += max(0, usable_width - row_widths[row_index]) // 2
                elif strategy == "easy":
                    y_cursor += max(0, usable_width - row_widths[row_index]) // 4
                for cargo_id, option in row:
                    orientation, length, width, _ = option
                    _, _, height = by_cargo[cargo_id].cargo.dimensions_for(orientation)
                    instance_index = next_instance[cargo_id]
                    floor_unit = replace(
                        by_cargo[cargo_id],
                        id=f"{cargo_id}-shelf-floor-{instance_index}",
                        orientation=orientation,
                        count=1,
                        length_mm=length,
                        width_mm=width,
                        item_height_mm=height,
                        stack_height_mm=height,
                        total_weight_g=by_cargo[cargo_id].cargo.weight_g,
                        first_instance_index=instance_index,
                    )
                    floor_stacks.append(
                        PackedStack(
                            unit=floor_unit,
                            x_mm=x_cursor,
                            y_mm=y_cursor,
                            step=row_index + 1,
                        )
                    )
                    next_instance[cargo_id] += 1
                    y_cursor += width + gap
                x_cursor += row_lengths[row_index] + gap

            completed = _complete_generic_floor_band(
                request,
                floor_stacks,
                quantity_by_cargo,
                capacity_by_cargo,
            )
            if completed is None:
                continue
            placements = _expand_stacks(request, completed, "high_fill")
            validation = validate_solution(
                request.container,
                request.cargo_items,
                placements,
                item_gap_mm=gap,
            )
            if not validation.valid:
                continue
            profile_name = {
                "fill": "high_fill",
                "stable": "stable",
                "easy": "easy",
                "strict": "easy",
            }[strategy]
            quality = _layout_quality(request, placements)
            score = _layout_quality_score(request, placements, profile_name) + (
                -float(len(rows)),
                -float(total_length),
                -float(quality.floor_bbox_void_pct),
            )
            candidates.append((score, completed))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _optimized_shelf_mixed_floor_layout(
    request: PackRequest,
    by_cargo: dict[str, StackUnit],
    quantity_by_cargo: Counter[str],
    capacity_by_cargo: dict[str, int],
    strategy: Literal["fill", "stable", "easy", "strict"],
    floor_target_override: dict[str, int] | None = None,
) -> list[PackedStack] | None:
    """Find compact mixed rows with an exact finite row-composition search."""
    if not 4 <= len(by_cargo) <= 6:
        return None

    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_width = request.container.inner_width_mm - 2 * c
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c
    floor_required = {
        cargo_id: (
            quantity_by_cargo[cargo_id] + capacity_by_cargo[cargo_id] - 1
        ) // capacity_by_cargo[cargo_id]
        for cargo_id in by_cargo
        if capacity_by_cargo[cargo_id] > 0
    }
    if floor_target_override is not None:
        floor_required = {
            cargo_id: floor_target_override.get(cargo_id, floor_required[cargo_id])
            for cargo_id in floor_required
        }
    if len(floor_required) != len(by_cargo):
        return None

    options_by_cargo = {
        cargo_id: _floor_orientation_options(request, unit)
        for cargo_id, unit in by_cargo.items()
    }
    if any(not options for options in options_by_cargo.values()):
        return None

    cargo_ids = list(by_cargo)
    request_order = [item.id for item in request.cargo_items if item.id in by_cargo]
    order = {
        "fill": sorted(
            cargo_ids,
            key=lambda cargo_id: (
                -options_by_cargo[cargo_id][0][2],
                -options_by_cargo[cargo_id][0][1],
                cargo_id,
            ),
        ),
        "stable": sorted(
            cargo_ids,
            key=lambda cargo_id: (
                -by_cargo[cargo_id].cargo.weight_g,
                cargo_id,
            ),
        ),
        "easy": request_order,
        "strict": request_order,
    }[strategy]

    candidates: list[tuple[tuple[float, ...], list[PackedStack]]] = []
    orientation_variants = [options_by_cargo[cargo_id][:2] for cargo_id in cargo_ids]
    for chosen_options in itertools.product(*orientation_variants):
        option_by_cargo = dict(zip(cargo_ids, chosen_options))
        max_counts = {
            cargo_id: min(
                floor_required[cargo_id],
                max(1, usable_width // option_by_cargo[cargo_id][2]),
            )
            for cargo_id in cargo_ids
        }
        row_patterns: list[tuple[tuple[int, ...], int]] = []
        for pattern in itertools.product(
            *(range(max_counts[cargo_id] + 1) for cargo_id in cargo_ids)
        ):
            piece_count = sum(pattern)
            if not piece_count:
                continue
            row_width = sum(
                count * option_by_cargo[cargo_id][2]
                for cargo_id, count in zip(cargo_ids, pattern)
            ) + max(0, piece_count - 1) * gap
            if row_width > usable_width:
                continue
            row_length = max(
                option_by_cargo[cargo_id][1]
                for cargo_id, count in zip(cargo_ids, pattern)
                if count
            )
            row_patterns.append((pattern, row_length))

        pattern_solution_limit = 1

        upper_ids = {
            cargo_id
            for cargo_id in cargo_ids
            if quantity_by_cargo[cargo_id] > floor_required[cargo_id]
        }

        def continuity_key(
            candidate: tuple[int, int, tuple[tuple[int, ...], ...]],
        ) -> tuple[int, int, int]:
            unsafe_rows = 0
            for pattern in candidate[2]:
                active_upper_lengths = [
                    option_by_cargo[cargo_id][1]
                    for cargo_id in upper_ids
                    if pattern[cargo_ids.index(cargo_id)]
                ]
                if active_upper_lengths:
                    row_length = max(
                        option_by_cargo[cargo_id][1]
                        for cargo_id in cargo_ids
                        if pattern[cargo_ids.index(cargo_id)]
                    )
                    unsafe_rows += row_length > max(active_upper_lengths)
            return unsafe_rows, candidate[0], candidate[1]

        @lru_cache(maxsize=None)
        def solve(
            remaining: tuple[int, ...],
        ) -> tuple[tuple[int, int, tuple[tuple[int, ...], ...]], ...]:
            if not any(remaining):
                return ((0, 0, ()),)
            first = next(index for index, count in enumerate(remaining) if count)
            candidates: list[tuple[int, int, tuple[tuple[int, ...], ...]]] = []
            for pattern, row_length in row_patterns:
                if not pattern[first] or any(
                    count > available
                    for count, available in zip(pattern, remaining)
                ):
                    continue
                next_remaining = tuple(
                    available - count
                    for available, count in zip(remaining, pattern)
                )
                child = solve(next_remaining)
                if not child:
                    continue
                for child_length, child_rows, child_patterns in child:
                    total_length = row_length + child_length
                    if child_rows:
                        total_length += gap
                    candidates.append(
                        (
                            total_length,
                            child_rows + 1,
                            (pattern,) + child_patterns,
                        )
                    )
            unique_candidates = {
                candidate[2]: candidate
                for candidate in candidates
            }
            shortest = sorted(
                unique_candidates.values(),
                key=lambda item: item[:2],
            )[:pattern_solution_limit]
            if len(cargo_ids) < 6:
                return tuple(shortest)
            continuous = sorted(
                unique_candidates.values(),
                key=continuity_key,
            )[:pattern_solution_limit]
            selected = {
                candidate[2]: candidate
                for candidate in (*shortest, *continuous)
            }
            return tuple(sorted(selected.values(), key=lambda item: item[:2]))

        solved_candidates = solve(tuple(floor_required[cargo_id] for cargo_id in cargo_ids))
        if not solved_candidates or c + solved_candidates[0][0] > door_limit:
            continue

        solved = solved_candidates[0]
        _, row_count, patterns = solved
        pattern_variants = [candidate[2] for candidate in solved_candidates]
        def bridge_key(pattern: tuple[int, ...]) -> tuple[int, int, int]:
            upper_lengths = [
                option_by_cargo[cargo_id][1]
                for cargo_id in cargo_ids
                if pattern[cargo_ids.index(cargo_id)] and cargo_id in upper_ids
            ]
            upper_count = sum(
                pattern[cargo_ids.index(cargo_id)]
                for cargo_id in upper_ids
            )
            edge_count = sum(
                pattern[cargo_ids.index(cargo_id)]
                for cargo_id in cargo_ids
                if cargo_id not in upper_ids
            )
            return (-max(upper_lengths, default=0), -upper_count, edge_count)

        for candidate_patterns in tuple(pattern_variants):
            bridge_patterns = tuple(sorted(candidate_patterns, key=bridge_key))
            if bridge_patterns not in pattern_variants:
                pattern_variants.append(bridge_patterns)

        if len(cargo_ids) >= 6:
            # Keep single-layer SKUs at the two ends so stackable cargo can form
            # one contiguous upper core instead of several isolated islands.
            edge_ids = {
                cargo_id
                for cargo_id in cargo_ids
                if capacity_by_cargo[cargo_id] <= 1
                or quantity_by_cargo[cargo_id] <= floor_required[cargo_id]
            }
            for candidate_patterns in tuple(pattern_variants):
                edge_rows = [
                    pattern
                    for pattern in candidate_patterns
                    if any(
                        pattern[cargo_ids.index(cargo_id)]
                        for cargo_id in edge_ids
                    )
                ]
                core_rows = [
                    pattern
                    for pattern in candidate_patterns
                    if pattern not in edge_rows
                ]
                if not edge_rows or not core_rows:
                    continue
                left_edge_count = (len(edge_rows) + 1) // 2
                edge_grouped = tuple(
                    edge_rows[:left_edge_count]
                    + core_rows
                    + list(reversed(edge_rows[left_edge_count:]))
                )
                if edge_grouped not in pattern_variants:
                    pattern_variants.append(edge_grouped)

        for ordered_patterns in pattern_variants:
            floor_stacks: list[PackedStack] = []
            next_instance: Counter[str] = Counter()
            x_cursor = c
            for row_index, pattern in enumerate(ordered_patterns):
                active_ids = [
                    cargo_id
                    for cargo_id in order
                    if pattern[cargo_ids.index(cargo_id)]
                ]
                row_width = sum(
                    pattern[cargo_ids.index(cargo_id)]
                    * option_by_cargo[cargo_id][2]
                    for cargo_id in cargo_ids
                ) + max(0, sum(pattern) - 1) * gap
                y_cursor = c
                if strategy == "stable":
                    y_cursor += max(0, usable_width - row_width) // 2
                elif strategy == "easy":
                    y_cursor += max(0, usable_width - row_width) // 4
                row_length = max(
                    option_by_cargo[cargo_id][1]
                    for cargo_id in cargo_ids
                    if pattern[cargo_ids.index(cargo_id)]
                )
                for cargo_id in active_ids:
                    orientation, length, width, _ = option_by_cargo[cargo_id]
                    for _ in range(pattern[cargo_ids.index(cargo_id)]):
                        _, _, height = by_cargo[cargo_id].cargo.dimensions_for(orientation)
                        instance_index = next_instance[cargo_id]
                        floor_unit = replace(
                            by_cargo[cargo_id],
                            id=f"{cargo_id}-optimized-shelf-{instance_index}",
                            orientation=orientation,
                            count=1,
                            length_mm=length,
                            width_mm=width,
                            item_height_mm=height,
                            stack_height_mm=height,
                            total_weight_g=by_cargo[cargo_id].cargo.weight_g,
                            first_instance_index=instance_index,
                        )
                        floor_stacks.append(
                            PackedStack(
                                unit=floor_unit,
                                x_mm=x_cursor,
                                y_mm=y_cursor,
                                step=row_index + 1,
                            )
                        )
                        next_instance[cargo_id] += 1
                        y_cursor += width + gap
                x_cursor += row_length + gap

            completed = _complete_generic_floor_band(
                request,
                floor_stacks,
                quantity_by_cargo,
                capacity_by_cargo,
            )
            if completed is None:
                continue
            placements = _expand_stacks(request, completed, "high_fill")
            validation = validate_solution(
                request.container,
                request.cargo_items,
                placements,
                item_gap_mm=gap,
            )
            if not validation.valid:
                continue
            profile_name = {
                "fill": "high_fill",
                "stable": "stable",
                "easy": "easy",
                "strict": "easy",
            }[strategy]
            quality = _layout_quality(request, placements)
            score = _candidate_selection_score(request, placements, profile_name) + (
                -float(row_count),
                -float(solved[0]),
                -float(quality.floor_bbox_void_pct),
            )
            candidates.append((score, completed))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _pure_pallet_floor_first_layout(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    strategy: Literal["fill", "stable", "easy", "strict"],
) -> list[PackedStack] | None:
    """Generate floor-first pallet candidates, then stack only on floor slots.

    ``_build_stack_units`` groups vertically stackable pallets into StackUnit
    objects. That representation is useful for the general packer, but it
    hides the number of available floor slots. This path deliberately expands
    pallets to one-piece units for the 2D floor search, then adds the remaining
    instances back as centered upper stacks.
    """
    if (
        not units
        or any(isinstance(unit, CompositeUnit) for unit in units)
        or any(unit.cargo.kind != "pallet" for unit in units)
    ):
        return None

    c = request.container.clearance_mm
    gap = request.item_gap_mm
    center_x = request.container.inner_length_mm / 2
    door_limit = request.container.inner_length_mm - request.door_buffer_mm - c

    by_cargo: dict[str, StackUnit] = {}
    for unit in units:
        by_cargo.setdefault(unit.cargo.id, unit)
    if not by_cargo:
        return None

    available_height = request.container.inner_height_mm - 2 * c
    capacity_by_cargo = {
        cargo_id: _stack_capacity(
            unit.cargo,
            unit.orientation,
            available_height,
            gap,
        )
        for cargo_id, unit in by_cargo.items()
    }
    quantity_by_cargo: Counter[str] = Counter()
    for unit in units:
        quantity_by_cargo[unit.cargo.id] += unit.count
    minimum_floor = {
        cargo_id: (
            quantity_by_cargo[cargo_id] + capacity - 1
        ) // capacity
        for cargo_id, capacity in capacity_by_cargo.items()
        if capacity > 0
    }
    if len(minimum_floor) != len(by_cargo):
        return None

    band_layout = _same_sku_band_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        strategy,
    )
    if band_layout is not None and not request.ai_layout_hint:
        return band_layout

    mixed_band_layout = _mixed_floor_band_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        strategy,
    )
    guided_candidates: list[list[PackedStack]] = []
    if request.ai_layout_hint:
        guided_candidates = [
            candidate
            for candidate in (band_layout, mixed_band_layout)
            if candidate is not None
        ]
    elif mixed_band_layout is not None:
        return mixed_band_layout

    optimized_candidates = [
        candidate
        for candidate in (
            *guided_candidates,
            _optimized_shelf_mixed_floor_layout(
                request,
                by_cargo,
                quantity_by_cargo,
                capacity_by_cargo,
                strategy,
            ),
            *(
                _optimized_shelf_mixed_floor_layout(
                    request,
                    by_cargo,
                    quantity_by_cargo,
                    capacity_by_cargo,
                    strategy,
                    {
                        **minimum_floor,
                        cargo_id: minimum_floor[cargo_id] + 1,
                    },
                )
                for cargo_id in by_cargo
                if len(by_cargo) >= 6
                and capacity_by_cargo[cargo_id] > 1
                and quantity_by_cargo[cargo_id] > minimum_floor[cargo_id]
            ),
        )
        if candidate is not None
    ]
    if optimized_candidates:
        profile_name = {
            "fill": "high_fill",
            "stable": "stable",
            "easy": "easy",
            "strict": "easy",
        }[strategy]
        return max(
            optimized_candidates,
            key=lambda candidate: _candidate_selection_score(
                request,
                _expand_stacks(request, candidate, profile_name),
                profile_name,
            ),
        )

    shelf_layout = _shelf_mixed_floor_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        strategy,
    )
    if shelf_layout is not None:
        return shelf_layout

    generic_band_layout = _generic_floor_band_layout(
        request,
        by_cargo,
        quantity_by_cargo,
        capacity_by_cargo,
        strategy,
    )
    if generic_band_layout is not None:
        return generic_band_layout

    def one_piece_units(counts: dict[str, int]) -> list[StackUnit]:
        result: list[StackUnit] = []
        for cargo_id in sorted(counts):
            sample = by_cargo[cargo_id]
            for index in range(counts[cargo_id]):
                result.append(
                    replace(
                        sample,
                        id=f"{cargo_id}-floor-{index}",
                        count=1,
                        stack_height_mm=sample.item_height_mm,
                        total_weight_g=sample.cargo.weight_g,
                        first_instance_index=index,
                    )
                )
        return result

    # Keep the search small for large orders while retaining the dense middle
    # values needed by mixed-size pallet orders such as the A/B/C acceptance case.
    def count_options(cargo_id: str) -> list[int]:
        minimum = minimum_floor[cargo_id]
        quantity = quantity_by_cargo[cargo_id]
        values = {
            minimum,
            min(quantity, minimum + 4),
            min(quantity, minimum + 8),
            min(quantity, minimum + 12),
            max(minimum, quantity - 12),
            max(minimum, quantity - 8),
            max(minimum, quantity - 4),
            quantity,
            (minimum + quantity) // 2,
        }
        if quantity <= 36:
            values.add(max(minimum, quantity - 2))
        return sorted(values)

    cargo_ids = sorted(by_cargo)
    option_lists = [count_options(cargo_id) for cargo_id in cargo_ids]
    combinations: list[dict[str, int]] = []
    total_combinations = 1
    for options in option_lists:
        total_combinations *= len(options)
    if total_combinations <= FLOOR_COMBINATION_LIMIT:
        combinations = [
            dict(zip(cargo_ids, values))
            for values in itertools.product(*option_lists)
        ]
    else:
        # Deterministic diagonal sampling prevents a large all-pallet order
        # from turning the floor-first path into an unbounded Cartesian search.
        for offset in range(max(len(options) for options in option_lists)):
            combinations.append(
                {
                    cargo_id: options[(offset + index) % len(options)]
                    for index, (cargo_id, options) in enumerate(
                        zip(cargo_ids, option_lists)
                    )
                }
            )

    orders = {
        "fill": ("volume", "footprint", "pieces"),
        "stable": ("weight", "volume", "footprint"),
        "easy": ("sku", "pieces", "volume"),
        "strict": ("volume", "footprint", "pieces"),
    }[strategy]
    candidates: list[tuple[tuple[float, ...], list[PackedStack]]] = []
    for counts in combinations:
        if any(
            counts[cargo_id] * capacity_by_cargo[cargo_id]
            < quantity_by_cargo[cargo_id]
            for cargo_id in cargo_ids
        ):
            continue
        floor_units = one_piece_units(counts)
        candidate_orders = orders
        if any(unit.cargo.unload_order for unit in floor_units):
            candidate_orders = ("unload",) + orders
        for algorithm in PACK_ALGOS:
            for order in candidate_orders:
                floor_stacks = _pack_units(request, floor_units, algorithm, order)
                if len(floor_stacks) != len(floor_units):
                    continue
                if any(
                    stack.x_mm + stack.length_mm > door_limit
                    for stack in floor_stacks
                ):
                    continue
                floor_stacks = [
                    replace(stack, step=1)
                    for stack in floor_stacks
                ]

                by_floor_cargo: dict[str, list[PackedStack]] = defaultdict(list)
                for stack in floor_stacks:
                    by_floor_cargo[stack.unit.cargo.id].append(stack)
                final_stacks = list(floor_stacks)
                upper_count = 0
                upper_stack_count_by_cargo: Counter[str] = Counter()
                for cargo_id in cargo_ids:
                    slots = sorted(
                        by_floor_cargo[cargo_id],
                        key=lambda stack: (
                            abs(stack.x_mm + stack.length_mm / 2 - center_x),
                            stack.y_mm,
                            stack.unit.first_instance_index,
                        ),
                    )
                    remaining = quantity_by_cargo[cargo_id] - counts[cargo_id]
                    next_instance = counts[cargo_id]
                    extra_capacity = capacity_by_cargo[cargo_id] - 1
                    if remaining > len(slots) * extra_capacity:
                        break
                    for index, slot in enumerate(slots):
                        if remaining <= 0:
                            break
                        extra = min(extra_capacity, remaining)
                        upper_unit = replace(
                            slot.unit,
                            id=f"{cargo_id}-upper-{index}",
                            count=extra,
                            stack_height_mm=extra * slot.unit.item_height_mm,
                            total_weight_g=extra * slot.unit.cargo.weight_g,
                            first_instance_index=next_instance,
                        )
                        final_stacks.append(
                            replace(
                                slot,
                                unit=upper_unit,
                                z_mm=slot.unit.item_height_mm,
                                step=2,
                            )
                        )
                        remaining -= extra
                        next_instance += extra
                        upper_count += extra
                        upper_stack_count_by_cargo[cargo_id] += extra
                    if remaining:
                        break
                else:
                    placements = _expand_stacks(request, final_stacks, "high_fill")
                    validation = validate_solution(
                        request.container,
                        request.cargo_items,
                        placements,
                        item_gap_mm=gap,
                    )
                    if not validation.valid:
                        continue
                    quality_ok = _upper_layout_quality_ok(request, placements)
                    component_count, upper_center, _ = _upper_layout_diagnostics(
                        request,
                        placements,
                    )
                    floor_span = (
                        max(
                            placement.x_mm + placement.length_mm
                            for placement in placements
                            if placement.z_mm == c
                        )
                        - min(
                            placement.x_mm
                            for placement in placements
                            if placement.z_mm == c
                        )
                    )
                    profile_name = {
                        "fill": "high_fill",
                        "stable": "stable",
                        "easy": "easy",
                        "strict": "easy",
                    }[strategy]
                    score = _layout_quality_score(
                        request,
                        placements,
                        profile_name,
                    ) + (
                        int(quality_ok),
                        floor_span,
                        -abs(upper_center - center_x),
                        -component_count,
                    )
                    candidates.append((score, final_stacks))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _layer_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
    allow_partial: bool = False,
    band_grid: bool = True,
) -> list[PackedStack] | None:
    """分层铺满（floor-layer-first）。

    第 1 层：托盘（含上叠散箱的 CompositeUnit）与散箱单件用 2D 装箱铺满
    整个柜底（贴壁、密集）；放不下的散箱单件按“柱高”叠到第 1 层同 SKU
    位置正上方（同 footprint → 100% 完整支撑）。每位置叠高层数不超过
    该 SKU 的栈容量（max_layers/高度/承重）。

    ``allow_partial`` 为 True 时托盘或散箱放不下可丢弃（少装并披露）；
    False 时放不下返回 None 由调用方回退。
    """
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    pallets = [unit for unit in units if unit.cargo.kind == "pallet"]
    cartons = [unit for unit in units if unit.cargo.kind == "carton"]
    # 托盘（含可叠托盘）→ 托盘带（居中）；散箱 → 托盘带两端/空隙（混装时）。
    # 可叠单位（散箱栈 + 多层托盘栈 count>1）→ 拆单件铺底 + 柱高叠高；
    # 单层托盘（含 CompositeUnit）→ 直接铺第 1 层
    single_pallets = [
        unit for unit in pallets
        if isinstance(unit, CompositeUnit) or unit.count == 1
    ]
    stackable_pallets = [
        unit for unit in pallets
        if not isinstance(unit, CompositeUnit) and unit.count > 1
    ]
    stackables = list(cartons) + list(stackable_pallets)
    if not single_pallets and not stackable_pallets and not cartons:
        return None

    placed: list[PackedStack] = []
    slot_units: dict[str, list[StackUnit]] = {}  # cargo_id -> 第 1 层已放单件（旋转后）
    slot_rects: dict[str, list[tuple[int, int]]] = {}  # cargo_id -> 全局坐标（含 clearance）

    # 1) 托盘带：所有托盘单件（单层 + 可叠托盘全部单件）用 rectpack 排满后
    #    整体居中（重货集中中间）。可叠托盘拆全部单件尝试铺第 1 层
    #    （先铺满底层），放不下的单件归入柱高分配（同 SKU 底位正上方叠高）。
    #    先卸后装：卸货顺序大的（后卸）先铺。
    tray_units: list[StackUnit | CompositeUnit] = list(single_pallets)
    for unit in stackable_pallets:
        for i in range(unit.count):
            tray_units.append(
                replace(
                    unit,
                    count=1,
                    stack_height_mm=unit.item_height_mm,
                    total_weight_g=unit.cargo.weight_g,
                    first_instance_index=unit.first_instance_index + i,
                )
            )
    # 放不下的可叠托盘单件 → 归入柱高分配（与散箱"放不下随后叠高"一致）。
    # 柱高分配按 SKU 总件数 total 分到 k 个第 1 层底位，放不下的件自动叠高。
    stackable_pallet_ids = {u.id for u in stackable_pallets}
    # 托盘带：优先用网格（重托盘放中间行 → 重量集中中间、两头别偏重），
    tray_units.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    pallet_slots: list[tuple[int, int, StackUnit | CompositeUnit]] = []  # (x, y, unit)
    if tray_units and not band_grid:
        # 装得多：托盘带用 rectpack 从柜头铺（体积优先、位置自然），
        # 与"更稳妥"的居中网格配平布局区分
        packer = MaxRectsBssf(usable_length, usable_width, rot=False)
        for unit in tray_units:
            rect, placed_unit = _try_add_to_pallet_top(packer, unit, request)
            if rect is None:
                if unit.id in stackable_pallet_ids:
                    # 可叠托盘单件放不下 → 跳过，由柱高分配叠到同 SKU 底位上方
                    continue
                if not allow_partial or unit.required:
                    return None
                continue  # allow_partial：单层托盘丢弃（披露）
            pallet_slots.append((int(rect.x), int(rect.y), placed_unit))
    elif tray_units:
        min_width = min(unit.width_mm for unit in tray_units)
        min_cols = max(1, usable_width // min_width)
        n_trays = len(tray_units)
        max_len = max(unit.length_mm for unit in tray_units)
        grid_ok = False
        cols = min_cols
        for cand in range(min_cols, n_trays + 1):
            rows_cand = (n_trays + cand - 1) // cand
            band_cand = rows_cand * max_len
            row_w = cand * min_width
            if band_cand <= usable_length and row_w <= usable_width:
                cols = cand
                grid_ok = True
                break
        if grid_ok:
            rows = (n_trays + cols - 1) // cols
            row_order = sorted(range(rows), key=lambda r: (abs(r - (rows - 1) / 2), r))
            per_row: dict[int, list[StackUnit | CompositeUnit]] = {r: [] for r in range(rows)}
            idx = 0
            for r in row_order:
                per_row[r] = tray_units[idx:idx + cols]
                idx += cols
            row_x: dict[int, int] = {}
            cursor_x = 0
            for r in range(rows):
                row_x[r] = cursor_x
                row_len = max((u.length_mm for u in per_row[r]), default=0)
                cursor_x += row_len + gap
            band_len = cursor_x - gap
            shift = max(0, (usable_length - band_len) // 2)
            for r in range(rows):
                # 行内按重量降序排列 + 奇偶行反向（蛇形）：重托盘在左右交替分布，
                # 使柜宽方向（y 向）重量均衡（二维配平，对齐成熟软件做法）
                row_units = sorted(per_row[r], key=lambda u: -u.total_weight_g)
                if r % 2 == 1:
                    row_units.reverse()
                y_cursor = 0
                for unit in row_units:
                    pallet_slots.append((shift + row_x[r], y_cursor, unit))
                    y_cursor += unit.width_mm + gap
        else:
            # 回退：rectpack 排托盘（允许旋转），整体平移居中
            packer = MaxRectsBssf(usable_length, usable_width, rot=False)
            tmp_rects: list[tuple[int, int, int, int, StackUnit | CompositeUnit]] = []
            for unit in tray_units:
                rect, placed_unit = _try_add_to_pallet_top(packer, unit, request)
                if rect is None:
                    if unit.id in stackable_pallet_ids:
                        continue  # 可叠托盘单件放不下 → 柱高分配叠高
                    if not allow_partial or unit.required:
                        return None
                    continue  # allow_partial：单层托盘丢弃（披露）
                tmp_rects.append(
                    (int(rect.x), int(rect.y), int(rect.width), int(rect.height), placed_unit)
                )
            if tmp_rects:
                rx_min = min(r for r, _, _, _, _ in tmp_rects)
                rx_max = max(r + w for r, _, w, _, _ in tmp_rects)
                shift = max(0, (usable_length - (rx_max - rx_min)) // 2 - rx_min)
                for rx, ry, _, _, unit in tmp_rects:
                    pallet_slots.append((rx + shift, ry, unit))
    # 注意：网格分支（band_grid=True）整带排布，可叠托盘全部单件可能超长，
    # 此时 grid_ok=False → rectpack 分支处理（放不下的归柱高）。
    for x, y, unit in pallet_slots:
        # 可叠托盘栈的底层件只登记进柱高分配（剩余件叠高），最终 PackedStack
        # 由柱高分配统一生成（count=height），避免同一托盘件被放置两次；
        # 单层托盘（single_pallets/CompositeUnit）只有 1 件、直接放置。
        if isinstance(unit, CompositeUnit) or unit.id not in stackable_pallet_ids:
            placed.append(PackedStack(unit=unit, x_mm=c + x, y_mm=c + y, step=1))
            continue
        slot_units.setdefault(unit.cargo.id, []).append(unit)
        slot_rects.setdefault(unit.cargo.id, []).append((c + x, c + y))

    # 2) 可叠单位（散箱栈 + 可叠托盘栈）单件铺第 1 层：放不下的归入“柱高”；
    #    必装优先铺底，先卸后装（卸货顺序大的后卸 → 先铺）。
    #    混装（有托盘）时：散箱填托盘带两端区 + 托盘带内 y 空隙（多 bin），
    #    保证底层铺满且托盘（重货）居中；纯散箱用单 bin 铺满整个柜底。
    cartons.sort(
        key=lambda unit: (-unit.required, -unit.cargo.unload_order, -unit.volume_mm3, unit.id)
    )
    carton_bins: list[tuple[int, int, int, int]] = []  # (x0, x1, y0, y1)
    if pallet_slots and cartons:
        pallet_x_min = min(x for x, _, _ in pallet_slots)
        pallet_x_max = max(x + unit.length_mm for x, _, unit in pallet_slots)
        # 托盘带内 y 空隙（托盘列之间的空行）
        ys = sorted((y, y + unit.width_mm) for _, y, unit in pallet_slots)
        merged: list[tuple[int, int]] = []
        for y0, y1 in ys:
            if merged and y0 <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], y1))
            else:
                merged.append((y0, y1))
        # 托盘带内空隙与两端区均与托盘带保持 item_gap，避免校验器 GAP 报错
        prev = 0
        for y0, y1 in merged:
            if y0 > prev:
                carton_bins.append(
                    (pallet_x_min + gap, pallet_x_max - gap, prev + gap, y0 - gap)
                )
            prev = max(prev, y1)
        if prev < usable_width:
            carton_bins.append(
                (pallet_x_min + gap, pallet_x_max - gap, prev + gap, usable_width - gap)
            )
        # 托盘带两端区
        carton_bins.append((c, pallet_x_min - gap, 0, usable_width))
        carton_bins.append((pallet_x_max + gap, usable_length, 0, usable_width))
        carton_bins = [b for b in carton_bins if b[1] > b[0] and b[3] > b[2]]
        carton_bins.sort(key=lambda b: -(b[1] - b[0]) * (b[3] - b[2]))
    else:
        carton_bins.append((c, usable_length, 0, usable_width))

    if cartons:
        bin_packers = {
            (x0, y0): MaxRectsBssf(x1 - x0, y1 - y0, rot=False)
            for x0, x1, y0, y1 in carton_bins
        }
        # 按 SKU 轮转铺底：每轮每个 SKU 放 1 件，保证各 SKU 都公平获得
        # 第 1 层位置（避免体积大的 SKU 独占底面、其余 SKU 一件不装）。
        # 必装/先卸后装优先：SKU 顺序按必装、卸货顺序降序、重量降序。
        sku_groups: dict[str, list[StackUnit]] = {}
        for stackable in cartons:
            sku_groups.setdefault(stackable.cargo.id, []).append(stackable)
        sku_order = sorted(
            sku_groups,
            key=lambda sid: (
                -int(any(u.required for u in sku_groups[sid])),
                -sku_groups[sid][0].cargo.unload_order,
                -sku_groups[sid][0].cargo.weight_g,
                sid,
            ),
        )
        # 按 SKU 顺序（必装/先卸后装/重量）逐个拆单件铺第 1 层：
        # 后卸的 SKU 先铺柜头（先卸后装分区），放不下的件随后叠高
        for stackable in cartons:
            sku_id = stackable.cargo.id
            for _ in range(stackable.count):
                single = replace(
                    stackable,
                    count=1,
                    stack_height_mm=stackable.item_height_mm,
                    total_weight_g=stackable.cargo.weight_g,
                )
                for x0, x1, y0, y1 in carton_bins:
                    rect, placed_unit = _try_add_to_pallet_top(
                        bin_packers[(x0, y0)], single, request
                    )
                    if rect is not None:
                        slot_units.setdefault(sku_id, []).append(placed_unit)
                        slot_rects.setdefault(sku_id, []).append(
                            (x0 + int(rect.x), c + y0 + int(rect.y))
                        )
                        break  # 已放入某个 bin
                # 所有 bin 都放不下 → 该件随后叠高（柱高分配覆盖）

    # 3) 柱高分配：每 SKU 的 n 件分到第 1 层 k 个位置（每位置 ≤ 栈容量），
    #    同 footprint 垂直叠（100% 完整支撑）。按 cargo_id 聚合处理，
    #    避免同 SKU 多个栈共享 slot 导致重复放置。
    by_sku: dict[str, list[StackUnit]] = {}
    for stackable in stackables:
        by_sku.setdefault(stackable.cargo.id, []).append(stackable)
    for sku_id, sku_stackables in by_sku.items():
        units_k = slot_units.get(sku_id, [])
        if not units_k:
            continue  # 该 SKU 无第 1 层位置（底面放不下）→ 无法叠（无支撑）→ 不装
        total = sum(stackable.count for stackable in sku_stackables)
        capacity = max(stackable.count for stackable in sku_stackables)
        k = len(units_k)
        base = min(total // k, capacity)
        rem = total % k
        # 顶层集中中间：多余件优先加在距柜长中心近的位置（rem 个 +1），
        # 使最顶层只出现在中间柱子（两头低中间高，重心居中）；
        # 每位置最多 capacity 件，超 capacity 的部分截断（少装并披露）。
        slot_rects_sku = slot_rects[sku_id]
        center = usable_length / 2
        order = sorted(
            range(k),
            key=lambda i: abs(
                (c + slot_rects_sku[i][0] + units_k[i].length_mm / 2) - center
            ),
        )
        heights = [min(capacity, base) for _ in range(k)]
        for i in order[:rem]:
            if heights[i] < capacity:
                heights[i] += 1
        first = min(stackable.first_instance_index for stackable in sku_stackables)
        for unit, (rx, ry), height in zip(units_k, slot_rects[sku_id], heights):
            placed_unit = replace(
                unit,
                count=height,
                stack_height_mm=height * unit.item_height_mm,
                total_weight_g=height * unit.cargo.weight_g,
                first_instance_index=first,
            )
            first += height
            placed.append(
                PackedStack(
                    unit=placed_unit,
                    x_mm=rx,
                    y_mm=ry,
                    step=1,
                )
            )

    placed.sort(key=lambda stack: (stack.y_mm, stack.x_mm))
    return placed


def _interstack_layout(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    base_stacks: list[PackedStack],
    coverage_min: float = 0.7,
    overhang_ratio_max: float = 0.2,
) -> list[PackedStack] | None:
    """互叠高装载布局：在基础布局之上，把未装入的件叠放到组合支撑平面上。

    第 1 阶段：复用 ``base_stacks``（通常是"装得多"的完整支撑布局，装载率高）。
    第 2 阶段：units 中未被基础布局覆盖的件，贪心叠放到任意已有件顶面
    （支撑覆盖率 ≥ ``coverage_min``、每边悬挑 ≤ ``overhang_ratio_max × 短边``），
    允许跨 SKU 落在组合平面上。只叠在单层底层件顶面（最多两层，不超
    max_layers/柜高）；放不下的保持未装（少装披露）。

    与严格布局的区别：不要求"同 SKU 同 footprint 正上方"，允许跨 SKU
    落在组合平面上 —— 校验器以宽松的覆盖率阈值放行。装载率 ≥ 基础布局。
    """
    if not units or not base_stacks:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    available_height = request.container.inner_height_mm - 2 * c

    # 第 1 阶段：基础布局
    placed = list(base_stacks)

    # 未装件：units 中未被 base_stacks 覆盖的（按 instance 判定）
    placed_instances: set[tuple[str, int]] = set()
    for stack in base_stacks:
        unit = stack.unit
        if isinstance(unit, CompositeUnit):
            for i in range(unit.pallet.count):
                placed_instances.add((unit.pallet.cargo.id, unit.pallet.first_instance_index + i))
            for on_top, _, _ in unit.on_top:
                for i in range(on_top.count):
                    placed_instances.add((on_top.cargo.id, on_top.first_instance_index + i))
            continue
        for i in range(unit.count):
            placed_instances.add((unit.cargo.id, unit.first_instance_index + i))
    remaining: list[StackUnit | CompositeUnit] = []
    for unit in units:
        if isinstance(unit, CompositeUnit):
            count = unit.pallet.count
            base_id = unit.pallet.first_instance_index
        else:
            count = unit.count
            base_id = unit.first_instance_index
        missing = [
            base_id + i for i in range(count)
            if (unit.cargo.id, base_id + i) not in placed_instances
        ]
        if not missing:
            continue
        if isinstance(unit, CompositeUnit):
            # CompositeUnit 整体已判定，缺失则不处理（少装披露）
            continue
        # 互叠阶段拆成 count=1 单件：每个缺失件单独叠放到组合平面上，
        # 不能把多件合成一个栈（那样会叠更高、超柜高/层数限制）。
        for instance in missing:
            remaining.append(
                replace(
                    unit,
                    count=1,
                    stack_height_mm=unit.item_height_mm,
                    total_weight_g=unit.cargo.weight_g,
                    first_instance_index=instance,
                )
            )
    if not remaining:
        return placed

    # 第 2 阶段：跨 SKU 互叠。
    remaining.sort(key=lambda u: (-u.volume_mm3, -u.length_mm * u.width_mm, u.id))
    # 性能保护：剩余栈过多（说明物理装不下，而非布局问题）时只尝试
    # 前 N 个最大栈，避免大订单 O(n³) 超时。
    REMAINING_CAP = 60
    remaining = remaining[:REMAINING_CAP]
    if max(unit.item_height_mm for unit in remaining) >= available_height:
        return placed  # 没有可用叠放高度，直接返回第 1 阶段

    # placed 按 x 排序，供支撑查询的滑动窗口剪枝
    placed_by_x = sorted(placed, key=lambda s: s.x_mm)

    def stack_top(stack: PackedStack) -> int:
        """栈顶面绝对高度（相对 clearance 的偏移 + 栈高）。"""
        unit = stack.unit
        if isinstance(unit, CompositeUnit):
            height = unit.pallet.stack_height_mm
        else:
            height = unit.stack_height_mm
        return stack.z_mm + height

    def supporters_under(
        x: int, y: int, length: int, width: int,
    ) -> tuple[int, list[PackedStack]]:
        """返回 (放置高度 z, 支撑件列表)。

        只检查底面与 (x,y,length,width) 有交集的栈（x 滑动窗口剪枝）。
        """
        candidates: list[PackedStack] = []
        x_end = x + length
        for stack in placed_by_x:
            if stack.x_mm >= x_end:
                break
            if stack.x_mm + stack.length_mm <= x:
                continue
            if y < stack.y_mm + stack.width_mm and y + width > stack.y_mm:
                candidates.append(stack)
        if not candidates:
            return 0, []
        z = max(stack_top(stack) for stack in candidates)
        supporters = [
            stack for stack in candidates
            if stack_top(stack) == z
        ]
        return z, supporters

    def coverage_area(x: int, y: int, length: int, width: int, supporters: list[PackedStack]) -> int:
        """该件底面被支撑栈覆盖的总面积（各支撑栈交集面积之和）。"""
        total = 0
        for support in supporters:
            ox = max(
                0,
                min(x + length, support.x_mm + support.length_mm) - max(x, support.x_mm),
            )
            oy = max(
                0,
                min(y + width, support.y_mm + support.width_mm) - max(y, support.y_mm),
            )
            total += ox * oy
        return total

    def intersects(a: PackedStack, b: PackedStack) -> bool:
        return (
            a.x_mm < b.x_mm + b.length_mm
            and a.x_mm + a.length_mm > b.x_mm
            and a.y_mm < b.y_mm + b.width_mm
            and a.y_mm + a.width_mm > b.y_mm
            and a.z_mm < stack_top(b)
            and stack_top(a) > b.z_mm
        )

    for unit in remaining:
        u_length = unit.length_mm
        u_width = unit.width_mm
        u_height = unit.item_height_mm
        if u_height >= available_height:
            continue
        best: tuple[int, int, int] | None = None  # (x, y, z)
        best_score: tuple[float, float, int] | None = None
        # 候选位置：每个已放置的**底层**（z_mm==0）栈顶面矩形内，与该栈对齐
        # 的落位点（含跨栈组合平面的对齐点）。只叠在底层件顶面（最多两层），
        # 保证不超 max_layers、不超出柜高。只取"支撑件顶面 == 放置高度"的
        # 位置，覆盖组合平面互叠所需的所有可行边界。
        for base in placed_by_x:
            if base.z_mm != 0:
                continue  # 只在底层件顶面互叠（最多两层，不超层数限制）
            # 底层件必须是单件（展开后只占 1 层），否则其顶面已是第 2 层，
            # 再叠会超 max_layers。CompositeUnit 若含上托散箱也不在此互叠。
            if isinstance(base.unit, CompositeUnit):
                continue
            if base.unit.count != 1:
                continue
            # 底层件必须可叠（stackable）且不脆弱；互叠件自身也须可叠
            if not base.unit.cargo.stackable or base.unit.cargo.fragile:
                continue
            if not unit.cargo.stackable or unit.cargo.fragile:
                continue
            # 底部承重：底层件 max_top_load_g 必须足以承受互叠件的重量
            if base.unit.cargo.max_top_load_g < unit.total_weight_g:
                continue
            # 以 base 顶面为放置平面
            base_z = stack_top(base)
            if base_z + u_height > available_height:
                continue
            # 候选 (x, y)：base 矩形内与上层件对齐的角落（贴合 base 边缘）
            for x in (base.x_mm - c, base.x_mm - c + base.length_mm - u_length):
                if x < 0 or x + u_length > usable_length:
                    continue
                for y in (base.y_mm - c, base.y_mm - c + base.width_mm - u_width):
                    if y < 0 or y + u_width > usable_width:
                        continue
                    z, supporters = supporters_under(x, y, u_length, u_width)
                    if z != base_z:
                        continue  # 必须以该 base 顶面为放置面
                    if not supporters:
                        continue
                    support_area = coverage_area(x, y, u_length, u_width, supporters)
                    coverage = support_area / (u_length * u_width)
                    if coverage < coverage_min:
                        continue
                    # 悬挑：底面相对支撑件并集外接矩形
                    support_x0 = min((s.x_mm for s in supporters), default=x)
                    support_y0 = min((s.y_mm for s in supporters), default=y)
                    support_x1 = max(
                        (s.x_mm + s.length_mm for s in supporters),
                        default=x + u_length,
                    )
                    support_y1 = max(
                        (s.y_mm + s.width_mm for s in supporters),
                        default=y + u_width,
                    )
                    short_side = min(u_length, u_width)
                    overhang = max(
                        support_x0 - x,
                        x + u_length - support_x1,
                        support_y0 - y,
                        y + u_width - support_y1,
                    )
                    if overhang > short_side * overhang_ratio_max:
                        continue
                    candidate = PackedStack(
                        unit=unit,
                        x_mm=x,
                        y_mm=y,
                        z_mm=z,
                        step=2,
                    )
                    if any(intersects(candidate, s) for s in placed):
                        continue
                    score = (coverage, -overhang, x)
                    if best_score is None or score > best_score:
                        best_score = score
                        best = (x, y, z)
        if best is None:
            continue
        x, y, z = best
        placed.append(
            PackedStack(
                unit=unit,
                x_mm=x,
                y_mm=y,
                z_mm=z,
                step=2,
            )
        )
        placed_by_x = sorted(placed, key=lambda s: s.x_mm)

    placed.sort(key=lambda stack: (stack.y_mm, stack.x_mm, stack.z_mm))
    return placed


def _rects_overlap_x(
    stack: PackedStack,
    x: int,
    length: int,
) -> bool:
    return x < stack.x_mm + stack.length_mm and x + length > stack.x_mm


def _rects_overlap_y(
    stack: PackedStack,
    y: int,
    width: int,
) -> bool:
    return y < stack.y_mm + stack.width_mm and y + width > stack.y_mm


def _expand_stacks(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: str,
) -> list[Placement]:
    explicit_steps = {
        stack.unit.id: stack.step
        for stack in stacks
        if stack.step is not None
    }
    ordered = sorted(
        stacks,
        key=(
            (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.cargo.id))
            if profile == "easy"
            else (lambda stack: (stack.x_mm, stack.y_mm, stack.unit.id))
        ),
    )
    if len(explicit_steps) == len(stacks):
        step_by_id = explicit_steps
    elif profile == "easy":
        cargo_order = list(dict.fromkeys(stack.unit.cargo.id for stack in ordered))
        step_by_cargo = {cargo_id: index + 1 for index, cargo_id in enumerate(cargo_order)}
        step_by_id = {stack.unit.id: step_by_cargo[stack.unit.cargo.id] for stack in ordered}
    else:
        step_by_id = {stack.unit.id: index + 1 for index, stack in enumerate(ordered)}
    placements: list[Placement] = []
    for stack in stacks:
        unit = stack.unit
        # 互叠布局：列起点 z 偏移 stack.z_mm（相对 clearance）
        base_z = request.container.clearance_mm + stack.z_mm
        step = step_by_id[unit.id]
        if isinstance(unit, CompositeUnit):
            pallet = unit.pallet
            for offset in range(pallet.count):
                placements.append(
                    Placement(
                        id=f"{pallet.cargo.id}-{pallet.first_instance_index + offset}",
                        cargo_id=pallet.cargo.id,
                        instance_index=pallet.first_instance_index + offset,
                        x_mm=stack.x_mm,
                        y_mm=stack.y_mm,
                        z_mm=base_z + offset * pallet.item_height_mm,
                        length_mm=stack.length_mm,
                        width_mm=stack.width_mm,
                        height_mm=pallet.item_height_mm,
                        rotation=stack.orientation,
                        weight_g=pallet.cargo.weight_g,
                        step=step,
                    )
                )
            top_z = base_z + pallet.stack_height_mm
            for on_top, offset_x, offset_y in unit.on_top:
                for offset in range(on_top.count):
                    placements.append(
                        Placement(
                            id=f"{on_top.cargo.id}-{on_top.first_instance_index + offset}",
                            cargo_id=on_top.cargo.id,
                            instance_index=on_top.first_instance_index + offset,
                            x_mm=stack.x_mm + offset_x,
                            y_mm=stack.y_mm + offset_y,
                            z_mm=top_z + offset * on_top.item_height_mm,
                            length_mm=on_top.length_mm,
                            width_mm=on_top.width_mm,
                            height_mm=on_top.item_height_mm,
                            rotation=on_top.orientation,
                            weight_g=on_top.cargo.weight_g,
                            step=step,
                        )
                    )
            continue
        for offset in range(unit.count):
            instance_index = unit.first_instance_index + offset
            placements.append(
                Placement(
                    id=f"{unit.cargo.id}-{instance_index}",
                    cargo_id=unit.cargo.id,
                    instance_index=instance_index,
                    x_mm=stack.x_mm,
                    y_mm=stack.y_mm,
                    z_mm=base_z + offset * unit.item_height_mm,
                    length_mm=stack.length_mm,
                    width_mm=stack.width_mm,
                    height_mm=unit.item_height_mm,
                    rotation=stack.orientation,
                    weight_g=unit.cargo.weight_g,
                    step=step,
                )
            )
    placements.sort(key=lambda item: (item.step, item.z_mm, item.id))
    return placements


def _band_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Full-width contiguous band per SKU, one loading step per band."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c
    by_sku: dict[str, list[StackUnit]] = defaultdict(list)
    for unit in units:
        by_sku[unit.cargo.id].append(unit)

    placed: list[PackedStack] = []
    cursor = 0
    band_index = 0
    for cargo_id in (item.id for item in request.cargo_items):
        group = by_sku.get(cargo_id)
        if not group:
            continue
        base_length = group[0].length_mm
        base_width = group[0].width_mm
        variants: list[tuple[int, int, bool]] = [(base_length, base_width, False)]
        swapped_orientation = SWAP_ORIENTATIONS.get(group[0].orientation)
        # CompositeUnit（托盘+上托散箱）不旋转：on_top 偏移基于未旋转托盘顶面
        if not isinstance(group[0], CompositeUnit) and swapped_orientation in group[0].cargo.allowed_orientations:
            swapped = (base_width, base_length)
            if swapped[1] <= door_usable_width:
                variants.append((swapped[0], swapped[1], True))
        best: tuple[int, int, int, int, bool] | None = None
        for along_length, across_width, rotated in variants:
            if across_width + gap > usable_width:
                continue
            columns = usable_width // (across_width + gap)
            if columns < 1:
                continue
            rows = (len(group) + columns - 1) // columns
            depth = rows * along_length + (rows - 1) * gap
            if best is None or (depth, -columns) < (best[0], best[1]):
                best = (depth, columns, along_length, across_width, rotated)
        if best is None:
            return None
        depth, columns, along_length, across_width, rotated = best
        if cursor + depth > usable_length:
            return None
        x0 = c + cursor
        for index, unit in enumerate(group):
            row = index // columns
            column = index % columns
            placed.append(
                PackedStack(
                    unit=unit,
                    x_mm=x0 + row * (along_length + gap),
                    y_mm=c + column * (across_width + gap),
                    rotated=rotated,
                    step=band_index + 1,
                )
            )
        cursor += depth + gap
        band_index += 1
    return placed


def _shelf_layout(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    """Full-width shelves with contiguous same-SKU blocks, one step per shelf."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    remaining = sorted(
        units,
        key=lambda unit: (-unit.length_mm, -unit.volume_mm3, unit.id),
    )
    sku_order = {
        cargo_id: index
        for index, cargo_id in enumerate(item.id for item in request.cargo_items)
    }
    placed: list[PackedStack] = []
    cursor = 0
    shelf_index = 0
    while remaining:
        shelf_depth = remaining[0].length_mm
        if cursor + shelf_depth > usable_length:
            return None
        shelf_units: list[StackUnit] = []
        width_left = usable_width
        index = 0
        while index < len(remaining):
            unit = remaining[index]
            if unit.length_mm <= shelf_depth and unit.width_mm + gap <= width_left:
                shelf_units.append(unit)
                width_left -= unit.width_mm + gap
                del remaining[index]
            else:
                index += 1
        if not shelf_units:
            return None
        shelf_units.sort(
            key=lambda unit: (sku_order.get(unit.cargo.id, 10**9), unit.id)
        )
        y = c
        for unit in shelf_units:
            placed.append(
                PackedStack(
                    unit=unit,
                    x_mm=c + cursor,
                    y_mm=y,
                    step=shelf_index + 1,
                )
            )
            y += unit.width_mm + gap
        cursor += shelf_depth + gap
        shelf_index += 1
    return placed


def _try_region_layouts(
    request: PackRequest,
    units: list[StackUnit],
) -> list[PackedStack] | None:
    bands = _band_layout(request, units)
    if bands is not None:
        return bands
    return _shelf_layout(request, units)


EASY_MAX_DROP_RATIO = 0.05


def _easy_drop_limit(reference_count: int) -> int:
    return max(1, round(reference_count * EASY_MAX_DROP_RATIO))


def _trim_optional_piece(units: list[StackUnit | CompositeUnit]) -> list[StackUnit | CompositeUnit] | None:
    """Remove one optional carton piece without deleting a required stack."""
    candidates = sorted(
        (
            unit
            for unit in units
            if not unit.required and isinstance(unit, StackUnit)
        ),
        key=lambda unit: (unit.volume_mm3, unit.cargo.id, unit.id),
    )
    if not candidates:
        return None
    target = candidates[0]
    trimmed: list[StackUnit | CompositeUnit] = []
    for unit in units:
        if unit.id != target.id:
            trimmed.append(unit)
            continue
        if unit.count > 1:
            trimmed.append(replace(
                unit,
                count=unit.count - 1,
                stack_height_mm=(unit.count - 1) * unit.item_height_mm,
                total_weight_g=(unit.count - 1) * unit.cargo.weight_g,
            ))
    return trimmed


def _easy_compact_score(
    request: PackRequest,
    stacks: list[PackedStack],
) -> tuple[float, ...]:
    placements = _expand_stacks(request, stacks, "easy")
    quality = _layout_quality(request, placements)
    metrics = _metrics(request, placements)
    return (
        -quality.floor_bbox_void_pct,
        -quality.floor_largest_gap_mm,
        -quality.floor_internal_gap_mm,
        -quality.floor_largest_transverse_gap_mm,
        -quality.floor_x_components,
        -metrics.cargo_zones,
        -metrics.loading_steps,
        -quality.sku_transitions,
    )


def _easy_max_zones(request: PackRequest) -> int | None:
    hint = request.ai_layout_hint
    value = hint.get("max_zones") if isinstance(hint, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _easy_pallets_satisfied(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
    candidate: list[PackedStack],
) -> bool:
    reference = Counter()
    for unit in units:
        if isinstance(unit, CompositeUnit):
            reference[unit.pallet.cargo.id] += unit.pallet.count
        elif unit.cargo.kind == "pallet":
            reference[unit.cargo.id] += unit.count
    if not reference:
        return True
    loaded = Counter(item.cargo_id for item in _expand_stacks(request, candidate, "easy"))
    return all(loaded[cargo_id] >= count for cargo_id, count in reference.items())


def _easy_region_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
    reference_count: int | None = None,
) -> list[PackedStack] | None:
    """Compare full and bounded optional-piece removals for a compact layout."""
    if not units:
        return None
    if reference_count is None:
        reference_count = sum(unit.count for unit in units)
    if reference_count > 160:
        candidate = _layer_layout(request, units, allow_partial=True)
        if candidate is None or not _easy_pallets_satisfied(request, units, candidate):
            return None
        loaded_count = len(_expand_stacks(request, candidate, "easy"))
        return candidate if loaded_count <= reference_count else None
    drop_limit = _easy_drop_limit(reference_count)
    candidates: list[tuple[int, list[PackedStack]]] = []
    current_units = list(units)
    for drop_count in range(drop_limit + 1):
        layouts = []
        if all(unit.count == 1 and unit.cargo.kind == "pallet" for unit in current_units):
            layouts.append(_pallet_grid_layout(request, current_units))
        layouts.extend((
            _try_region_layouts(request, current_units),
            _layer_layout(request, current_units, allow_partial=True),
        ))
        for candidate in layouts:
            if (
                candidate is None
                or not _required_satisfied(request, candidate)
                or not _easy_pallets_satisfied(request, units, candidate)
            ):
                continue
            loaded_count = len(_expand_stacks(request, candidate, "easy"))
            actual_drop = reference_count - loaded_count
            if 0 <= actual_drop <= drop_limit:
                candidates.append((actual_drop, candidate))
        if drop_count == drop_limit:
            break
        current_units = _trim_optional_piece(current_units) or []
        if not current_units:
            break
    if not candidates:
        return None
    full_candidates = [candidate for drop, candidate in candidates if drop == 0]
    baseline = max(full_candidates, key=lambda candidate: _easy_compact_score(request, candidate), default=None)
    max_zones = _easy_max_zones(request)
    if baseline is not None and max_zones is None:
        return baseline
    if baseline is not None and max_zones is not None:
        baseline_zones = _metrics(request, _expand_stacks(request, baseline, "easy")).cargo_zones
        goal_candidates = [
            candidate
            for drop, candidate in candidates
            if drop > 0
            and _metrics(request, _expand_stacks(request, candidate, "easy")).cargo_zones <= max_zones
        ]
        if baseline_zones > max_zones and goal_candidates:
            return max(goal_candidates, key=lambda candidate: _easy_compact_score(request, candidate))
    best_drop, best = max(
        candidates,
        key=lambda item: (_easy_compact_score(request, item[1]), -item[0]),
    )
    if baseline is not None and best_drop > 0:
        if _easy_compact_score(request, best) <= _easy_compact_score(request, baseline):
            return baseline
    return best


def _rects_connected(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    tolerance: int,
) -> bool:
    first_x1, first_y1, first_length, first_width = first
    second_x1, second_y1, second_length, second_width = second
    horizontal_gap = max(
        0,
        first_x1 - (second_x1 + second_length),
        second_x1 - (first_x1 + first_length),
    )
    vertical_gap = max(
        0,
        first_y1 - (second_y1 + second_width),
        second_y1 - (first_y1 + first_width),
    )
    return horizontal_gap <= tolerance and vertical_gap <= tolerance


def _merge_zone_rects(
    rects: list[tuple[int, int, int, int, int]],
    tolerance: int,
) -> list[tuple[int, int, int, int, int]]:
    parent = list(range(len(rects)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for first in range(len(rects)):
        for second in range(first + 1, len(rects)):
            if _rects_connected(
                rects[first][:4],
                rects[second][:4],
                tolerance,
            ):
                root_first = find(first)
                root_second = find(second)
                if root_first != root_second:
                    parent[root_second] = root_first
    merged: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rects)):
        merged[find(index)].append(index)
    zones: list[tuple[int, int, int, int, int]] = []
    for indexes in merged.values():
        min_x = min(rects[index][0] for index in indexes)
        min_y = min(rects[index][1] for index in indexes)
        max_x = max(rects[index][0] + rects[index][2] for index in indexes)
        max_y = max(rects[index][1] + rects[index][3] for index in indexes)
        piece_count = sum(rects[index][4] for index in indexes)
        zones.append((min_x, min_y, max_x - min_x, max_y - min_y, piece_count))
    return zones


def _compute_zones(
    request: PackRequest,
    placements: list[Placement],
) -> list[Zone]:
    tolerance = request.item_gap_mm + 1
    column_counts = Counter(
        (placement.cargo_id, placement.x_mm, placement.y_mm)
        for placement in placements
    )
    groups: dict[tuple[str, int], list[tuple[int, int, int, int, int]]] = defaultdict(list)
    seen: set[tuple] = set()
    for placement in placements:
        rect = (
            placement.x_mm,
            placement.y_mm,
            placement.length_mm,
            placement.width_mm,
        )
        key = (placement.cargo_id, placement.step)
        if (key, rect) in seen:
            continue
        seen.add((key, rect))
        groups[key].append(
            (
                *rect,
                column_counts[(placement.cargo_id, placement.x_mm, placement.y_mm)],
            )
        )
    zones: list[Zone] = []
    for (cargo_id, step), rects in groups.items():
        for merged in _merge_zone_rects(rects, tolerance):
            zones.append(
                Zone(
                    step=step,
                    cargo_id=cargo_id,
                    x_mm=merged[0],
                    y_mm=merged[1],
                    length_mm=merged[2],
                    width_mm=merged[3],
                    piece_count=merged[4],
                )
            )
    zones.sort(key=lambda zone: (zone.step, zone.cargo_id, zone.x_mm, zone.y_mm))
    return zones


def _metrics(request: PackRequest, placements: list[Placement]) -> SolutionMetrics:
    cargo_by_id = {item.id: item for item in request.cargo_items}
    loaded_weight = sum(cargo_by_id[item.cargo_id].weight_g for item in placements)
    loaded_volume = sum(
        item.length_mm * item.width_mm * item.height_mm for item in placements
    )
    container_volume = (
        request.container.inner_length_mm
        * request.container.inner_width_mm
        * request.container.inner_height_mm
    )
    if loaded_weight:
        cg_x = sum(
            (item.x_mm + item.length_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
        cg_y = sum(
            (item.y_mm + item.width_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
        cg_z = sum(
            (item.z_mm + item.height_mm / 2)
            * cargo_by_id[item.cargo_id].weight_g
            for item in placements
        ) / loaded_weight
    else:
        cg_x = cg_y = cg_z = 0.0
    x_deviation = abs(cg_x - request.container.inner_length_mm / 2) / (
        request.container.inner_length_mm / 2
    )
    y_deviation = abs(cg_y - request.container.inner_width_mm / 2) / (
        request.container.inner_width_mm / 2
    )
    zones = len({(item.cargo_id, item.step) for item in placements})
    quality = _layout_quality(request, placements)
    return SolutionMetrics(
        loaded_pieces=len(placements),
        loaded_weight_g=loaded_weight,
        volume_utilization_pct=round(loaded_volume / container_volume * 100, 2),
        weight_utilization_pct=round(
            loaded_weight / request.container.max_payload_g * 100,
            2,
        ),
        center_of_gravity=CenterOfGravity(
            x_mm=round(cg_x, 1),
            y_mm=round(cg_y, 1),
            z_mm=round(cg_z, 1),
        ),
        length_imbalance_pct=round(x_deviation * 100, 2),
        width_imbalance_pct=round(y_deviation * 100, 2),
        weight_imbalance_pct=round(max(x_deviation, y_deviation) * 100, 2),
        loading_steps=len({item.step for item in placements}),
        cargo_zones=zones,
        floor_internal_gap_mm=quality.floor_internal_gap_mm,
        floor_largest_gap_mm=quality.floor_largest_gap_mm,
        floor_bbox_void_pct=quality.floor_bbox_void_pct,
        floor_largest_transverse_gap_mm=quality.floor_largest_transverse_gap_mm,
    )


def _raise_for_invalid_layout(validation: ValidationResult) -> None:
    """布局校验失败时抛出可读错误：错误码全部可解释 → 422（含中文调整建议），
    否则视为内部缺陷抛 INTERNAL_INVALID_LAYOUT（500 兜底）。"""
    codes_list = sorted({issue.code for issue in validation.errors})
    known_codes = [code for code in codes_list if code in LAYOUT_ADVICE]
    if len(known_codes) == len(codes_list) and codes_list:
        advice = "；".join(LAYOUT_ADVICE[code] for code in codes_list)
        raise PackingFailure(
            "LAYOUT_NOT_FEASIBLE",
            "当前货物参数无法生成有效的装柜方案，请根据下方提示调整后重试",
            hint=advice,
        )
    raise PackingFailure("INTERNAL_INVALID_LAYOUT", f"候选布局校验失败：{', '.join(codes_list)}")


def _build_solution(
    request: PackRequest,
    stacks: list[PackedStack],
    profile: Literal["high_fill", "stable", "easy"],
    support_coverage_min: float = 1.0,
    overhang_ratio_max: float = 0.0,
) -> PackingSolution:
    placements = _expand_stacks(request, stacks, profile)
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=request.item_gap_mm,
        support_coverage_min=support_coverage_min,
        overhang_ratio_max=overhang_ratio_max,
    )
    if not validation.valid:
        _raise_for_invalid_layout(validation)
    loaded = Counter(item.cargo_id for item in placements)
    loaded_counts = {item.id: loaded[item.id] for item in request.cargo_items}
    unloaded_counts = {
        item.id: item.quantity - loaded[item.id] for item in request.cargo_items
    }
    metrics = _metrics(request, placements)
    zones = _compute_zones(request, placements)
    names = {
        "high_fill": "装载率优先",
        "stable": "重心稳妥",
        "easy": "易操作",
    }
    warnings = []
    if request.door_buffer_mm > 0:
        warnings.append(f"柜门预留操作空间 {request.door_buffer_mm}mm")
        max_end = max(
            (
                placement.x_mm + placement.length_mm
                for placement in placements
            ),
            default=request.container.clearance_mm,
        )
        actual_door_reserve = request.container.inner_length_mm - max_end
        if actual_door_reserve < request.door_buffer_mm:
            warnings.append(
                f"当前排组实际柜门预留 {actual_door_reserve}mm，"
                f"较目标少 {request.door_buffer_mm - actual_door_reserve}mm，"
                "请现场复核"
            )
    unloaded_total = sum(unloaded_counts.values())
    if unloaded_total:
        warnings.append(f"仍有 {sum(unloaded_counts.values())} 件货物未装入本柜")
        requested_weight = sum(
            item.quantity * item.weight_g for item in request.cargo_items
        )
        remaining_payload = max(0, request.container.max_payload_g - metrics.loaded_weight_g)
        if requested_weight > request.container.max_payload_g:
            overload = requested_weight - request.container.max_payload_g
            warnings.append(
                f"订单总重 {requested_weight / 1_000_000:.2f}t，超过柜体最大载重 "
                f"{request.container.max_payload_g / 1_000_000:.2f}t（超出 "
                f"{overload / 1_000_000:.2f}t）；即使柜内空间足够，也无法全部装入一柜，"
                "请分柜或改用更高载重柜型"
            )
        if remaining_payload > 0:
            warnings.append(
                f"当前方案仍剩载重 {remaining_payload / 1_000_000:.2f}t；未装货物并非仅因总载重已满，"
                "还受柜内可用底面、允许朝向、同规格支撑、层数或柜门操作空间等安全约束限制"
            )
    quality = _layout_quality(request, placements)
    component_count = quality.upper_components
    upper_quality_ok = _upper_layout_quality_ok(request, placements)
    if component_count > 1:
        warnings.append(f"上层货物被拆成 {component_count} 个区域")
    if quality.upper_isolated_count:
        warnings.append(
            f"上层存在 {quality.upper_isolated_count} 件几何孤立货物，"
            "现场应复核支撑与装卸顺序"
        )
    if not upper_quality_ok:
        warnings.append(SOFT_LAYOUT_FALLBACK_WARNING)
        if component_count > 1:
            warnings.append("上层未形成单一中部连续区域，请现场复核")
        elif quality.upper_count:
            warnings.append(
                f"上层未充分集中在中部（中心偏差 "
                f"{quality.upper_center_deviation_mm:.0f}mm），"
                "请现场复核"
            )
    if quality.floor_count and (
        quality.floor_x_components > 1
        or quality.floor_coverage_pct < 95
        or quality.floor_count < quality.upper_count
    ):
        warnings.append(
            f"底层连续铺满程度不足（连续区 {quality.floor_x_components} 个，"
            f"覆盖率 {quality.floor_coverage_pct:.1f}%），"
            "当前为物理安全的次优方案，请现场复核"
        )
    if profile == "high_fill":
        pros = [
            f"装入 {metrics.loaded_pieces} 件，体积利用率 {metrics.volume_utilization_pct}%",
            f"重量利用率 {metrics.weight_utilization_pct}%",
        ]
        cons = [f"重心最大偏差 {metrics.weight_imbalance_pct}%"]
    elif profile == "stable":
        pros = [
            f"前后重心偏差 {metrics.length_imbalance_pct}%（左右 {metrics.width_imbalance_pct}%）",
            f"装入数量保持为 {metrics.loaded_pieces} 件",
        ]
        if metrics.length_imbalance_pct > 10:
            warnings.append(
                f"前后重量偏差仍较大（{metrics.length_imbalance_pct}%），建议现场复核重货位置"
            )
        cons = [f"需要按 {metrics.loading_steps} 个区域执行装载"]
    else:
        pros = [
            f"同类货物按区域集中，共 {metrics.cargo_zones} 个货区",
            f"装载顺序拆分为 {metrics.loading_steps} 步",
        ]
        cons = [f"当前体积利用率为 {metrics.volume_utilization_pct}%"]
    return PackingSolution(
        profile=profile,
        name=names[profile],
        placements=placements,
        loaded_counts=loaded_counts,
        unloaded_counts=unloaded_counts,
        metrics=metrics,
        zones=zones,
        pros=pros,
        cons=cons,
        warnings=warnings,
    )


def _layout_signature(solution: PackingSolution) -> tuple:
    return tuple(
        sorted(
            (
                item.cargo_id,
                item.instance_index,
                item.x_mm,
                item.y_mm,
                item.z_mm,
                item.rotation.value,
            )
            for item in solution.placements
        )
    )


def _validated_candidate(
    request: PackRequest,
    stacks: list[PackedStack] | None,
    profile: str,
    expected_counts: Counter[str] | None = None,
) -> list[PackedStack] | None:
    """Accept only physically valid candidates before result construction."""
    if not stacks:
        return None
    placements = _expand_stacks(request, stacks, profile)
    if not _required_satisfied(request, stacks):
        return None
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=request.item_gap_mm,
    )
    if not validation.valid:
        return None
    if expected_counts is not None:
        actual_counts = Counter(placement.cargo_id for placement in placements)
        if actual_counts != expected_counts:
            return None
    return stacks


def _validated_easy_candidate(
    request: PackRequest,
    stacks: list[PackedStack] | None,
    max_pieces: int,
) -> list[PackedStack] | None:
    candidate = _validated_candidate(request, stacks, "easy")
    if candidate is None:
        return None
    return (
        candidate
        if len(_expand_stacks(request, candidate, "easy")) <= max_pieces
        else None
    )


def pack_order(request: PackRequest) -> PackResponse:
    high_request = _request_for_profile(request, "high_fill")
    stable_request = _request_for_profile(request, "stable")
    easy_request = _request_for_profile(request, "easy")
    units = _build_stack_units(high_request)
    if not units:
        solutions = [
            _build_solution(request, [], "high_fill"),
            _build_solution(request, [], "stable"),
            _build_solution(request, [], "easy"),
        ]
        request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
        request_id = hashlib.sha256(request_json.encode("utf-8")).hexdigest()[:12]
        return PackResponse(request_id=request_id, solutions=solutions)
    pure_pallet_skus = {unit.cargo.id for unit in units}
    pure_pallet_order = (
        bool(units)
        and len(pure_pallet_skus) <= 6
        and all(
            unit.cargo.kind == "pallet" and not isinstance(unit, CompositeUnit)
            for unit in units
        )
    )
    # 四方案统一优先尝试 SKU 块布局，失败走原回退链
    # 缺陷 B 修复：SKU 块布局改传 merged（散箱上托托盘顶面，轻在上）
    high_merged = _merge_pallet_cartons(high_request, units)
    high_blocks = (
        _pure_pallet_floor_first_layout(high_request, units, "fill")
        if pure_pallet_order
        else _sku_block_layout(high_request, high_merged, "fill")
    )
    high_stacks = _validated_candidate(request, high_blocks, "high_fill")
    if high_stacks is None:
        high_stacks = _validated_candidate(
            request,
            _sku_block_layout(high_request, high_merged, "fill"),
            "high_fill",
        )
    if high_stacks is None:
        high_stacks = _validated_candidate(
            request,
            _high_fill_candidate(high_request, units),
            "high_fill",
        )
    if high_stacks is None:
        raise PackingFailure(
            "LAYOUT_NOT_FEASIBLE",
            "当前柜型没有找到物理安全的装柜方案",
            hint="请减少货物数量或更换更大柜型",
        )
    bounded_high = _bounded_pallet_layout_candidate(
        high_request,
        high_stacks,
        Counter(item.cargo_id for item in _expand_stacks(request, high_stacks, "high_fill")),
        "high_fill",
    )
    if bounded_high is not None:
        high_stacks = bounded_high
    high_stacks = _prefer_compact_candidate(high_request, high_stacks, "high_fill")
    high_placements = _expand_stacks(request, high_stacks, "high_fill")
    selected_counts = Counter(item.cargo_id for item in high_placements)
    stable_units = _build_stack_units(stable_request, dict(selected_counts), "stable")
    merged_stable = _merge_pallet_cartons(stable_request, stable_units)
    stable_blocks = (
        _pure_pallet_floor_first_layout(stable_request, stable_units, "stable")
        if pure_pallet_order
        else _sku_block_layout(stable_request, merged_stable, "balance")
    )
    stable_stacks: list[PackedStack] | None = None
    if stable_blocks is not None:
        stable_candidate = _swap_balance(stable_request, stable_blocks)
        stable_placements = _expand_stacks(stable_request, stable_candidate, "stable")
        if any(
            placement.z_mm > request.container.clearance_mm
            for placement in stable_placements
        ):
            stable_stacks = _validated_candidate(
                stable_request,
                stable_candidate,
                "stable",
                selected_counts,
            )
    if stable_stacks is None:
        balanced = _sku_block_layout(stable_request, merged_stable, "balance")
        if balanced is not None:
            stable_stacks = _validated_candidate(
                stable_request,
                _swap_balance(stable_request, balanced),
                "stable",
                selected_counts,
            )
    if stable_stacks is None:
        pallet_grid = _pallet_grid_layout(stable_request, stable_units)
        stable_stacks = _validated_candidate(stable_request, pallet_grid, "stable", selected_counts)
    if stable_stacks is None:
        balanced_grid = _stable_balance_layout(stable_request, stable_units)
        stable_stacks = _validated_candidate(
            stable_request,
            balanced_grid,
            "stable",
            selected_counts,
        )
    if stable_stacks is None:
        mixed = _layer_layout(stable_request, merged_stable)
        stable_stacks = _validated_candidate(stable_request, mixed, "stable", selected_counts)
    if stable_stacks is None:
        repacked = _repack_same_units(stable_request, merged_stable, "stable")
        stable_stacks = _validated_candidate(
            stable_request,
            repacked,
            "stable",
            selected_counts,
        )
    if stable_stacks is None:
        stable_stacks = _validated_candidate(
            stable_request,
            _center_stacks(stable_request, high_stacks),
            "stable",
            selected_counts,
        )
    if stable_stacks is None:
        stable_stacks = high_stacks
    stable_stacks = _swap_balance(stable_request, stable_stacks)
    stable_stacks = _prefer_compact_candidate(stable_request, stable_stacks, "stable")
    bounded_stable = _bounded_pallet_layout_candidate(
        stable_request,
        stable_stacks,
        selected_counts,
        "stable",
    )
    if bounded_stable is not None:
        stable_stacks = bounded_stable
    easy_units = _build_stack_units(easy_request)
    easy_merged = _merge_pallet_cartons(easy_request, easy_units)
    easy_blocks = (
        _pure_pallet_floor_first_layout(easy_request, easy_units, "easy")
        if pure_pallet_order
        else _sku_block_layout(easy_request, easy_merged, "easy")
    )
    easy_stacks = _validated_easy_candidate(
        easy_request,
        easy_blocks,
        sum(selected_counts.values()),
    )
    easy_region = None
    if easy_request.ai_layout_hint or easy_stacks is None:
        easy_region = _validated_easy_candidate(
            easy_request,
            _easy_region_layout(
                easy_request,
                easy_merged,
                reference_count=sum(selected_counts.values()),
            ),
            sum(selected_counts.values()),
        )
    if easy_region is not None and (
        easy_stacks is None
        or (
            _preserves_upper_quality(easy_request, easy_stacks, easy_region)
            and _easy_compact_score(easy_request, easy_region)
            > _easy_compact_score(easy_request, easy_stacks)
        )
    ):
        easy_stacks = easy_region
    if easy_stacks is None:
        easy_stacks = _validated_easy_candidate(
            easy_request,
            _sku_block_layout(easy_request, easy_merged, "easy"),
            sum(selected_counts.values()),
        )
    if easy_stacks is None:
        easy_stacks = _validated_easy_candidate(
            easy_request,
            _repack_same_units(easy_request, easy_merged, "easy"),
            sum(selected_counts.values()),
        )
    if easy_stacks is None:
        easy_stacks = _validated_easy_candidate(
            easy_request,
            high_stacks,
            sum(selected_counts.values()),
        )
    if easy_stacks is None:
        easy_stacks = high_stacks
    easy_stacks = _prefer_compact_candidate(easy_request, easy_stacks, "easy")
    bounded_easy = _bounded_pallet_layout_candidate(
        easy_request,
        easy_stacks,
        selected_counts,
        "easy",
    )
    if bounded_easy is not None and _preserves_upper_quality(
        easy_request,
        easy_stacks,
        bounded_easy,
    ):
        easy_stacks = bounded_easy

    solutions = [
        _build_solution(request, high_stacks, "high_fill"),
        _build_solution(request, stable_stacks, "stable"),
        _build_solution(request, easy_stacks, "easy"),
    ]
    for index, solution in enumerate(solutions):
        for previous in solutions[:index]:
            if _layout_signature(solution) == _layout_signature(previous):
                solution.identical_to = previous.profile
                break
    if solutions[2].metrics.loaded_pieces < solutions[0].metrics.loaded_pieces:
        dropped = solutions[0].metrics.loaded_pieces - solutions[2].metrics.loaded_pieces
        solutions[2].warnings.append(
            f"易操作方案少装 {dropped} 件换取连续分区"
        )
        solutions[2].cons.append(
            f"为保持整托区域连续，少装 {dropped} 件"
        )
    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    request_id = hashlib.sha256(request_json.encode("utf-8")).hexdigest()[:12]
    return PackResponse(request_id=request_id, solutions=solutions)
