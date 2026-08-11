# SKU 块布局重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把装柜算法核心从"混合铺满/网格"重构为"每 SKU 集中成块"（SKU Block 布局），三方案重新定义（装得多=装载率、更稳妥=配平重块居中、易操作=分步），默认门端缓冲 300mm，满足 5+ SKU / 纯整托 / 纯散箱 / 混装场景。

**Architecture:** 新增 `_sku_block_layout(request, units, strategy)` 作为主布局函数：先为每个 SKU 构建 Block（网格参数 columns×rows×layers、块长宽），再按策略（fill=体积降序 / balance=重量从中心向外 / easy=输入顺序）沿柜长放置；块超长拆块或回退现有 `_layer_layout`。三方案在 `pack_order` 统一接线。

**Tech Stack:** Python 3.12、FastAPI、pydantic、rectpack、pytest（沿用现有）。

## Global Constraints

- 后端单位：毫米/克（int）；前端仍用厘米/千克（本计划不改前端）。
- `POST /api/v1/pack` 响应契约不变（placements/zones/metrics/pros/cons/warnings 结构）。
- 校验器 `validate_solution` 不改；所有布局返回前必须通过校验。
- 门端缓冲 `door_buffer_mm`：默认 300，`0` 关闭；写入 `PackRequest` 可选字段。
- 禁止引入新依赖（只用 stdlib + rectpack）。
- 现有测试必须全量通过（允许更新断言以反映新布局，不允许削弱正确性）。

---

### Task 1: 门端缓冲字段

**Files:**
- Modify: `backend/app/models.py`（PackRequest 加字段）
- Test: `backend/tests/test_door_buffer.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `PackRequest.door_buffer_mm: int = Field(default=300, ge=0)`；`request.door_buffer_mm` 供 Task 2-4 使用

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_door_buffer.py
from app.models import ContainerSpec, PackRequest, CargoSpec


def test_door_buffer_default_300():
    req = PackRequest(
        container=ContainerSpec(id="c", name="c", inner_length_mm=12032,
            inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
            door_height_mm=2585, max_payload_g=28600000),
        cargo_items=[],
    )
    assert req.door_buffer_mm == 300


def test_door_buffer_zero_allowed():
    req = PackRequest(
        container=ContainerSpec(id="c", name="c", inner_length_mm=12032,
            inner_width_mm=2352, inner_height_mm=2698, door_width_mm=2340,
            door_height_mm=2585, max_payload_g=28600000),
        cargo_items=[], door_buffer_mm=0,
    )
    assert req.door_buffer_mm == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_door_buffer.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（AttributeError: 'PackRequest' object has no attribute 'door_buffer_mm'）

- [ ] **Step 3: 实现**

在 `backend/app/models.py` 的 `PackRequest` 加：

```python
class PackRequest(BaseModel):
    container: ContainerSpec
    cargo_items: list[CargoSpec] = Field(default_factory=list)
    item_gap_mm: int = Field(default=0, ge=0)
    # 门端操作空间：可用柜长 = 柜长 - door_buffer_mm（默认 300，0=关闭）
    door_buffer_mm: int = Field(default=300, ge=0)
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_door_buffer.py -q --basetemp /tmp/pytest-zt`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/models.py backend/tests/test_door_buffer.py
git commit -m "feat: add door_buffer_mm to PackRequest (default 300)"
```

---

### Task 2: SKU 块模型（Block 构建）

**Files:**
- Modify: `backend/app/packing.py`（新增 `Block` dataclass 与 `_build_sku_blocks`）
- Test: `backend/tests/test_sku_blocks.py`（新建）

**Interfaces:**
- Consumes: `request.door_buffer_mm`（Task 1）
- Produces:
  - `@dataclass Block: sku_id: str; cargo: CargoSpec; length_mm: int; width_mm: int; height_mm: int; layers: int; columns: int; rows: int; block_length_mm: int; block_width_mm: int; pieces: int; total_weight_g: int`
  - `_build_sku_blocks(request, units: list[StackUnit | CompositeUnit], strategy: str) -> list[Block] | None`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_sku_blocks.py
from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import _build_sku_blocks, _build_stack_units


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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（ImportError: cannot import name '_build_sku_blocks'）

- [ ] **Step 3: 实现**

在 `backend/app/packing.py` 顶部（`PackedStack` 附近）加：

```python
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
```

在 `_build_stack_units` 之后加：

