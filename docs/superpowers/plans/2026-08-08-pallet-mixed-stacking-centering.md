# 整托混合堆叠与托盘居中 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让混装订单（整托+散箱）的装柜方案满足：①可上托散箱实际叠在整托顶面（整托在下、散装在上）；②整托重量集中在柜长中间、散箱补两头；三个方案（装得多/更稳妥/易操作）都体现。

**Architecture:** 新增 `CompositeUnit`（托盘 + 上叠散箱栈）作为排布单位，合并发生在重量筛选之后、二维排布之前；新增 `_mixed_balance_layout`（托盘带网格居中 + 两端散箱区）；`validator` 增加托盘特判；`_expand_stacks` 支持复合单位展开。`placements`/`zones`/`metrics` API 契约不变，前端零改动。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、rectpack、pytest。

## Global Constraints

- 布局必须确定性、可复现（所有排序固定，不引入随机）。
- 每个候选返回前必须通过 `validate_solution()`；校验失败抛 `INTERNAL_INVALID_LAYOUT`（现有保护不变）。
- 单位：整数毫米/克；`item_gap_mm` 只在水平方向执行。
- 易碎散箱（`fragile=True`）**不上托**，保持独立栈。
- 托盘 `max_top_load_g == 0`（不可承重）时散箱**不上托**。
- 托盘上**禁止叠整托**（新增错误码 `PALLET_STACKING`）。
- 三方案都做"托盘带居中"：stable 强制、high_fill 加入候选选优、easy 混合路径优先。
- `POST /api/v1/pack` 请求/响应契约不变；`Placement`/`PackingSolution`/`zones`/`metrics` 结构不变。
- 规格文档：`docs/superpowers/specs/2026-08-08-pallet-mixed-stacking-centering-design.md`。

---

### Task 1: validator 托盘特判

**Files:**
- Modify: `backend/app/validator.py:162-179`（support 检查循环）
- Test: `backend/tests/test_validator.py`（新增 3 个测试）

**Interfaces:**
- Consumes: 现有 `validate_solution(container, cargo_items, placements, item_gap_mm) -> ValidationResult`；`CargoSpec.kind`（"pallet"/"carton"）。
- Produces: 新错误码 `PALLET_STACKING`；"托盘上允许叠散箱、禁止叠整托、上叠总重 ≤ max_top_load_g"的校验语义（后续 Task 2-5 的生成器依赖此语义）。

- [ ] **Step 1: 写失败测试**（追加到 `backend/tests/test_validator.py` 末尾）

```python
def test_accepts_cartons_on_top_of_pallet():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=1_000_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    carton_item = cargo(
        "B", quantity=1, length_mm=500, width_mm=400, height_mm=300,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("B-0", cargo_id="B", z_mm=1000, length_mm=500, width_mm=400, height_mm=300),
    ]

    result = validate_solution(container(), [pallet_item, carton_item], placements)

    assert result.valid is True
    assert result.errors == []


def test_rejects_pallet_stacked_on_pallet():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=1_000_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    pallet2 = cargo(
        "Q", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=0, length_mm=1000, width_mm=1000, height_mm=1000,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("Q-0", cargo_id="Q", z_mm=1000),
    ]

    result = validate_solution(container(), [pallet_item, pallet2], placements)

    assert "PALLET_STACKING" in error_codes(result)


def test_rejects_cartons_exceeding_pallet_top_load():
    pallet_item = cargo(
        "P", kind="pallet", quantity=1, stackable=False, max_layers=1,
        max_top_load_g=50_000, length_mm=1200, width_mm=1000, height_mm=1000,
    )
    carton_item = cargo(
        "B", quantity=1, weight_g=100_000, length_mm=500, width_mm=400, height_mm=300,
    )
    placements = [
        placement("P-0", cargo_id="P", length_mm=1200, width_mm=1000, height_mm=1000),
        placement("B-0", cargo_id="B", z_mm=1000, length_mm=500, width_mm=400, height_mm=300),
    ]

    result = validate_solution(container(), [pallet_item, carton_item], placements)

    assert "TOP_LOAD_EXCEEDED" in error_codes(result)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_validator.py -q --basetemp /tmp/pytest-zt`
