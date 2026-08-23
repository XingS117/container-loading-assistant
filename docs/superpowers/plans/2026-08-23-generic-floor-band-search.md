# 自定义整托排组搜索实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development` 或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让非预置纯整托组合也能通过通用排组搜索获得底层优先、上层集中且物理安全的装柜候选。

**架构：** 在现有 `_pure_pallet_floor_first_layout()` 的预置模板之后增加通用 floor-band 候选生成。候选只生成底层货位，复用现有同 SKU 中部支撑分配、质量评分、物理校验和回退链。AI 暂不进入本次代码路径，只在文档中固定为未来策略推荐边界。

**技术栈：** Python、FastAPI、Pydantic、`rectpack`、pytest；不新增运行时依赖。

---

### 任务 1：增加非预置自定义组合回归测试

**文件：**
- 修改：`backend/tests/test_packing.py`

- [x] **步骤 1：编写失败测试**

增加一个 4 SKU 纯整托请求：两个可叠 SKU、两个不可叠 SKU，尺寸与预置案例不同但允许两种朝向；断言 3 个方案物理有效、底层件数大于上层件数、上层只有同 SKU 支撑，并且高填充方案的底层 X 区域连续。增加一个 5 SKU 自定义请求，断言重复计算结果一致。

- [x] **步骤 2：运行测试确认当前行为**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py -q --basetemp .\_temp\pytest-generic-red
```

结果：新增测试因 `_generic_floor_band_layout` 尚不存在而失败，证明测试覆盖了当前缺口。

### 任务 2：实现通用底层排组候选

**文件：**
- 修改：`backend/app/packing.py`

- [x] **步骤 1：实现朝向和余数行枚举**

新增内部辅助函数，输入 `by_cargo`、底层件数和朝向选项，输出不超过柜宽且每行至少一个货位的确定性行模式。优先完整 SKU 行，其次按宽度降序合并余数行；所有坐标使用现有 `clearance_mm` 和 `item_gap_mm`。

- [x] **步骤 2：生成柜头到柜门的排组候选**

新增 `_generic_floor_band_layout()`，对有限底层数量组合、朝向和 SKU 顺序生成 floor-only `PackedStack` 列表。候选必须位于 `door_limit` 内，并保留每个 SKU 的底层货位索引供上层复用。

- [x] **步骤 3：复用上层分配和校验**

在 `_pure_pallet_floor_first_layout()` 中将通用 floor-only 候选接入现有评分环节，再沿用同 SKU、中心优先、完整行优先的上层分配。每个候选先调用 `validate_solution()`，失败候选不进入排序。

### 任务 3：质量评分与回归验证

**文件：**
- 修改：`backend/app/packing.py`
- 修改：`backend/tests/test_packing.py`

- [x] **步骤 1：为候选增加确定性排序**

使用现有 `_layout_quality_score()`，保持硬规则优先级不变；同分时按底层跨度、上层中心偏差、SKU 区域切换数和货物 ID 做稳定 tie-break。

- [x] **步骤 2：运行专项测试**

运行新增测试和既有纯整托测试，确认自定义组合不影响 A/B/C、四 SKU、五 SKU 和混装回归。

- [x] **步骤 3：运行完整验证**

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q --basetemp C:\tmp\packing-pytest
npm.cmd test -- --run
npm.cmd run build
git diff --check
```

结果：后端 `117 passed`，前端 `22 passed`，构建退出码为 0，差异检查无错误。

### 任务 4：更新交接记录

**文件：**
- 修改：`docs/HANDOFF.md`

- [x] **步骤 1：记录通用排组能力和 AI 边界**

说明自定义组合现在使用通用排组候选，预置模板仍优先；记录 AI 仅做策略推荐、必须经过后端物理校验且未配置时自动回退。

- [x] **步骤 2：提交实现**

```powershell
git add backend/app/packing.py backend/tests/test_packing.py docs/HANDOFF.md docs/superpowers/specs/2026-08-23-generic-floor-band-search-design.md docs/superpowers/plans/2026-08-23-generic-floor-band-search.md
git commit -m "feat(算法): 增强自定义整托排组搜索"
```

结果：已提交为 `8a716ac`。本次只完成本地实现与验证，未执行生产部署或 GitHub 推送。
