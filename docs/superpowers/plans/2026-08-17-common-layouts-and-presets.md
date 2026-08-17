# 常见装柜布局与预制规格实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将客户三 SKU、四 SKU、五 SKU 常见整托案例纳入底层排组算法，并增加不影响自定义录入的常见规格预制入口。

**架构：** 后端在现有 `StackUnit`、`PackedStack` 和 `validate_solution()` 之上增加通用底层排组候选，按横纵朝向枚举同 SKU 整排和余托混合排；前端使用静态预制目录加载货物清单，不新增模板 API。前两个案例的重量使用 `null` 表示未填，未补齐重量时禁止计算。

**技术栈：** Python 3.12、FastAPI、pytest、`rectpack`、React 19、TypeScript、Vitest、Testing Library。

---

## 文件清单

- 创建：`backend/tests/test_common_layouts.py`
  - 三 SKU、四 SKU、五 SKU 纯整托案例和底层混合排回归。
- 修改：`backend/app/packing.py`
  - 通用底层排组候选、横纵朝向枚举、余托混合排和候选排序。
- 修改：`backend/tests/test_floor_first_layout.py`
  - 保留 A/B/C 的 PB-PA-PB-PC 规则，补充与通用候选的兼容回归。
- 修改：`frontend/src/types.ts`
  - 支持预制项重量未填状态，新增预制项类型。
- 创建：`frontend/src/lib/cargoPresets.ts`
  - 3 个组合预制项和 12 个单品预制项。
- 创建：`frontend/src/lib/cargoPresets.test.ts`
  - 预制目录数量、字段和克隆隔离测试。
- 修改：`frontend/src/lib/cargo.ts`
  - 预制货物克隆、未填重量校验和普通货物默认值兼容。
- 修改：`frontend/src/components/CargoTable.tsx`
  - 常见规格入口、预制加载回调和重量未填显示。
- 修改：`frontend/src/App.tsx`
  - 预制项加载、替换确认、计算按钮禁用和错误提示。
- 修改：`frontend/src/App.test.tsx`
  - 组合加载、未填重量阻止计算、自定义新增和替换确认回归。
- 修改：`docs/HANDOFF.md`
  - 算法规则、常见组合和预制入口交接说明。

## 任务 1：先写后端失败测试

**文件：**

- 创建：`backend/tests/test_common_layouts.py`
- 修改：`backend/tests/test_floor_first_layout.py`

- [ ] **步骤 1：创建 4 SKU 和 5 SKU 测试夹具**

在 `test_common_layouts.py` 中增加 40HQ 工厂方法和两个请求工厂。算法测试
使用明确的测试重量，避免把预制项的未填重量状态混入物理算法测试：

```python
from collections import defaultdict


def pallet_request(items: list[CargoSpec]) -> PackRequest:
    return PackRequest(container=forty_hq(), cargo_items=items)


def pallet(
    cargo_id: str,
    sku: str,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    weight_kg: int,
    quantity: int,
    *,
    stackable: bool,
) -> CargoSpec:
    return CargoSpec(
        id=cargo_id,
        sku=sku,
        name=sku,
        kind="pallet",
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        weight_g=weight_kg * 1000,
        quantity=quantity,
        allowed_orientations=["LWH", "WLH"],
        stackable=stackable,
        max_layers=2 if stackable else 1,
        max_top_load_g=500_000,
        fragile=False,
        must_load=False,
    )


def four_sku_request() -> PackRequest:
    return pallet_request(
        [
            pallet("p1", "P1", 760, 760, 1000, 100, 18, stackable=True),
            pallet("p2", "P2", 1150, 1150, 1100, 150, 8, stackable=True),
            pallet("p3", "P3", 1100, 1100, 1000, 140, 12, stackable=True),
            pallet("p4", "P4", 1050, 1050, 800, 120, 12, stackable=True),
        ]
    )


def five_sku_request() -> PackRequest:
    return pallet_request(
        [
            pallet("q1", "Q1", 700, 700, 1100, 100, 22, stackable=True),
            pallet("q2", "Q2", 900, 750, 1080, 120, 25, stackable=True),
            pallet("q3", "Q3", 1080, 800, 1050, 140, 5, stackable=True),
            pallet("q4", "Q4", 1000, 800, 1000, 130, 1, stackable=False),
            pallet("q5", "Q5", 1220, 920, 1180, 180, 2, stackable=False),
        ]
    )
```

两层测试货物设置 `max_layers=2`、`max_top_load_g=500_000`，
不可叠货物设置 `max_layers=1`、`stackable=False`。

