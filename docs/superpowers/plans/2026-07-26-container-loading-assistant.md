# 装柜方案助手实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务实现；所有业务行为遵循测试驱动开发。

**目标：** 建成可公开试用、支持真实订单录入、三方案计算和 3D/2D 布局查看的“装柜方案助手”。

**架构：** React/TypeScript 单页前端负责录入、Excel、本地草稿、结果可视化和打印；FastAPI 无状态后端负责候选布局、严格校验、评分和可解释结果。前端构建后由 FastAPI 同源托管，形成单容器部署物。

**技术栈：** React 19、Vite、TypeScript、Three.js、Vitest、FastAPI、Pydantic、pytest、httpx。

---

### 任务 1：后端领域模型与严格校验器

**文件：**
- 创建：`backend/app/models.py`
- 创建：`backend/app/validator.py`
- 创建：`backend/tests/test_validator.py`

- [ ] 先写失败测试，覆盖柜内边界、碰撞、总重、方向、柜门、完整支撑、最大层数、顶部承重和必装统计。
- [ ] 运行 `pytest backend/tests/test_validator.py -q`，确认因模块不存在而失败。
- [ ] 实现整数毫米/克领域模型和 `validate_solution()`；违规返回结构化错误码。
- [ ] 重跑测试，确认全部通过。

### 任务 2：确定性候选生成器、三方案评分与 API

**文件：**
- 创建：`backend/app/packing.py`
- 创建：`backend/app/main.py`
- 创建：`backend/tests/test_packing.py`
- 创建：`backend/tests/test_api.py`

- [ ] 先写失败测试，覆盖精确贴合、混合箱托、超量、必装失败、确定性和三个方案结构。
- [ ] 运行目标测试，确认失败原因为实现缺失。
- [ ] 实现基于空间分割/极值点的确定性启发式候选生成，所有结果必须通过任务 1 校验器。
- [ ] 生成 high_fill、stable、easy 三种排序候选；优缺点只来自指标差值。
- [ ] 实现 `/health`、`/api/v1/container-presets`、`/api/v1/pack` 和统一错误响应。
- [ ] 运行 `pytest backend/tests -q`，确认全部通过。

### 任务 3：响应式录入与结果工作区

**文件：**
- 创建：`frontend/src/App.tsx`
- 创建：`frontend/src/components/CargoTable.tsx`
- 创建：`frontend/src/components/ContainerPicker.tsx`
- 创建：`frontend/src/components/SolutionWorkspace.tsx`
- 创建：`frontend/src/lib/api.ts`
- 创建：`frontend/src/**/*.test.tsx`

- [ ] 先写失败的组件测试，覆盖柜型选择、货物增删、校验、API 请求和三方案切换。
- [ ] 运行 `npm.cmd test -- --run`，确认失败。
- [ ] 实现单页两步式流程、移动端三段方案切换和可量化优缺点。
- [ ] 本地草稿只写浏览器存储，服务器请求不包含原始 Excel 文件。
- [ ] 重跑前端测试，确认全部通过。

### 任务 4：3D/2D 布局、Excel、打印与部署

**文件：**
- 创建：`frontend/src/components/LoadVisualizer.tsx`
- 创建：`frontend/src/lib/excel.ts`
- 创建：`frontend/src/styles.css`
- 创建：`Dockerfile`
- 创建：`README.md`

- [ ] 先写失败测试，覆盖 Excel 字段映射、视图模式和装载步骤筛选。
- [ ] 实现 Three.js 3D、俯视/侧视/柜门/分层视图，所有视图共享 API 坐标。
- [ ] 实现固定模板 Excel 导入、示例模板下载和打印/PDF 样式。
- [ ] 前端构建产物由 FastAPI 托管，补齐 Docker 健康检查和启动文档。
- [ ] 运行后端测试、前端测试、类型检查和生产构建。
- [ ] 启动本地服务，用 Playwright 验证 390px、768px、1440px，检查画布非空、无重叠、交互和打印视图。

### 验收命令

```powershell
& '<bundled-python>' -m pytest backend/tests -q
npm.cmd test -- --run
npm.cmd run build
docker build -t container-loading-assistant .
```

预期：所有测试 0 失败，TypeScript 与 Vite 构建退出码为 0，Docker 镜像构建成功。
