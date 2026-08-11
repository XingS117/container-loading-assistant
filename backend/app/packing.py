from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
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
    "NON_STACKABLE": "不可叠放货物的上方不应有其他货物，请取消该货物的“可叠”选项",
    "FRAGILE_STACKING": "易碎货物上方不能叠放其他货物，请移除其上方的货物或关闭“可叠”",
    "MAX_LAYERS_EXCEEDED": "货物堆叠层数超过限制，请调大“最大层数”或减少数量",
    "TOP_LOAD_EXCEEDED": "货物顶部承重不足，请调大“顶部承重”或减少该位置货物数量",
}


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
    """一个 SKU 的集中装载块：块内网格 columns×rows，每底位叠 layers 件。"""
    sku_id: str
    cargo: CargoSpec
    length_mm: int
    width_mm: int
    height_mm: int
    layers: int
    columns: int
    rows: int
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


def _build_stack_units(
    request: PackRequest,
    quantity_limits: dict[str, int] | None = None,
    profile: Literal["high_fill", "stable"] = "high_fill",
) -> list[StackUnit]:
    available_height = request.container.inner_height_mm - 2 * request.container.clearance_mm
    units: list[StackUnit] = []
    for cargo in request.cargo_items:
        target_quantity = quantity_limits.get(cargo.id, cargo.quantity) if quantity_limits else cargo.quantity
        orientation = _best_orientation(request, cargo, target_quantity, profile)
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
            max_by_load = (
                cargo.max_top_load_g // cargo.weight_g if cargo.max_top_load_g > 0 else 1
            )
            layers = max(1, min(cargo.max_layers, max_by_height, max_by_load))
        else:
            layers = 1
        if has_composite:
            # CompositeUnit 已带上托散箱（占满托盘上方空间），块内每底位整托放置不可叠
            layers = 1
        columns = max(1, usable_width // (width_mm + gap))
        rows = (total + columns * layers - 1) // (columns * layers)
        block_length = rows * length_mm + max(0, rows - 1) * gap
        block_width = columns * width_mm + max(0, columns - 1) * gap
        blocks.append(Block(
            sku_id=sku_id, cargo=cargo, length_mm=length_mm, width_mm=width_mm,
            height_mm=unit.item_height_mm, layers=layers, columns=columns,
            rows=rows, block_length_mm=block_length, block_width_mm=block_width,
            pieces=total, total_weight_g=sum(u.total_weight_g for u in group),
        ))
    return blocks


def _sku_block_layout(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    strategy: str,
) -> list[PackedStack] | None:
    """SKU 块布局主入口：构建块 → 策略排序 → 逐块网格放置。

    接受 StackUnit 与 CompositeUnit（托盘+上托散箱）：CompositeUnit 块内整托
    放置（每底位 1 个托盘件），上托散箱不占底面位置，由 _expand_stacks 展开。
    块排序遵循 cargo.unload_order（后卸先进柜头）。
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
    # fill 策略：若单 SKU 块超长（如 630 件散箱），返回 None 由调用方回退分层铺满
    if strategy == "fill":
        for b in blocks:
            if b.block_length_mm > usable_length - door_buffer:
                return None
    if strategy == "fill":
        ordered = sorted(
            blocks,
            key=lambda b: (-b.cargo.unload_order, -b.block_length_mm * b.block_width_mm, b.sku_id),
        )
    elif strategy == "easy":
        order_map = {item.id: i for i, item in enumerate(request.cargo_items)}
        ordered = sorted(
            blocks,
            key=lambda b: (-b.cargo.unload_order, order_map.get(b.sku_id, 10**9), b.sku_id),
        )
    else:  # balance
        if any(b.cargo.unload_order for b in blocks):
            # 先卸后装硬约束优先：unload_order 降序从柜头铺（不做中心槽配平）
            ordered = sorted(
                blocks,
                key=lambda b: (-b.cargo.unload_order, -b.total_weight_g, b.sku_id),
            )
            center_slots = False
        else:
            # 无先卸后装约束：保持重块从柜长中心向外（中心槽配平）
            ordered = sorted(blocks, key=lambda b: (-b.total_weight_g, b.sku_id))
            center_slots = True
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
        # 每底位叠 layers 件：把同 SKU 栈的件按列×行铺开
        column_count = 0
        row_count = 0
        y_cursor = c
        x_cursor = block_x[b.sku_id]
        instance_pool: list[int] = []
        for stack in group:
            if isinstance(stack, CompositeUnit):
                # CompositeUnit 只取托盘件占底面（count=pallet.count=1），
                # 上托散箱随托盘展开，不占底面位置
                pallet_stack = stack.pallet
                for i in range(pallet_stack.count):
                    instance_pool.append(pallet_stack.first_instance_index + i)
            else:
                for i in range(stack.count):
                    instance_pool.append(stack.first_instance_index + i)
        piece_idx = 0
        # 单位指针：每个底位按顺序取 group 中对应单位（CompositeUnit 或 StackUnit），
        # 而不是全部用 group[0]。CompositeUnit 一单位一底位（整托+上托散箱整体放置），
        # StackUnit 单位全部件放入当前底位（不跨底位拆分）；单位用完则不再取，
        # 底位数 > 单位数时多余底位空置（同 SKU 单位件数已确定）。
        group_idx = 0
        # 块内网格：先按行（x 向推进），行内按列（y 向）
        for r in range(b.rows):
            y_cursor = c
            for col in range(b.columns):
                remaining = b.pieces - piece_idx
                if remaining <= 0:
                    break
                if group_idx >= len(group):
                    # 单位已用完：多余底位不再放件
                    break
                base = group[group_idx]
                if isinstance(base, CompositeUnit):
                    # 托盘块整托放置：CompositeUnit 整体（pallet + on_top）占一个底位，
                    # take=托盘件数（count=pallet.count），上托散箱不占底面位置，
                    # 由 _expand_stacks 的 CompositeUnit 分支展开托盘+上托散箱一次
                    take = min(base.pallet.count, remaining)
                else:
                    # 可叠 StackUnit：单位全部件放入当前底位，保证每件货物只放置一次
                    take = min(base.count, remaining)
                piece_idx += take
                # 该底位叠 take 件（同 SKU 连续 instance）
                first = instance_pool[piece_idx - take]
                if isinstance(base, CompositeUnit):
                    # 用 pallet 做 replace（count/take 语义），重建 CompositeUnit 保留 on_top。
                    # 块 footprint 已按占宽最小朝向 swap 时 pallet 须同步长宽/朝向，
                    # 否则块按 swap 尺寸排位而件按原尺寸展开 → OVERLAP（Critical-1）
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


def _ordered_units(units: list[StackUnit], strategy: str) -> list[StackUnit]:
    required = [unit for unit in units if unit.required]
    optional = [unit for unit in units if not unit.required]
    key_map = {
        "volume": lambda unit: (-unit.volume_mm3, -unit.length_mm * unit.width_mm, unit.id),
        "footprint": lambda unit: (-unit.length_mm * unit.width_mm, -unit.volume_mm3, unit.id),
        "weight": lambda unit: (-unit.total_weight_g, -unit.volume_mm3, unit.id),
        "lightweight": lambda unit: (unit.total_weight_g, -unit.volume_mm3, unit.id),
        "pieces": lambda unit: (-unit.count, unit.total_weight_g, unit.id),
        "sku": lambda unit: (unit.cargo.id, -unit.length_mm * unit.width_mm, unit.id),
    }
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
    for unit in _ordered_units(units, order):
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


def _candidate_score(stacks: list[PackedStack]) -> tuple[int, int, int]:
    return (
        sum(stack.unit.volume_mm3 for stack in stacks),
        sum(stack.unit.count for stack in stacks),
        -len(stacks),
    )


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
    if layer_candidates:
        return max(layer_candidates, key=_candidate_score)
    if not candidates:
        missing_required = [unit for unit in units if unit.required]
        cargo_names = "、".join(sorted({unit.cargo.sku for unit in missing_required}))
        raise PackingFailure(
            "MUST_LOAD_UNSATISFIED",
            f"必装货物 {cargo_names} 无法全部放入当前柜型",
            hint="请减少其他货物数量或更换更大柜型，确保必装货物能全部装入",
        )
    best_score = max(_candidate_score(candidate) for candidate in candidates)
    best_candidates = [
        candidate
        for candidate in candidates
        if _candidate_score(candidate) == best_score
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
        groups: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index, stack in enumerate(current):
            groups[(stack.length_mm, stack.width_mm)].append(index)
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

    # 1) 托盘带：所有托盘（单层 + 可叠托盘底层件）用 rectpack 排满后整体居中
    #    （重货集中中间）。先卸后装：卸货顺序大的（后卸）先铺。
    tray_units: list[StackUnit | CompositeUnit] = list(single_pallets) + [
        replace(
            unit,
            count=1,
            stack_height_mm=unit.item_height_mm,
            total_weight_g=unit.cargo.weight_g,
        )
        for unit in stackable_pallets
    ]
    # 托盘带：优先用网格（重托盘放中间行 → 重量集中中间、两头别偏重），
    # 网格放不下（托盘多/需旋转才能装入）时回退 rectpack（允许旋转，装得多）。
    tray_units.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    pallet_slots: list[tuple[int, int, StackUnit | CompositeUnit]] = []  # (x, y, unit)
    if tray_units and not band_grid:
        # 装得多：托盘带用 rectpack 从柜头铺（体积优先、位置自然），
        # 与"更稳妥"的居中网格配平布局区分
        packer = MaxRectsBssf(usable_length, usable_width, rot=False)
        for unit in tray_units:
            rect, placed_unit = _try_add_to_pallet_top(packer, unit, request)
            if rect is None:
                if not allow_partial or unit.required:
                    return None
                continue  # allow_partial：托盘丢弃（披露）
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
                    if not allow_partial or unit.required:
                        return None
                    continue  # allow_partial：托盘丢弃（披露）
                tmp_rects.append(
                    (int(rect.x), int(rect.y), int(rect.width), int(rect.height), placed_unit)
                )
            if tmp_rects:
                rx_min = min(r for r, _, _, _, _ in tmp_rects)
                rx_max = max(r + w for r, _, w, _, _ in tmp_rects)
                shift = max(0, (usable_length - (rx_max - rx_min)) // 2 - rx_min)
                for rx, ry, _, _, unit in tmp_rects:
                    pallet_slots.append((rx + shift, ry, unit))
    stackable_pallet_ids = {u.id for u in stackable_pallets}
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
        base_z = request.container.clearance_mm
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


def _easy_region_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
) -> list[PackedStack] | None:
    """Region-based easy layout; drops optional SKUs/stacks when needed."""
    if not units:
        return None
    if all(unit.count == 1 and unit.cargo.kind == "pallet" for unit in units):
        return _pallet_grid_layout(request, units)
    # 易操作：优先区域化（每 SKU 集中成带/排、分步装载），与"装得多/更稳妥"
    # 的混合铺满布局区分开；区域化放不下时再回退分层铺满
    full = _try_region_layouts(request, units)
    if full is not None:
        return full
    mixed = _layer_layout(request, units, allow_partial=True)
    if mixed is not None:
        return mixed
    required = [unit for unit in units if unit.required]
    optional_by_sku: dict[str, list[StackUnit]] = defaultdict(list)
    for unit in units:
        if not unit.required:
            optional_by_sku[unit.cargo.id].append(unit)
    optional_order = [
        cargo_id
        for cargo_id in (item.id for item in request.cargo_items)
        if cargo_id in optional_by_sku
    ]
    optional_order.sort(
        key=lambda cargo_id: (
            optional_by_sku[cargo_id][0].cargo.kind != "carton",
            sum(unit.volume_mm3 for unit in optional_by_sku[cargo_id]),
            cargo_id,
        )
    )
    kept_optional = [unit for unit in units if not unit.required]
    for cargo_id in optional_order:
        candidate = _try_region_layouts(request, required + kept_optional)
        if candidate is not None:
            return candidate
        kept_optional = [
            unit for unit in kept_optional if unit.cargo.id != cargo_id
        ]
    candidate = _try_region_layouts(request, required)
    if candidate is not None:
        return candidate
    optional_sorted = sorted(
        [unit for unit in units if not unit.required],
        key=lambda unit: (unit.volume_mm3, unit.id),
    )
    for skip in range(len(optional_sorted)):
        candidate = _try_region_layouts(
            request,
            required + optional_sorted[skip + 1 :],
        )
        if candidate is not None:
            return candidate
    return None


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
) -> PackingSolution:
    placements = _expand_stacks(request, stacks, profile)
    validation = validate_solution(
        request.container,
        request.cargo_items,
        placements,
        item_gap_mm=request.item_gap_mm,
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
    names = {"high_fill": "装得多", "stable": "更稳妥", "easy": "易操作"}
    warnings = []
    if request.door_buffer_mm > 0:
        warnings.append(f"柜门预留操作空间 {request.door_buffer_mm}mm")
    if sum(unloaded_counts.values()):
        warnings.append(f"仍有 {sum(unloaded_counts.values())} 件货物未装入本柜")
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


def pack_order(request: PackRequest) -> PackResponse:
    units = _build_stack_units(request)
    # 三方案统一优先尝试 SKU 块布局（装得多/更稳妥/易操作），失败走原回退链
    # 缺陷 B 修复：SKU 块布局改传 merged（散箱上托托盘顶面，轻在上）
    high_merged = _merge_pallet_cartons(request, units)
    high_blocks = _sku_block_layout(request, high_merged, "fill")
    if high_blocks is not None:
        high_stacks = high_blocks
    else:
        high_stacks = _high_fill_candidate(request, units)
    high_placements = _expand_stacks(request, high_stacks, "high_fill")
    selected_counts = Counter(item.cargo_id for item in high_placements)
    stable_units = _build_stack_units(request, dict(selected_counts), "stable")
    merged_stable = _merge_pallet_cartons(request, stable_units)
    stable_blocks = _sku_block_layout(request, merged_stable, "balance")
    if stable_blocks is not None:
        stable_stacks = _swap_balance(request, stable_blocks)
    else:
        pallet_grid = _pallet_grid_layout(request, stable_units)
        if pallet_grid is not None:
            stable_stacks = pallet_grid
        else:
            # 混合尺寸纯整托：SKU 块配平布局（重块居中）→ 与"装得多"布局区分
            balanced = _stable_balance_layout(request, stable_units)
            if balanced is not None:
                stable_stacks = balanced
            else:
                mixed = _layer_layout(request, merged_stable)
                if mixed is not None:
                    stable_stacks = mixed
                else:
                    stable_stacks = _repack_same_units(
                        request, merged_stable, "stable"
                    ) or _center_stacks(request, high_stacks)
                stable_stacks = _swap_balance(request, stable_stacks)
    easy_blocks = _sku_block_layout(request, merged_stable, "easy")
    if easy_blocks is not None:
        easy_stacks = easy_blocks
    else:
        easy_region = _easy_region_layout(request, merged_stable)
        easy_stacks = easy_region if easy_region is not None else (
            _repack_same_units(request, merged_stable, "easy") or high_stacks
        )

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
        solutions[2].cons.append(
            f"为便于装载少装 {solutions[0].metrics.loaded_pieces - solutions[2].metrics.loaded_pieces} 件"
        )

    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    request_id = hashlib.sha256(request_json.encode("utf-8")).hexdigest()[:12]
    return PackResponse(request_id=request_id, solutions=solutions)