Expected: 3 个新测试 FAIL（`PALLET_STACKING` 未实现/托盘上叠散箱报 NON_STACKABLE、MAX_LAYERS_EXCEEDED）。

- [ ] **Step 3: 实现**（替换 `validator.py:162-179` 的 support 检查循环）

```python
    for support in known:
        item = cargo_by_id[support.cargo_id]
        above = above_by_id[support.id]
        if not above:
            continue

        if item.kind == "pallet":
            if any(cargo_by_id[other.cargo_id].kind == "pallet" for other in above):
                add("PALLET_STACKING", "整托上方不能叠放整托", support.id)
        else:
            if not item.stackable:
                add("NON_STACKABLE", "不可叠放货物上方存在其他货物", support.id)
            if item.fragile:
                add("FRAGILE_STACKING", "易碎货物上方存在其他货物", support.id)
            levels = 1 + len({other.z_mm for other in above})
            if levels > item.max_layers:
                add("MAX_LAYERS_EXCEEDED", "货物堆叠层数超过限制", support.id)

        top_load = sum(cargo_by_id[other.cargo_id].weight_g for other in above)
        if top_load > item.max_top_load_g:
            add("TOP_LOAD_EXCEEDED", "货物顶部承重超过限制", support.id)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_validator.py -q --basetemp /tmp/pytest-zt`
Expected: 全部 PASS（含原有 9 个测试，`test_rejects_stacking_on_fragile_or_non_stackable_item`、`test_rejects_max_layers_and_top_load` 仍应通过——它们用 carton 作支撑物，走 else 分支）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/validator.py backend/tests/test_validator.py
git commit -m "feat(validator): allow cartons on pallets, forbid pallet-on-pallet"
```

---

### Task 2: CompositeUnit 数据结构与 `_merge_pallet_cartons`

**Files:**
- Modify: `backend/app/packing.py`（`StackUnit` 定义之后新增 `CompositeUnit`；`_select_payload_units` 之后新增合并函数）
- Test: `backend/tests/test_mixed_stacking.py`（新建）

**Interfaces:**
- Consumes: `StackUnit`（字段 `id/cargo/orientation/count/length_mm/width_mm/item_height_mm/stack_height_mm/total_weight_g/first_instance_index/required`）；`SWAP_ORIENTATIONS`（`packing.py:86`）；`MaxRectsBssf`。
- Produces:
  - `CompositeUnit`：`pallet: StackUnit`、`on_top: tuple[StackUnit, ...]`；属性 `id`、`cargo`、`required`、`orientation`、`length_mm`、`width_mm`、`count`、`total_weight_g`、`volume_mm3`（后续 Task 3/4/5 依赖）。
  - `_merge_pallet_cartons(request: PackRequest, units: list[StackUnit]) -> list[CompositeUnit | StackUnit]`：可上托散箱栈并入托盘，其余单位原样返回。
  - `_try_add_to_pallet_top(packer, carton: StackUnit, request: PackRequest) -> tuple[rect, StackUnit] | tuple[None, None]`：向托盘顶面 bin 添加一个散箱栈（不旋转优先，旋转需在 `allowed_orientations` 内）；旋转时返回旋转后的栈变体（`replace` 互换 length/width 并更新 orientation），保证展开阶段尺寸/朝向与放置位置一致。

- [ ] **Step 1: 写失败测试**（新建 `backend/tests/test_mixed_stacking.py`）

```python
import pytest