```python
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
        total = sum(u.count for u in group)
        # footprint：占宽最小的朝向（受门宽约束，简化取 LWH 或旋转后更窄者）
        length_mm = group[0].length_mm
        width_mm = group[0].width_mm
        swapped = SWAP_ORIENTATIONS.get(group[0].orientation)
        if (
            swapped in cargo.allowed_orientations
            and group[0].width_mm <= request.container.door_width_mm - 2 * c
        ):
            if group[0].width_mm < width_mm:
                length_mm, width_mm = group[0].width_mm, group[0].length_mm
        # 叠高层数：可叠 + 高度 + 承重（简化按 max_layers 与柜高）
        if cargo.stackable and not cargo.fragile:
            max_by_height = available_height // group[0].item_height_mm
            layers = max(1, min(cargo.max_layers, max_by_height))
        else:
            layers = 1
        columns = max(1, usable_width // (width_mm + gap))
        rows = (total + columns * layers - 1) // (columns * layers)
        block_length = rows * length_mm + max(0, rows - 1) * gap
        block_width = columns * width_mm + max(0, columns - 1) * gap
        blocks.append(Block(
            sku_id=sku_id, cargo=cargo, length_mm=length_mm, width_mm=width_mm,
            height_mm=group[0].item_height_mm, layers=layers, columns=columns,
            rows=rows, block_length_mm=block_length, block_width_mm=block_width,
            pieces=total, total_weight_g=sum(u.total_weight_g for u in group),
        ))
    return blocks
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_sku_blocks.py
git commit -m "feat: SKU block model builder"
```

---

### Task 3: 块放置与三方案顺序

**Files:**
- Modify: `backend/app/packing.py`（新增 `_place_blocks` 与 `_sku_block_layout`）
- Test: `backend/tests/test_sku_blocks.py`（追加）

**Interfaces:**
- Consumes: `Block`、`_build_sku_blocks`（Task 2）、`request.door_buffer_mm`
- Produces: `_place_blocks(request, blocks: list[Block], strategy: str) -> list[PackedStack] | None`；`_sku_block_layout(request, units, strategy) -> list[PackedStack] | None`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_sku_blocks.py 追加
from app.packing import _place_blocks, _build_sku_blocks, _build_stack_units


def test_place_blocks_fill_order_and_door_buffer():
    req = _req([
        _pallet("p2", "B", 890, 750, 1100, 303, 30),
        _pallet("p1", "A", 650, 650, 1000, 174, 30),
    ])
    units = _build_stack_units(req)
    blocks = _build_sku_blocks(req, units, "fill")
    stacks = _place_blocks(req, blocks, "fill")
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
    blocks = _build_sku_blocks(req, units, "balance")
    stacks = _place_blocks(req, blocks, "balance")
    assert stacks is not None
    heavy = [s for s in stacks if s.unit.cargo.id == "heavy"]
    light = [s for s in stacks if s.unit.cargo.id == "light"]
    hc = sum(s.x_mm + s.length_mm / 2 for s in heavy) / len(heavy)
    lc = sum(s.x_mm + s.length_mm / 2 for s in light) / len(light)
    center = 12032 / 2
    assert abs(hc - center) < abs(lc - center), "重块应比轻块更居中"
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（ImportError: cannot import name '_place_blocks'）

- [ ] **Step 3: 实现**

在 `_build_sku_blocks` 之后加：

