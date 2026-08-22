# 三方案契约收敛实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将正式装柜方案从四套收敛为三套，并统一常见案例标题。

**架构：** 保留 `high_fill`、`stable`、`easy` 三个正式 profile。底层优先、同规格支撑和上层中部连续性继续作为所有正式方案的共同安全规则；`strict_support` 不再出现在正式 API 响应中。

**技术栈：** FastAPI/Pydantic、Python pytest、React/TypeScript、Vitest、Vite、systemd/Uvicorn。

---

### 任务 1：建立失败回归测试

**文件：**
- 修改：`backend/tests/test_api.py`
- 修改：`backend/tests/test_floor_first_layout.py`
- 修改：`backend/tests/test_sku_blocks.py`
- 修改：`backend/tests/test_large_order.py`
- 修改：`frontend/src/App.test.tsx`
- 修改：`frontend/src/components/SolutionWorkspace.test.tsx`

- [x] **步骤 1：** 将正式响应数量断言从 4 改为 3，profile 顺序断言改为 `high_fill`、`stable`、`easy`，标题断言移除「底层优先」。
- [x] **步骤 2：** 将 A/B/C 标题断言改为「三 SKU 案例」，并保留四 SKU、五 SKU标题。
- [x] **步骤 3：** 运行相关测试，确认当前实现因仍返回四套方案而失败。

### 任务 2：收敛后端方案契约

**文件：**
- 修改：`backend/app/packing.py`
- 修改：`backend/app/models.py`

- [x] **步骤 1：** 保留 `high_fill`、`stable`、`easy` 的候选生成和评分路径。
- [x] **步骤 2：** 从 `pack_order()` 的正式 `solutions` 列表移除 `strict_support`，并删除其正式方案构建分支。
- [x] **步骤 3：** 保留底层优先、同规格叠放、上层连续性和物理校验作为所有方案共同规则。
- [x] **步骤 4：** 更新返回模型 profile 类型和接口说明，使正式响应只允许三个 profile。
- [x] **步骤 5：** 运行后端回归测试，确认三方案契约和物理布局都通过。

### 任务 3：同步前端类型、展示和标题

**文件：**
- 修改：`frontend/src/types.ts`
- 修改：`frontend/src/components/SolutionWorkspace.tsx`
- 修改：`frontend/src/App.tsx`
- 修改：`frontend/src/App.test.tsx`
- 修改：`frontend/src/components/SolutionWorkspace.test.tsx`
- 修改：`frontend/src/lib/cargoPresets.ts`

- [x] **步骤 1：** 从 `SolutionProfile`、标题映射、指标映射和测试夹具移除 `strict_support`。
- [x] **步骤 2：** 保持方案卡片动态遍历 `response.solutions`，不添加前端推导布局逻辑。
- [x] **步骤 3：** 将 A/B/C 预制项标题改为「三 SKU 案例」。
- [x] **步骤 4：** 运行前端测试和构建。

### 任务 4：更新交接文档并验收

**文件：**
- 修改：`docs/HANDOFF.md`
- 修改：`docs/superpowers/specs/2026-08-17-common-layouts-and-presets-design.md`
- 修改：`docs/superpowers/plans/2026-08-17-common-layouts-and-presets.md`

- [x] **步骤 1：** 将正式方案、API 示例、测试说明和生产验收说明统一改为三套。
- [x] **步骤 2：** 将案例标题统一为「三 SKU 案例」「四 SKU 案例」「五 SKU 案例」。
- [x] **步骤 3：** 运行后端全量测试、前端全量测试、构建、编译和 `git diff --check`。
- [x] **步骤 4：** 部署 backend/frontend dist，验证生产健康接口、首页资源和 A/B/C 真实请求返回三套方案。
- [x] **步骤 5：** 提交并推送 GitHub，更新现有 Pull Request。