from app.models import CargoSpec, ContainerSpec, PackRequest
from app.packing import (
    CompositeUnit,
    StackUnit,
    _build_stack_units,
    _merge_pallet_cartons,
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
    assert composite.count == 5
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: FAIL（`ImportError: cannot import name 'CompositeUnit'`）。

- [ ] **Step 3: 实现**（`packing.py`：`StackUnit` 定义后新增 `CompositeUnit`；`_select_payload_units` 后新增两个函数）

```python
@dataclass(frozen=True)
class CompositeUnit:
    """A pallet (count=1) with carton stacks packed on its top surface."""
    pallet: StackUnit
    on_top: tuple[StackUnit, ...] = ()

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
        return self.pallet.count + sum(unit.count for unit in self.on_top)

    @property
    def total_weight_g(self) -> int:
        return self.pallet.total_weight_g + sum(unit.total_weight_g for unit in self.on_top)

    @property
    def volume_mm3(self) -> int:
        return self.pallet.volume_mm3 + sum(unit.volume_mm3 for unit in self.on_top)
```

```python
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
    door_usable_width = request.container.door_width_mm - 2 * c
    rect = packer.add_rect(carton.length_mm, carton.width_mm, rid=carton.id)
    if rect is not None:
        return rect, carton
    swapped_orientation = SWAP_ORIENTATIONS.get(carton.orientation)
    if (
        swapped_orientation in carton.cargo.allowed_orientations
        and carton.length_mm <= door_usable_width
    ):
        rect = packer.add_rect(carton.width_mm, carton.length_mm, rid=carton.id)
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
    cartons.sort(key=lambda unit: (-unit.volume_mm3, unit.id))
    merged: list[CompositeUnit | StackUnit] = []
    remaining: list[StackUnit] = cartons
    for pallet in pallets:
        if pallet.cargo.max_top_load_g <= 0:
            merged.append(pallet)
            continue
        packer = MaxRectsBssf(pallet.length_mm, pallet.width_mm, rot=False)
        assigned: list[StackUnit] = []
        load_left = pallet.cargo.max_top_load_g
        height_left = available_height - pallet.stack_height_mm
        still: list[StackUnit] = []
        for carton in remaining:
            if carton.cargo.fragile:
                still.append(carton)
                continue
            if (
                carton.stack_height_mm > height_left
                or carton.total_weight_g > load_left
            ):
                still.append(carton)
                continue
            rect, placed_carton = _try_add_to_pallet_top(packer, carton, request)
            if rect is None:
                still.append(carton)
                continue
            assigned.append(placed_carton)
            load_left -= carton.total_weight_g
        remaining = still
        if assigned:
            merged.append(CompositeUnit(pallet=pallet, on_top=tuple(assigned)))
        else:
            merged.append(pallet)
    merged.extend(remaining)
    return merged
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: 5 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_mixed_stacking.py
git commit -m "feat(packing): add CompositeUnit and merge cartons onto pallet tops"
```

---

### Task 3: `_expand_stacks` 支持复合单位

**Files:**
- Modify: `backend/app/packing.py:627-648`（`_expand_stacks` 的逐栈展开循环）
- Test: `backend/tests/test_mixed_stacking.py`（追加）

**Interfaces:**
- Consumes: `CompositeUnit`（Task 2）；`PackedStack`（`unit/x_mm/y_mm/step`）。
- Produces: 复合单位展开规则——托盘件 `z = clearance + offset×托盘件高`；上叠散箱件 `z = clearance + 托盘栈高 + offset×散箱件高`；全部件与托盘同 `step`。

- [ ] **Step 1: 写失败测试**（追加到 `backend/tests/test_mixed_stacking.py`）

```python
from app.packing import PackedStack, _expand_stacks  # 追加到 import


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
    assert len(carton_p) == 4
    assert pallet_p[0].z_mm == 0
    assert pallet_p[0].height_mm == 1100
    assert [p.z_mm for p in sorted(carton_p, key=lambda p: p.instance_index)] == [
        1100, 1400, 1700, 2000,
    ]
    assert all(p.step == 1 for p in placements)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py::test_expand_composite_places_cartons_on_pallet_top -q --basetemp /tmp/pytest-zt`
Expected: FAIL（`AttributeError: 'CompositeUnit' object has no attribute 'first_instance_index'` 或断言失败）。

- [ ] **Step 3: 实现**（修改 `_expand_stacks` 的展开循环，`packing.py:627-648`）

```python
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
            for on_top in unit.on_top:
                for offset in range(on_top.count):
                    placements.append(
                        Placement(
                            id=f"{on_top.cargo.id}-{on_top.first_instance_index + offset}",
                            cargo_id=on_top.cargo.id,
                            instance_index=on_top.first_instance_index + offset,
                            x_mm=stack.x_mm,
                            y_mm=stack.y_mm,
                            z_mm=top_z + offset * on_top.item_height_mm,
                            length_mm=stack.length_mm,
                            width_mm=stack.width_mm,
                            height_mm=on_top.item_height_mm,
                            rotation=stack.orientation,
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
```

注意：`length_mm=stack.length_mm`/`width_mm=stack.width_mm` 对复合单位取托盘 footprint；散箱件使用 `on_top` 栈自身尺寸/朝向（Task 2 合并时旋转已被 `replace` 写成栈变体），并完整落在托盘顶面内（合并时保证），校验器 `UNSUPPORTED`/尺寸一致性按散箱自身 `cargo` 与 `rotation` 校验，天然覆盖。

```python
            top_z = base_z + pallet.stack_height_mm
            for on_top in unit.on_top:
                for offset in range(on_top.count):
                    placements.append(
                        Placement(
                            id=f"{on_top.cargo.id}-{on_top.first_instance_index + offset}",
                            cargo_id=on_top.cargo.id,
                            instance_index=on_top.first_instance_index + offset,
                            x_mm=stack.x_mm,
                            y_mm=stack.y_mm,
                            z_mm=top_z + offset * on_top.item_height_mm,
                            length_mm=on_top.length_mm,
                            width_mm=on_top.width_mm,
                            height_mm=on_top.item_height_mm,
                            rotation=on_top.orientation,
                            weight_g=on_top.cargo.weight_g,
                            step=step,
                        )
                    )
```

（合并时 `_try_add_to_pallet_top` 旋转的栈已被 `replace` 写成旋转变体，展开直接用 `on_top.orientation`/`on_top.length_mm`/`on_top.width_mm` 即与放置位置一致，无需额外传递旋转标记。）

- [ ] **Step 4: 运行测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: 6 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_mixed_stacking.py
git commit -m "feat(packing): expand CompositeUnit placements on pallet top"
```

---

### Task 4: `_mixed_balance_layout`（托盘带居中 + 两端散箱）

**Files:**
- Modify: `backend/app/packing.py`（`_pallet_grid_layout` 之后新增）
- Test: `backend/tests/test_mixed_stacking.py`（追加）

**Interfaces:**
- Consumes: `CompositeUnit`/`StackUnit`（`cargo.kind`、`total_weight_g`、`length_mm`、`width_mm`、`orientation`、`cargo.allowed_orientations`）；`SWAP_ORIENTATIONS`；`MaxRectsBssf`；`_try_add_to_pallet_top`。
- Produces: `_mixed_balance_layout(request: PackRequest, units: list[CompositeUnit | StackUnit]) -> list[PackedStack] | None`——托盘单位按重量从中心向外网格排布并整体居中，未上托散箱栈填入前后两端；放不下返回 `None`（调用方回退）。

- [ ] **Step 1: 写失败测试**（追加到 `backend/tests/test_mixed_stacking.py`）

```python
from app.packing import _mixed_balance_layout  # 追加到 import


def test_mixed_balance_layout_centers_pallets():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    layout = _mixed_balance_layout(request, merged)

    assert layout is not None
    pallet_stacks = [s for s in layout if s.unit.cargo.kind == "pallet"]
    assert len(pallet_stacks) == 2
    total = sum(s.unit.total_weight_g for s in pallet_stacks)
    cg_x = (
        sum((s.x_mm + s.length_mm / 2) * s.unit.total_weight_g for s in pallet_stacks)
        / total
    )
    assert mixed_container().inner_length_mm / 3 <= cg_x <= mixed_container().inner_length_mm * 2 / 3


def test_mixed_balance_layout_passes_validator():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[
            pallet_box(),
            pallet_box(id="pallet2", sku="P-200", weight_g=600_000),
            carton_box(quantity=8),
        ],
    )
    merged = _merge_pallet_cartons(request, _build_stack_units(request))

    layout = _mixed_balance_layout(request, merged)

    assert layout is not None
    placements = _expand_stacks(request, layout, "stable")
    result = validate_solution(
        request.container, request.cargo_items, placements, request.item_gap_mm
    )
    assert result.valid, [error.code for error in result.errors]