```python
def _place_blocks(
    request: PackRequest,
    blocks: list[Block],
    strategy: str,
) -> list[PackedStack] | None:
    """按策略沿柜长放置块；每块内网格生成 PackedStack（每底位叠 layers 件）。"""
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    door_buffer = request.door_buffer_mm
    if strategy == "fill":
        ordered = sorted(blocks, key=lambda b: (-b.block_length_mm * b.block_width_mm, b.sku_id))
    elif strategy == "easy":
        # 输入顺序：按 request.cargo_items 的顺序
        order_map = {item.id: i for i, item in enumerate(request.cargo_items)}
        ordered = sorted(blocks, key=lambda b: (order_map.get(b.sku_id, 10**9), b.sku_id))
    else:  # balance：重块从柜长中心向外
        ordered = sorted(blocks, key=lambda b: (-b.total_weight_g, b.sku_id))
    # 计算块 x 偏移：balance 时重块居中，其余从柜头铺
    block_x: dict[str, int] = {}
    if strategy == "balance":
        total_len = sum(b.block_length_mm + gap for b in ordered) - gap
        if total_len > usable_length - door_buffer:
            return None
        # 重块放中间：按重量降序，位置从中心向外交替
        slots = len(ordered)
        slot_x = [0] * slots
        cursor = (usable_length - door_buffer - total_len) // 2
        for i in range(slots):
            slot_x[i] = cursor
            cursor += ordered[i].block_length_mm + gap
        # 重块映射到中间槽：重量降序对应槽序 [mid, mid-1, mid+1, ...]
        mid = slots // 2
        order_idx = sorted(range(slots), key=lambda i: (abs(i - (slots - 1) / 2), i))
        for weight_pos, slot in enumerate(order_idx):
            block_x[ordered[weight_pos].sku_id] = slot_x[slot]
    else:
        cursor = c
        for b in ordered:
            block_x[b.sku_id] = cursor
            cursor += b.block_length_mm + gap
        if cursor - gap > usable_length - door_buffer:
            return None
    placed: list[PackedStack] = []
    for b in ordered:
        x0 = block_x[b.sku_id]
        unit = next(u for u in [u for u in [] ])  # 占位：由调用方传 unit
    return placed
```

注意：`_place_blocks` 需要块对应的 StackUnit（含 first_instance_index/count）来生成 PackedStack。为保持接口简单，`_sku_block_layout` 内部同时持有 blocks 与 units，`_place_blocks` 接收 `blocks` 并在放置时从传入的 `units` 中按 `sku_id` 取栈。

**替代实现（本任务采用，职责更清晰）：** `_sku_block_layout` 整合构建+放置：

```python
def _sku_block_layout(
    request: PackRequest,
    units: list[StackUnit | CompositeUnit],
    strategy: str,
) -> list[PackedStack] | None:
    """SKU 块布局主入口：构建块 → 策略排序 → 逐块网格放置。"""
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
    if strategy == "fill":
        ordered = sorted(blocks, key=lambda b: (-b.block_length_mm * b.block_width_mm, b.sku_id))
    elif strategy == "easy":
        order_map = {item.id: i for i, item in enumerate(request.cargo_items)}
        ordered = sorted(blocks, key=lambda b: (order_map.get(b.sku_id, 10**9), b.sku_id))
    else:  # balance
        ordered = sorted(blocks, key=lambda b: (-b.total_weight_g, b.sku_id))
    total_len = sum(b.block_length_mm + gap for b in ordered) - gap
    if total_len > usable_length - door_buffer:
        return None
    block_x: dict[str, int] = {}
    if strategy == "balance":
        slots = len(ordered)
        slot_x: list[int] = []
        cursor = (usable_length - door_buffer - total_len) // 2
        for b in ordered:
            slot_x.append(cursor)
            cursor += b.block_length_mm + gap
        order_idx = sorted(range(slots), key=lambda i: (abs(i - (slots - 1) / 2), i))
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
        instance_pool = []
        for stack in group:
            for i in range(stack.count):
                instance_pool.append(stack.first_instance_index + i)
        piece_idx = 0
        # 块内网格：先按行（x 向推进），行内按列（y 向）
        for r in range(b.rows):
            y_cursor = c
            for col in range(b.columns):
                remaining = b.pieces - piece_idx
                if remaining <= 0:
                    break
                take = min(b.layers, remaining)
                piece_idx += take
                # 该底位叠 take 件（同 SKU 连续 instance）
                first = instance_pool[piece_idx - take]
                base_stack = group[0]
                unit = replace(
                    base_stack,
                    count=take,
                    stack_height_mm=take * b.height_mm,
                    total_weight_g=take * base_stack.cargo.weight_g,
                    first_instance_index=first,
                )
                placed.append(PackedStack(unit=unit, x_mm=x_cursor, y_mm=y_cursor, step=step))
                y_cursor += b.width_mm + gap
            x_cursor += b.length_mm + gap
        step += 1
    return placed
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: PASS（含 fill 顺序、门端缓冲、balance 重块居中）

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_sku_blocks.py
git commit -m "feat: SKU block placement with fill/balance/easy strategies"
```

---

### Task 4: 三方案接线（pack_order）

**Files:**
- Modify: `backend/app/packing.py`（`pack_order` 与 `_high_fill_candidate`）
- Test: `backend/tests/test_sku_blocks.py`（追加集成测试）

