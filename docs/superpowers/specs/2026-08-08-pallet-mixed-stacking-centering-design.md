# 设计文档：整托混合堆叠与混装订单整托居中

日期：2026-08-08
状态：已获用户认可（三方案托盘带居中、易碎散箱第一版不上托）

## 1. 背景与目标

装柜方案助手当前生成三个方案（装得多 / 更稳妥 / 易操作），布局模型为"单一 SKU 竖直货栈 + 二维平面排布"：整托与散箱各自作为独立货栈**并排占据柜底**，不存在"散箱叠在整托上"的混合堆叠。

用户提出两个业务需求（重点针对混装订单，即整托与散箱同单）：

1. **整托重量集中中间，两头别偏重**：混装订单布局中，整托（及其上叠散箱）应集中在柜长方向中间区域，散箱独立栈补两头。
2. **整托在下、散装在上**：可上托的散箱实际堆叠在整托顶面（整托打底），而非与整托并排占底面。

已确认的设计决策：

- 三个方案（high_fill / stable / easy）都采用"托盘带居中 + 散箱补两头"的布局基调。
- 易碎散箱第一版**不上托**，保持独立栈（后续可扩展）。
- 托盘 `max_top_load_g = 0`（不可承重）时散箱不上托。
- 本次不做：此前诊断出的 3 个高优先级正确性 bug（stable 回退、rectpack 门宽、前端 layerIndex 越界）延后处理。

## 2. 现状模型回顾

- `StackUnit`（`backend/app/packing.py:40`）：单一 SKU 的竖直货栈（count 件、可旋转、含高度/重量），是排布的最小单位。
- `_build_stack_units`（`packing.py:155`）：按柜高、朝向、堆叠约束为每个 cargo 生成一个或多个 `StackUnit`。
- `_select_payload_units`（`packing.py:205`）：按重量策略筛选/削减栈，保证不超柜载重。
- `_pack_units`（`packing.py:251`）：用 rectpack 把栈二维排进柜底（`MaxRectsBssf`/`MaxRectsBaf`/`GuillotineBssfSas`）。
- `_pallet_grid_layout`（`packing.py:508`）：纯整托订单按重量从柜中心向外网格配平（现有"整托集中中间"的纯整托实现）。
- `_expand_stacks`（`packing.py:601`）：把栈展开为逐件 `Placement`（z = clearance + offset×件高）。
- `validate_solution`（`backend/app/validator.py:39`）：独立校验器，支持逐件叠放关系（支撑、完整支撑、顶部承重、层数、不可叠/易碎）。

## 3. 设计

### 3.1 复合排布单位 CompositeUnit（需求 2 核心）

新增 dataclass `CompositeUnit`（`packing.py`）：

```python
@dataclass(frozen=True)
class CompositeUnit:
    pallet: StackUnit          # count=1 的整托
    on_top: list[StackUnit]    # 分配在托盘顶面的散箱栈（有序）
```

为复用现有 rectpack/排序/选优代码，提供鸭子类型属性：

- `id` = pallet.id
- `length_mm` / `width_mm` = pallet 的 footprint
- `total_weight_g` = pallet.total_weight_g + Σ(on_top 栈重)
- `count` = pallet.count + Σ(on_top 件数)
- `volume_mm3` = 各部件体积之和
- `cargo` = pallet.cargo（区域分组用）
- `required` = pallet.required
- `stack_height_mm` = pallet.stack_height_mm + max(on_top 栈高, 0)

**上托筛选条件**（散箱栈 → 托盘）：
1. 散箱 `kind == "carton"` 且非 `fragile`。
2. 托盘 `max_top_load_g > 0`。
3. 散箱栈 footprint（含其允许朝向）旋转后能放入托盘顶面（用 rectpack 在托盘顶面 bin 中排布验证）。
4. 托盘 `stack_height_mm` + 散箱栈 `stack_height_mm` ≤ 柜内有效高（`inner_height - 2×clearance`）。
5. 上叠总重 ≤ 托盘 `max_top_load_g`（合并时按托盘逐台分配时校验）。

**合并流程** `_merge_pallet_cartons(request, units) -> list[CompositeUnit | StackUnit]`：

1. 分离 `pallets`（kind=pallet 的 unit）与 `cartons`（其余）。
2. 按托盘顺序（重量降序、id 决胜，确定性）逐个托盘：用 rectpack（固定算法与排序，可复现）在托盘顶面 bin（托盘 length×width）中排布仍可上托的散箱栈；放不下的栈保留在待分配列表。
3. 每次分配前检查：托盘剩余承重 ≥ 栈重、托盘高+栈高 ≤ 柜内高、栈 footprint 可放入顶面剩余空间。
4. 输出：有上叠散箱的托盘 → `CompositeUnit`；其余单位原样返回。
5. 纯散箱/纯整托订单不产生复合单位，行为与现状一致。

**调用时机**：在 `_select_payload_units`（重量筛选）之后、`_pack_units`（二维排布）之前调用。`_high_fill_candidate` 的 4 种 payload 策略各合并一次。

**展开**（`_expand_stacks` 增加分支，`packing.py:628` 附近）：

- 托盘件：`z = clearance`，`step` 与复合单位一致。
- 上叠散箱件：`z = clearance + pallet.stack_height_mm + offset×散箱件高`，`step` 与托盘相同（一个装载位置 = 一个步骤）。
- 尺寸/朝向沿用各自 `StackUnit`。

### 3.2 整托集中中间：`_mixed_balance_layout`（需求 1）

新增 `_mixed_balance_layout(request, units) -> list[PackedStack] | None`，处理"含复合单位"的订单（无托盘或纯整托订单分别走现有 `_pack_units` / `_pallet_grid_layout` 路径）：