def test_mixed_balance_layout_returns_none_for_pure_carton():
    request = PackRequest(
        container=mixed_container(),
        cargo_items=[carton_box(quantity=4)],
    )

    layout = _mixed_balance_layout(request, _build_stack_units(request))

    assert layout is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: 3 个新测试 FAIL（`ImportError: cannot import name '_mixed_balance_layout'`）。

- [ ] **Step 3: 实现**（`_pallet_grid_layout` 之后新增）

```python
def _mixed_balance_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
) -> list[PackedStack] | None:
    """Center pallet units along container length; carton stacks fill both ends."""
    if not units:
        return None
    c = request.container.clearance_mm
    gap = request.item_gap_mm
    usable_length = request.container.inner_length_mm - 2 * c
    usable_width = request.container.inner_width_mm - 2 * c
    door_usable_width = request.container.door_width_mm - 2 * c
    pallets = [unit for unit in units if unit.cargo.kind == "pallet"]
    cartons = [unit for unit in units if unit.cargo.kind == "carton"]
    if not pallets or not cartons:
        return None  # 纯整托走 _pallet_grid_layout；纯散箱走 rectpack

    options: list[set[tuple[int, int]]] = []
    for unit in pallets:
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
        rows = (len(pallets) + columns - 1) // columns
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
    rows = (len(pallets) + columns - 1) // columns
    total_length = rows * along_length + (rows - 1) * gap
    x0 = c + (usable_length - total_length) // 2

    pallets.sort(key=lambda unit: (-unit.total_weight_g, unit.id))
    row_order = sorted(
        range(rows),
        key=lambda row: (abs(row - (rows - 1) / 2), row),
    )
    column_weights = [0] * columns
    assignments: list[tuple[int, int, int]] = []
    index = 0
    for row in row_order:
        for _ in range(columns):
            if index >= len(pallets):
                break
            column = min(
                range(columns),
                key=lambda col: (column_weights[col], col),
            )
            column_weights[column] += pallets[index].total_weight_g
            assignments.append((row, column, index))
            index += 1

    left_len = x0 - c
    right_len = usable_length - total_length - left_len
    # 装载区域按 x 升序编号：左端区、托盘带（按行）、右端区
    next_step = 1
    left_step = next_step if left_len > 0 else None
    if left_step is not None:
        next_step += 1
    pallet_row_first_step = next_step
    next_step += rows
    right_step = next_step if right_len > 0 else None

    placed: list[PackedStack] = []
    for row, column, unit_index in assignments:
        unit = pallets[unit_index]
        placed.append(
            PackedStack(
                unit=unit,
                x_mm=x0 + row * (along_length + gap),
                y_mm=c + column * (across_width + gap),
                rotated=(unit.length_mm, unit.width_mm) == (across_width, along_length),
                step=pallet_row_first_step + row,
            )
        )

    cartons.sort(key=lambda unit: (-unit.volume_mm3, unit.id))
    remaining = cartons
    for zone_x, zone_len, zone_step in (
        (c, left_len, left_step),
        (x0 + total_length, right_len, right_step),
    ):
        if zone_step is None or not remaining:
            continue
        packer = MaxRectsBssf(zone_len, usable_width, rot=False)
        still: list[StackUnit] = []
        for carton in remaining:
            rect, _ = _try_add_to_pallet_top(packer, carton, request)
            if rect is None:
                still.append(carton)
                continue
            placed.append(
                PackedStack(
                    unit=carton,
                    x_mm=zone_x + int(rect.x),
                    y_mm=c + int(rect.y),
                    rotated=(
                        carton.length_mm != carton.width_mm
                        and int(rect.width) == carton.width_mm
                        and int(rect.height) == carton.length_mm
                    ),
                    step=zone_step,
                )
            )
        remaining = still
    if remaining:
        return None  # 两端放不下全部散箱 → 布局失败，由调用方回退
    placed.sort(key=lambda stack: stack.unit.id)
    return placed
```