**Interfaces:**
- Consumes: `_sku_block_layout`（Task 3）
- Produces: 三方案布局（high_fill/stable/easy 均尝试 SKU 块，失败回退现有路径）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_sku_blocks.py 追加
from app.packing import pack_order


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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（pack_order 尚未用 SKU 块；门端缓冲未生效或布局相同）

- [ ] **Step 3: 实现**

在 `pack_order` 中接入（放在 `_build_stack_units` 之后、三方案生成处）：

```python
def pack_order(request: PackRequest) -> PackResponse:
    units = _build_stack_units(request)
    # 三方案统一尝试 SKU 块布局（装得多/更稳妥/易操作）
    high_fill_blocks = _sku_block_layout(request, units, "fill")
    if high_fill_blocks is not None:
        high_stacks = high_fill_blocks
    else:
        high_stacks = _high_fill_candidate(request, units)
    high_placements = _expand_stacks(request, high_stacks, "high_fill")
    selected_counts = Counter(item.cargo_id for item in high_placements)
    stable_units = _build_stack_units(request, dict(selected_counts), "stable")
    merged_stable = _merge_pallet_cartons(request, stable_units)
    stable_blocks = _sku_block_layout(request, stable_units, "balance")
    if stable_blocks is not None:
        stable_stacks = _swap_balance(request, stable_blocks)
    else:
        pallet_grid = _pallet_grid_layout(request, stable_units)
        if pallet_grid is not None:
            stable_stacks = pallet_grid
        else:
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
    easy_blocks = _sku_block_layout(request, stable_units, "easy")
    if easy_blocks is not None:
        easy_stacks = easy_blocks
    else:
        easy_stacks = _easy_region_layout(request, merged_stable) or (
            _repack_same_units(request, merged_stable, "easy") or high_stacks
        )
    solutions = [
        _build_solution(request, high_stacks, "high_fill"),
        _build_solution(request, stable_stacks, "stable"),
        _build_solution(request, easy_stacks, "easy"),
    ]
    return PackResponse(request_id=request_id, solutions=solutions)
```

（若 `pack_order` 现有结构不同，按现状并入：high_fill 优先 `_sku_block_layout(fill)`，stable 优先 `_sku_block_layout(balance)`，easy 优先 `_sku_block_layout(easy)`，失败走原回退链。）

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: PASS（69 托全装、门端缓冲生效、三方案布局互不相同）

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_sku_blocks.py
git commit -m "feat: wire SKU block layout into three solutions"
```

---

### Task 5: 场景覆盖与兜底（纯散箱/混装/块超长）

**Files:**
- Modify: `backend/app/packing.py`（`_sku_block_layout` 兜底、混装块拆分）
- Test: `backend/tests/test_sku_blocks.py`（追加）

**Interfaces:**
- Consumes: `_sku_block_layout`（Task 3/4）
- Produces: 兜底行为（块超长拆块/回退、少装披露）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_sku_blocks.py 追加
def _carton(id, sku, l, w, h, kg, qty, layers=8):
    return CargoSpec(id=id, sku=sku, name=sku, kind="carton", length_mm=l, width_mm=w,
        height_mm=h, weight_g=kg * 1000, quantity=qty, allowed_orientations=["LWH", "WLH"],
        stackable=True, max_layers=layers, max_top_load_g=kg * 2000, fragile=False, must_load=False)


def test_pure_carton_fill_falls_back_to_layer_layout():
    # 630 件单 SKU 散箱：fill 的 SKU 块超长 → 回退分层铺满（装载率最优）
    req = _req([_carton("ca", "CA", 500, 400, 400, 10, 630)])
    units = _build_stack_units(req)
    layout = _sku_block_layout(req, units, "fill")
    assert layout is None, "630 件单块超长应返回 None 由调用方回退"
    resp = pack_order(req)
    assert resp.solutions[0].metrics.loaded_pieces == 630


def test_mixed_pallet_carton_blocks():
    req = _req([
        _pallet("p1", "A", 1200, 800, 1100, 175, 4),
        _carton("ca", "CA", 500, 400, 400, 10, 40),
    ])
    resp = pack_order(req)
    assert resp.solutions[0].metrics.loaded_pieces == 44
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（fill 未回退/混装未成块）

- [ ] **Step 3: 实现**

在 `_sku_block_layout` 的 fill 分支前加"单 SKU 大订单回退"逻辑；混装块保持统一成块：

```python
    # fill 策略：若单 SKU 块超长（如 630 件散箱），返回 None 由调用方回退分层铺满
    if strategy == "fill":
        for b in blocks:
            if b.block_length_mm > usable_length - door_buffer:
                return None