- [ ] **步骤 2：增加数量和物理校验失败测试**

运行前先写出以下断言，预期当前通用实现至少有一项失败：

```python
def test_four_sku_case_loads_all_fifty_pallets():
    response = pack_order(four_sku_request())
    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [50] * 4
    assert all(validate_solution(...).valid for solution in response.solutions)


def test_five_sku_case_loads_all_fifty_five_pallets():
    response = pack_order(five_sku_request())
    assert [solution.metrics.loaded_pieces for solution in response.solutions] == [55] * 4
    assert all(validate_solution(...).valid for solution in response.solutions)
```

- [ ] **步骤 3：增加底层排组和同规格叠放断言**

测试应验证：

```python
def supports_for(elevated, bottom):
    return [
        support
        for support in bottom
        if support.cargo_id == elevated.cargo_id
        and support.x_mm < elevated.x_mm + elevated.length_mm
        and support.x_mm + support.length_mm > elevated.x_mm
        and support.y_mm < elevated.y_mm + elevated.width_mm
        and support.y_mm + support.width_mm > elevated.y_mm
        and support.z_mm + support.height_mm == elevated.z_mm
    ]


def max_row_width(cargo_ids, solution):
    rows = defaultdict(list)
    for placement in solution.placements:
        if placement.z_mm == 0 and placement.cargo_id in cargo_ids:
            rows[placement.step].append(placement)
    return max(sum(item.width_mm for item in row) for row in rows.values())


def test_five_sku_uses_mixed_remainder_rows_and_door_order():
    response = pack_order(five_sku_request())
    for solution in response.solutions:
        bottom = [placement for placement in solution.placements if placement.z_mm == 0]
        upper = [placement for placement in solution.placements if placement.z_mm > 0]
        assert len(bottom) > len(upper)
        for elevated in upper:
            supports = supports_for(elevated, bottom)
            assert supports
            assert all(support.cargo_id == elevated.cargo_id for support in supports)
        assert max_row_width({"q3", "q4"}, solution) <= 2352
        assert max_row_width({"q2", "q5"}, solution) <= 2352
        assert max_row_width({"q1", "q5"}, solution) <= 2352
```

测试还要确认最靠近柜门的混合排包含 Q1 和 Q5，且所有 placement 的右边界
不超过 `inner_length_mm - door_buffer_mm`。

- [ ] **步骤 4：运行后端新增测试，确认红灯**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_common_layouts.py -q --basetemp C:\tmp\packing-pytest-red
```

预期：测试因当前 4/5 SKU 候选未覆盖客户混合排规则而失败，而不是因为导入错误。

## 任务 2：实现通用底层排组算法

**文件：**

- 修改：`backend/app/packing.py`
- 测试：`backend/tests/test_common_layouts.py`

- [ ] **步骤 1：增加内部排组数据结构**

在 `PackedStack` 附近增加只用于候选生成的不可变结构：

```python
@dataclass(frozen=True)
class FloorBand:
    cargo_ids: tuple[str, ...]
    placements: tuple[PackedStack, ...]
    length_mm: int
    width_mm: int
    step: int
    mixed: bool
```

该结构不进入 API。最终仍转换为 `PackedStack`，再使用现有
`_expand_stacks()` 生成 `Placement`。

- [ ] **步骤 2：实现朝向 footprint 枚举**

新增：

```python
def _floor_orientations(
    request: PackRequest,
    unit: StackUnit,
) -> list[tuple[Orientation, int, int]]:
    """返回 (朝柜长尺寸、横跨柜宽尺寸、旋转标记) 的合法候选。"""
```

候选必须满足柜门宽度、柜门高度、柜体安全边距和 `item_gap_mm`。
相同 footprint 去重，排序固定为：

1. 横向可容纳列数更多；
2. 沿柜长尺寸更短；
3. 原始朝向优先。

- [ ] **步骤 3：实现主体整排与余托混合排**

新增：

```python
def _mixed_floor_band_layout(
    request: PackRequest,
    units: list[StackUnit],
    strategy: Literal["fill", "stable", "easy", "strict"],
) -> list[PackedStack] | None:
    """生成 3 至 5 个纯整托 SKU 的底层排组布局。"""