说明：
- 散箱区 bin 宽度用 `usable_width`（含两端空隙），`_try_add_to_pallet_top` 的旋转门宽检查已保证 `width <= door_usable_width`。
- 托盘带与两端区 x 范围不重叠，几何上安全；validator 的 3D 碰撞检查兜底。

- [ ] **Step 4: 运行测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: 9 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_mixed_stacking.py
git commit -m "feat(packing): add mixed balance layout centering pallets"
```

---

### Task 5: 三方案接线（pack_order / high_fill / easy）

**Files:**
- Modify: `backend/app/packing.py:303-326`（`_high_fill_candidate`）、`:785-835`（`_easy_region_layout`）、`:1079-1094`（`pack_order`）
- Test: `backend/tests/test_mixed_stacking.py`（追加端到端测试）；`backend/tests/test_packing.py`（回归确认）

**Interfaces:**
- Consumes: `_merge_pallet_cartons`、`_mixed_balance_layout`、`_expand_stacks`（Task 2-4）。
- Produces: `pack_order` 对混装订单返回三方案——high_fill 候选含混合布局并选优；stable 强制托盘带居中；easy 混合路径优先；stable 保持 high_fill 同一批货物（`selected_counts` 改为从 high_fill 展开的 `placements` 计算，复合单位也正确）。

- [ ] **Step 1: 写失败测试**（追加到 `backend/tests/test_mixed_stacking.py`）

```python
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
        pallet_top = pallet_p[0].z_mm + pallet_p[0].height_mm
        assert any(p.z_mm == pallet_top for p in carton_p), "散箱应叠在整托顶面"


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
    total = sum(p.weight_g for p in pallet_p)
    cg_x = sum((p.x_mm + p.length_mm / 2) * p.weight_g for p in pallet_p) / total
    assert container.inner_length_mm / 3 <= cg_x <= container.inner_length_mm * 2 / 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py -q --basetemp /tmp/pytest-zt`
Expected: 2 个新测试 FAIL（散箱未上托 / 托盘未居中）。

- [ ] **Step 3: 实现**

3a. `_high_fill_candidate`（`packing.py:303-326`）：`pool` 之后先合并，再追加混合布局候选：

```python
def _high_fill_candidate(request: PackRequest, units: list[StackUnit]) -> list[PackedStack]:
    candidates: list[list[PackedStack]] = []
    for payload_strategy in ("volume", "footprint", "pieces", "lightweight"):
        pool = _select_payload_units(request, units, payload_strategy)
        merged = _merge_pallet_cartons(request, pool)
        for algo in PACK_ALGOS:
            for order in ("volume", "footprint", "weight", "lightweight"):
                packed = _pack_units(request, merged, algo, order)
                packed_ids = {stack.unit.id for stack in packed}
                if all(not unit.required or unit.id in packed_ids for unit in units):
                    candidates.append(packed)
    mixed_pool = _select_payload_units(request, units, "volume")
    mixed = _mixed_balance_layout(
        request, _merge_pallet_cartons(request, mixed_pool)
    )
    if mixed is not None:
        candidates.append(mixed)
    if not candidates:
        missing_required = [unit for unit in units if unit.required]
        cargo_names = "、".join(sorted({unit.cargo.sku for unit in missing_required}))
        raise PackingFailure(
            "MUST_LOAD_UNSATISFIED",
            f"必装货物 {cargo_names} 无法全部放入当前柜型",
        )
    best_score = max(_candidate_score(candidate) for candidate in candidates)
    best_candidates = [
        candidate
        for candidate in candidates
        if _candidate_score(candidate) == best_score
    ]
    return min(best_candidates, key=lambda candidate: _stack_imbalance(request, candidate))