1. **托盘带网格排布**：复合单位与独立托盘按重量从中心向外排成网格（复用 `_pallet_grid_layout` 的 row_order/column_weights 逻辑，见 `packing.py:561-578`），网格整体居中放置：
   - `x0 = c + (usable_length - total_length) // 2`。
   - 行内按重量从中心列向外、列内按累计最轻列分配。
   - 同一排内以重量降序确定位置，保证重托盘居中。
2. **两端散箱区**：托盘带前后剩余长度各形成一个 rectpack bin（长度 = 端部剩余空间，宽度 = usable_width），排入未上托的散箱独立栈（从地面叠起，高度受柜高约束）。
3. 托盘带放不下的复合单位/托盘 → 排入两端散箱区（先散箱后托盘）或布局失败返回 `None`（由调用方回退现有逻辑）。
4. 整体 `_center_stacks`（`packing.py:368`）居中一次；`step` 按托盘带行序 + 端部区域顺序赋值（可复现）。

**接线**：

- `pack_order`（`packing.py:1079`）：high_fill 候选集合中加入 `_mixed_balance_layout` 布局参与选优（保证装载率不塌）；stable 强制使用 `_mixed_balance_layout`（失败回退现有 `_repack_same_units` 路径）；easy 区域化布局中托盘带居中、两端散箱按 SKU 分带（复用 `_band_layout`/`_shelf_layout` 思路处理复合单位，托盘 SKU 带排中间、散箱带补两端）。
- 保持"stable 保持 high_fill 同一批货物"的既有语义（`selected_counts` 重建逻辑不变，本设计不触碰 `quantity_limits` 回退问题）。

### 3.3 校验器托盘特判（validator.py）

`validate_solution` 的支撑物检查（`validator.py:162-179`）针对 `kind == "pallet"` 的支撑物：

- 上方**存在整托** → 新增错误码 `PALLET_STACKING`（"整托上方不能叠放整托"）。
- 跳过 `max_layers` 层数检查（托盘 `max_layers` 语义是托盘自身层数，不约束上方散箱层数；散箱层数由散箱自身校验）。
- 保留：`stackable`/`fragile` 检查对托盘不适用（托盘可被散箱上叠、本身非易碎），改为仅对非托盘支撑物执行。
- 保留：上方散箱总重 ≤ 托盘 `max_top_load_g`（现有 `TOP_LOAD_EXCEEDED`，`validator.py:177-179`）。
- 保留：完整支撑检查（`UNSUPPORTED`，`validator.py:153-160`）——散箱底面必须被托盘顶面完整覆盖，合并逻辑已保证 footprint 在托盘顶面内，校验器天然覆盖。

### 3.4 契约与前端

- `Placement`/`PackingSolution`/`zones`/`metrics` 结构不变；`POST /api/v1/pack` 响应契约不变。
- 前端（`LoadVisualizer` 3D/2D、`SolutionWorkspace` 打印）零改动：散箱 `z` 起点高于托盘顶面即自然呈现"整托在下、散箱在上"。
- `_compute_zones` 按 (cargo_id, step) 连续块合并：托盘与其上叠散箱 step 相同，但 cargo_id 不同，各自成区；区域说明照常标注。

## 4. 错误处理

- `_mixed_balance_layout` 失败返回 `None`，由 `pack_order` 回退现有布局路径（`_repack_same_units` 等），不新增错误码。
- 合并阶段承重/高度/朝向不满足 → 该散箱栈保留为独立栈，不报错、不丢弃货物。
- 现有 `INTERNAL_INVALID_LAYOUT`（`_build_solution` 校验失败）保护机制不变：任何候选必须再次通过 `validate_solution` 才返回。

## 5. 测试计划

`backend/tests/test_packing.py` 扩展（并新增 `backend/tests/test_mixed_stacking.py`）：

1. 混装订单（托盘 + 可叠散箱）：存在散箱 placement 的 `z_mm = clearance + 托盘高 + offset×散箱高`；散箱底面被托盘完整支撑（validator 通过）；托盘上无托盘；上叠总重 ≤ 托盘 `max_top_load_g`。
2. 混装订单 stable：所有整托质心落在柜长中段（`[L/3, 2L/3]`）；`length_imbalance_pct` 优于未居中的对照；三方案件数契约（stable 件数 = high_fill 件数）保持。
3. 纯整托订单：走 `_pallet_grid_layout`，行为与现有测试一致（回归）。
4. 纯散箱订单：不产生复合单位，行为与现有测试一致（回归）。
5. 易碎散箱：不上托，全部独立栈（回归 + 新增断言）。
6. 托盘 `max_top_load_g = 0`：不上托，不报错。
7. validator：`PALLET_STACKING`（托盘上叠托盘报错）、托盘上叠散箱合法、上叠超承重报错。
8. 性能回归：大订单（30 SKU / 5000 件）总耗时 < 15s（现为 0.3s 级，应无退化）。

前端测试：契约不变，无需新增；跑全量回归确认无破坏。

## 6. 范围外（本次不做）

- 3 个高优先级正确性 bug（延后）。
- 易碎散箱上托（第一版不上托，数据结构预留 `on_top` 支持未来扩展）。
- 多 SKU 混放同一托盘顶面的优化分配（第一版贪心按托盘顺序分配）。
- 自动码托（托盘内部码放）、跨箱搭接等仍不在模型内。

## 7. 验证方式

实施后执行：

```powershell
./.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp /tmp/pytest-zt
./.venv/Scripts/python.exe /tmp/bench_pack.py   # 性能基准（四场景）
npm.cmd test -- --run
npm.cmd run build
```

并手工用浏览器（390px 手机/平板/桌面）验证混装订单三方案的 3D/俯视/分层图、打印报告：托盘在下、散箱在上、托盘集中中间。