```

实现步骤：

1. 按 SKU 汇总数量和单托 footprint。
2. 为每个 SKU 计算在每种朝向下的横向列数和主体整排数量。
3. 先保留可形成完整整排的主体货位。
4. 对每个 SKU 剩余货位，枚举两个 SKU 的横向混合排；
   只有横向尺寸之和加间隙不超过柜宽的组合才保留。
5. 对排组进行确定性排列，默认从柜头到柜门按主体区、混合余托区排序。
6. 为每个排组计算累计柜长，超出
   `inner_length_mm - door_buffer_mm - clearance_mm` 的候选直接丢弃。
7. 将剩余可叠件从柜长中心向两侧放到同 SKU 底位上；
   不可叠、易碎或承重不足的货物不生成上层。
8. 调用 `validate_solution()`，失败候选不返回。

客户五 SKU 案例的排组优先级为：

```text
Q3 + Q4
Q2 + Q5（横放）
Q1 + Q5（纵放）
```

如果朝向枚举找到更短且满足同一规则的方案，允许使用更短方案，
但不得跨 SKU 叠放。

- [ ] **步骤 4：接入纯整托布局选择链**

在 `_pure_pallet_floor_first_layout()` 中，将通用排组候选放在现有
`_same_sku_band_layout()` 之后、通用 `rectpack` 候选之前：

```python
band_layout = _same_sku_band_layout(...)
if band_layout is not None:
    return band_layout

mixed_layout = _mixed_floor_band_layout(request, list(by_cargo.values()), strategy)
if mixed_layout is not None:
    return mixed_layout
```

只有纯整托且 SKU 数量为 3 至 5 时进入该路径，散箱和混装继续走原有路径。

- [ ] **步骤 5：运行后端新增测试，确认绿灯**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_common_layouts.py backend\tests\test_floor_first_layout.py -q --basetemp C:\tmp\packing-pytest-green
```

预期：新案例全部通过，原有 A/B/C 63 托、4 个 profile、上层连续和同规格支撑
回归保持通过。

## 任务 3：先写前端预制目录和重量状态失败测试

**文件：**

- 创建：`frontend/src/lib/cargoPresets.test.ts`
- 修改：`frontend/src/App.test.tsx`
- 修改：`frontend/src/types.ts`

- [ ] **步骤 1：增加预制项类型和红灯断言**

预制目录类型固定为：

```typescript
export interface CargoPreset {
  id: string;
  label: string;
  kind: "组合" | "单品";
  containerHint: string;
  description: string;
  items: Array<Omit<CargoInput, "id">>;
}
```

重量未填使用 `weight_kg: null`。先写以下测试：

```typescript
test("contains three combinations and twelve single-product presets", () => {
  expect(COMMON_CARGO_PRESETS.filter((item) => item.kind === "组合")).toHaveLength(3);
  expect(COMMON_CARGO_PRESETS.filter((item) => item.kind === "单品")).toHaveLength(12);
});

test("clones preset rows with fresh ids", () => {
  const first = cloneCargoPreset(COMMON_CARGO_PRESETS[0]);
  const second = cloneCargoPreset(COMMON_CARGO_PRESETS[0]);
  expect(first.map((row) => row.id)).not.toEqual(second.map((row) => row.id));
});
```

在 `App.test.tsx` 增加：加载四 SKU 组合后，重量未填时生成按钮不可用，
补齐重量后恢复可用。

- [ ] **步骤 2：运行前端测试，确认红灯**

运行：

```powershell
npm.cmd test -- --run frontend/src/lib/cargoPresets.test.ts frontend/src/App.test.tsx
```

预期：因预制目录、`null` 重量类型和加载入口不存在而失败。

## 任务 4：实现前端预制目录和安全加载

**文件：**

- 创建：`frontend/src/lib/cargoPresets.ts`
- 修改：`frontend/src/types.ts`
- 修改：`frontend/src/lib/cargo.ts`

- [ ] **步骤 1：实现预制目录**

目录必须包含：

- A/B/C 组合，使用真实重量和测试顶部承重 500 kg；
- 四 SKU 组合，尺寸和数量为 18/8/12/12，重量为 `null`；
- 五 SKU 组合，尺寸和数量为 22/25/5/1/2，重量为 `null`；
- 12 个单品规格，数量沿用对应客户案例，重量状态与来源一致。

四 SKU 叠放设置为全部可叠 2 层；五 SKU 中 Q1/Q2/Q3 可叠 2 层，
Q4/Q5 不可叠。所有整托的顶部承重默认显示 500 kg，并在说明中提示按真实数据复核。

- [ ] **步骤 2：实现克隆函数**

```typescript
let presetSequence = 1;

function nextPresetSequence(): number {
  return presetSequence++;
}

export function cloneCargoPreset(preset: CargoPreset): CargoInput[] {
  return preset.items.map((item) => ({
    ...item,
    id: `cargo_${Date.now()}_${nextPresetSequence()}`,
  }));
}
```