```

3b. `_easy_region_layout`（`packing.py:785-793`）：混装路径优先用 `_mixed_balance_layout`：

```python
def _easy_region_layout(
    request: PackRequest,
    units: list[CompositeUnit | StackUnit],
) -> list[PackedStack] | None:
    """Region-based easy layout; drops optional SKUs/stacks when needed."""
    if not units:
        return None
    if all(unit.count == 1 and unit.cargo.kind == "pallet" for unit in units):
        return _pallet_grid_layout(request, units)
    if any(unit.cargo.kind == "pallet" for unit in units):
        mixed = _mixed_balance_layout(request, units)
        if mixed is not None:
            return mixed
    full = _try_region_layouts(request, units)
    if full is not None:
        return full
    ...（其余少装逻辑保持不变，units 类型标注放宽为 list[CompositeUnit | StackUnit]）
```

3c. `pack_order`（`packing.py:1079-1094`）：

```python
def pack_order(request: PackRequest) -> PackResponse:
    units = _build_stack_units(request)
    high_stacks = _high_fill_candidate(request, units)
    high_placements = _expand_stacks(request, high_stacks, "high_fill")
    selected_counts = Counter(item.cargo_id for item in high_placements)
    stable_units = _build_stack_units(request, dict(selected_counts), "stable")
    merged_stable = _merge_pallet_cartons(request, stable_units)
    pallet_grid = _pallet_grid_layout(request, stable_units)
    if pallet_grid is not None:
        stable_stacks = pallet_grid
    else:
        mixed = _mixed_balance_layout(request, merged_stable)
        if mixed is not None:
            stable_stacks = mixed
        else:
            stable_stacks = _repack_same_units(
                request, merged_stable, "stable"
            ) or _center_stacks(request, high_stacks)
        stable_stacks = _swap_balance(request, stable_stacks)
    easy_region = _easy_region_layout(request, merged_stable)
    easy_stacks = easy_region if easy_region is not None else (
        _repack_same_units(request, merged_stable, "easy") or high_stacks
    )
    ...（其余不变：solutions 构建、identical_to、少装披露、request_id）