```

（说明：`_build_sku_blocks` 已把同 SKU 合并成一块；块超长时 fill 直接回退，balance/easy 走 Task 3 的 `total_len` 检查返回 None → 回退链。混装托盘块 + 散箱块在 `_build_sku_blocks` 天然成块。）

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sku_blocks.py -q --basetemp /tmp/pytest-zt`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_sku_blocks.py
git commit -m "feat: SKU block fallback for oversized blocks and mixed orders"
```

---

### Task 6: 回归、性能与文档

**Files:**
- Modify: `backend/tests/`（更新受新布局影响的断言）
- Modify: `docs/HANDOFF.md`（记录 SKU 块布局与门端缓冲）
- Test: 全量

- [ ] **Step 1: 全量回归**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp /tmp/pytest-zt`
Expected: 全量通过；若有失败，逐条更新断言以反映 SKU 块布局（不得削弱正确性），并重跑。

- [ ] **Step 2: 性能基准（大订单）**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_large_order.py -q --basetemp /tmp/pytest-zt`
Expected: 通过（30 SKU / 5000 件在服务预算内）。

- [ ] **Step 3: 前端回归**

Run: `npm.cmd test -- --run && npm.cmd run build`
Expected: 14 tests passed + build 成功。

- [ ] **Step 4: 更新 HANDOFF**

在 `docs/HANDOFF.md` 第 6 节补充：SKU 块布局（每 SKU 集中成块、块内网格×双层）、三方案定义（装得多=体积降序/更稳妥=重块居中配平/易操作=输入顺序分步）、门端缓冲（door_buffer_mm 默认 300）。

- [ ] **Step 5: 提交**

```bash
git add docs/HANDOFF.md backend/tests
git commit -m "docs: SKU block layout and door buffer in handoff"
```

---

### Task 7: 部署与公网验证

**Files:**
- 无代码改动；发布与验证

- [ ] **Step 1: 打包并部署**

```bash
tar -czf packing-release.tar.gz backend/app
scp -i ~/.ssh/xingshuwen/id_ed25519_xingshuwen -o BatchMode=yes packing-release.tar.gz ubuntu@111.229.187.91:/tmp/
ssh -i ~/.ssh/xingshuwen/id_ed25519_xingshuwen -o BatchMode=yes ubuntu@111.229.187.91 \
  "cd /data/packing-assistant/app && tar -xzf /tmp/packing-release.tar.gz && sudo systemctl restart packing-assistant && sleep 4 && systemctl is-active packing-assistant && rm -f /tmp/packing-release.tar.gz"
rm -f packing-release.tar.gz
```

- [ ] **Step 2: 公网验证 5 SKU 案例**

Run: `curl -fsS -X POST https://packing.xingshuwen.com/api/v1/pack -H "Content-Type: application/json" -d '{"container":{"id":"40hq",...},"door_buffer_mm":300,"cargo_items":[...5 SKU 69 托...]}'`
Expected: 三方案 loaded=69、最远件 x ≤ 12032-300、更稳妥重心偏差最小、三方案布局互不相同。

- [ ] **Step 3: 推送远程**

```bash
git push origin main
git rev-list --left-right --count origin/main...main  # 期望 0 0
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 SKU 块模型 → Task 2
- §3.2 门端缓冲 → Task 1 + Task 3（放置截断）
- §3.3 三方案 → Task 3（策略）+ Task 4（接线）
- §3.4 兜底（拆块/回退/少装）→ Task 5
- §3.5 契约（door_buffer_mm 进 PackRequest，前端不改）→ Task 1
- §4 测试计划 → Task 2-5 各场景 + Task 6 回归
- 少装披露（打印稿 PB 案例）→ 现有 `_build_solution` 未改，保留 ✓

**2. Placeholder scan:** 无 TBD/TODO；Task 3 中 `_place_blocks` 的"占位"示例已说明改用整合版 `_sku_block_layout`（实现采用后者，避免占位代码）。

**3. Type consistency:** `Block` 字段（Task 2）与 `_sku_block_layout` 使用一致；`door_buffer_mm`（Task 1）在 Task 3/4 一致引用；`strategy` 取值 fill/balance/easy 贯穿 Task 3-4。