每次加载都创建新对象和新 ID，不能直接把静态目录数组传给 React 状态。

- [ ] **步骤 3：扩展未填重量校验**

将 `CargoInput.weight_kg` 改为 `number | null`，普通新建货物仍使用原来的默认重量。
`validateCargo()` 新增：

```typescript
if (rows.some((row) => row.weight_kg == null || row.weight_kg <= 0)) {
  return "请先补充所有货物的单托重量";
}
```

`packOrder()` 只在通过校验后调用，保持后端请求字段为正数克数，
不把 `null` 转换成伪造重量。

- [ ] **步骤 4：运行前端测试，确认目录和校验通过**

运行：

```powershell
npm.cmd test -- --run frontend/src/lib/cargoPresets.test.ts frontend/src/App.test.tsx
```

预期：预制目录数量、ID 隔离和未填重量阻止计算测试通过。

## 任务 5：实现预制入口并保护自定义录入

**文件：**

- 修改：`frontend/src/components/CargoTable.tsx`
- 修改：`frontend/src/App.tsx`
- 修改：`frontend/src/App.test.tsx`

- [ ] **步骤 1：扩展 `CargoTable` 回调**

增加：

```typescript
interface Props {
  rows: CargoInput[];
  onChange: (rows: CargoInput[]) => void;
  onLoadPreset?: (preset: CargoPreset) => void;
  // 保留已有导入和下载回调
}
```

在货物清单标题区域增加「常见产品规格」按钮和分组菜单。
菜单只展示预制项元数据，不直接修改行数据。

- [ ] **步骤 2：实现替换确认**

在 `App.tsx` 中：

```typescript
const loadPreset = (preset: CargoPreset) => {
  if (cargoItems.length > 0 && !window.confirm("加载常见规格将替换当前货物清单，是否继续？")) {
    return;
  }
  setCargoItems(cloneCargoPreset(preset));
  setResult(null);
  setError(null);
};
```

清单为空时直接加载；加载后用户可以继续编辑和添加产品。
预制项选择不改变柜型、间隙或安全边距。

- [ ] **步骤 3：显示重量状态和计算门槛**

重量为 `null` 的输入框显示空值和辅助文本「需补充重量」。
计算按钮使用 `validateCargo(cargoItems)` 的结果决定是否禁用；
点击按钮时仍保留原有错误提示作为第二道保护。

- [ ] **步骤 4：运行前端回归**

运行：

```powershell
npm.cmd test -- --run
```

预期：预制加载、替换确认、整托默认值、四方案展示、换柜重算和原有输入测试全部通过。

## 任务 6：更新交接文档和执行质量检查

**文件：**

- 修改：`docs/HANDOFF.md`

- [ ] **步骤 1：补充当前实现事实**

更新交接文档，写明：

- 通用底层排组支持 3/4/5 SKU 纯整托案例；
- 四 SKU 的 76 × 76 × 100 笔误已修正；
- 五 SKU 的三个余托混合排和柜门顺序；
- 前两个案例重量未填时不能计算；
- 预制目录是前端静态数据，不影响自定义录入和其他用户。

- [ ] **步骤 2：运行完整验证**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q --basetemp C:\tmp\packing-pytest-final
npm.cmd test -- --run
npm.cmd run build
python -m compileall backend
git diff --check
```

预期：后端、前端、构建和编译全部退出码为 0；临时目录不加入 Git。

- [ ] **步骤 3：生成并检查发布包**

构建通过后，确认发布内容同时包含：

```text
backend/app
frontend/dist
```

不上传 `docs/`、测试临时目录、SSH 凭据或用户本地草稿。

- [ ] **步骤 4：部署后验证**

重启生产服务后执行：

```powershell
curl.exe -fsS https://packing.xingshuwen.com/health
curl.exe -fsS https://packing.xingshuwen.com/api/v1/container-presets
```

再提交一次真实 A/B/C `POST /api/v1/pack`，确认返回 3 个方案，
并确认首页静态资源与前端预制入口可加载。

## 提交顺序

按以下粒度提交，方便回滚：

1. `test(算法): 增加四五 SKU 常见布局回归`
2. `feat(算法): 增加横纵朝向和混合底层排组`
3. `test(前端): 增加常见规格预制与重量门槛测试`
4. `feat(前端): 增加常见产品规格预制入口`
5. `docs(交接): 更新常见布局和预制功能说明`