```

注意：`_repack_same_units`/`_center_stacks`/`_swap_balance` 接受 `list[CompositeUnit | StackUnit]`（它们只读 `unit` 的鸭子类型属性并 `replace(stack, x_mm=...)` 修改坐标，不触碰 unit 内部）。

- [ ] **Step 4: 运行测试确认通过**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_mixed_stacking.py backend/tests/test_packing.py -q --basetemp /tmp/pytest-zt`
Expected: 全部 PASS。若 `test_packing.py` 原有断言被破坏（如混装件数变化），检查是否因混合堆叠导致——预期不破坏（`test_packs_cartons_and_whole_pallets_together` 的托盘 `max_top_load_g=0` 不上托）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/packing.py backend/tests/test_mixed_stacking.py
git commit -m "feat(packing): wire pallet-on-bottom centering into three solutions"
```

---

### Task 6: 回归、性能与文档

**Files:**
- Modify: `docs/HANDOFF.md`（记录新行为）
- Test: 全量后端测试 + 性能基准 + 前端回归

**Interfaces:**
- Consumes: Task 1-5 全部改动。

- [ ] **Step 1: 后端全量回归**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp /tmp/pytest-zt`
Expected: 全部 PASS（原 36 + 新增 ≥14）。

- [ ] **Step 2: 性能基准**

Run: `./.venv/Scripts/python.exe /tmp/bench_pack.py`
Expected: 四场景总耗时保持秒级以下（原 0.01-0.34s；混装场景若并入混合布局不应超过 1s）。

- [ ] **Step 3: 前端回归（契约不变）**

Run: `npm.cmd test -- --run && npm.cmd run build`
Expected: 13 个测试通过、build 成功。

- [ ] **Step 4: 更新 HANDOFF.md**

在"当前功能"（第 2 节）新增一条：混装订单中可上托散箱叠放于整托顶面（整托在下、散装在上），整托带集中柜长中间、散箱补两头；易碎散箱与 `max_top_load_g=0` 托盘不参与混合堆叠；三方案均适用。更新"算法和正确性边界"（第 6 节）与"测试现状"（第 9 节）相应描述。

- [ ] **Step 5: 提交**

```bash
git add docs/HANDOFF.md
git commit -m "docs: update handoff for pallet mixed stacking and centering"
```

---

## Self-Review 记录（计划自审）

- 规格覆盖：需求 2（散箱叠整托）→ Task 1/2/3；需求 1（托盘居中）→ Task 4/5；测试计划 → Task 1-5 内嵌；契约不变/前端零改动 → Task 6 Step 3；范围外（易碎/0 承重不上托）→ Task 2 测试。
- 无占位符：所有步骤含真实测试与实现代码。
- 类型一致性：`CompositeUnit` 属性（Task 2）被 Task 3（`_expand_stacks`）、Task 4（`_mixed_balance_layout`）、Task 5（`_high_fill_candidate`/`pack_order`）一致引用；`_merge_pallet_cartons`/`_mixed_balance_layout`/`_expand_stacks` 签名在 Task 2/4/5 中一致。
