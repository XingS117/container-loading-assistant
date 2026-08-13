# 装柜方案助手：技术交接

最后更新：2026-08-13

## 0. 最新交接摘要

### 当前结论

- 项目已完成试用版开发并部署到 `https://packing.xingshuwen.com`。
- 当前生产服务为服务器上的 `packing-assistant.service`，后端监听 `127.0.0.1:8500`，Nginx 负责 HTTPS 和反向代理。
- GitHub 仓库：`https://github.com/XingS117/container-loading-assistant`，当前主分支包含最新代码。
- 当前工作区唯一无关未跟踪目录是 `.playwright-cli/`，不要为了清理它而删除用户文件；`.reasonix/` 为本地工具会话目录，可忽略。
- 布局为 **SKU 块布局 + 分层铺满 + 互叠高装载** 三套策略组合，**输出 5 个方案**（装得多 / 更稳妥 / 易操作 / 严格完整支撑 / 互叠高装载）。所有方案在返回前都过 `validate_solution()` 独立校验。

### 已完成的最新变更

按时间从新到旧：

- **互叠高装载 + 5 方案**（`b562dfa`）：`PackRequest` 新增 `enable_interstack`（默认开）/ `support_coverage_min`（0.7）/ `overhang_ratio_max`（0.2）；`packing.py` 新增 `_interstack_layout`（在"装得多"基础上把未装入件跨 SKU 叠放到组合支撑平面上）；`validator.py` 的 `UNSUPPORTED` 改为支持覆盖率阈值 + 悬挑比例（默认严格模式向后兼容）；`PackedStack` 增加 `z_mm` 偏移；输出 5 方案、相同布局以 `identical_to` 去重；前端 `SolutionProfile` 扩到 5 值。参考成熟装柜软件"互叠开关"做法。
- **承重层数 +1 修复**（`ad09c56`）：`_build_sku_blocks` 的 `max_by_load` 曾用 `max_top_load // weight`（少 `+1`），把"可叠 2 层"误判成 1 层、整托被强制平铺导致其它 SKU 装不下（用户实测 63 托剩 14 件）。改为与 `_stack_capacity` 一致的 `// + 1`。
- **先铺满底层规则**（`ca9d72d`）：SKU 块布局与分层铺满统一遵循"先把底层铺满、剩余的最上层集中在中间"。`Block` 增加 `flat_rows`（全平铺行数）、`_grow_block_rows` 按柜长预算给块加行（装得多按 columns/length 效率、更稳妥按重量）、块内放置改为底层铺满 + 剩余件集中在距柜长中心近的底位。
- 之前的统计接入（`3bb7689`）、易操作区域优化（`08b4f42`）等见下。

### 已验证

- 后端测试：**83 项通过**（`pytest backend/tests -q`）；Windows 下全量里可能出现 1 个 `.pytest-tmp` 权限 ERROR，用 `--basetemp` 指向可写目录即可通过，非代码缺陷。
- 前端测试：6 个测试文件、**14 项通过**；生产构建通过。
- 生产服务器 `http://127.0.0.1:8500/health` 返回 `{"status":"ok"}`。
- 公网验证（`packing.xingshuwen.com`）：用户场景（65/89/108 三托，150/280/400kg）5 方案全部 63 件装下、字段齐全；互叠价值场景（20 大托+200 小托）互叠高装载 80 件 vs 严格方案 20 件；关闭互叠返回 4 方案；大订单（30 SKU / 5000 件）在 15s 服务预算内。
- 本地 Docker 镜像完整构建曾因 Docker Hub 基础镜像网络连接失败未完成；生产发布使用已有 systemd 服务完成，不代表 Dockerfile 有语法错误。网络恢复后可重试。

### 接手后的第一步

1. 先阅读本摘要，再阅读第 5、6、8 节，了解 API、算法正确性边界和部署方式。
2. 修改前运行 `git status --short`，不要覆盖用户已有改动。
3. 若继续调整算法，先增加 `backend/tests` 场景测试，再改 `backend/app/packing.py` 或 `backend/app/validator.py`，并确认返回布局能再次通过 `validate_solution()`。
4. 若需要重新发布统计配置，从本机 `frontend/.env` 读取配置；不要把它提交到 GitHub，也不要把 SSH 私钥、密码或证书写入仓库。
5. 改动互叠或方案数量时，前后端契约需同步：`backend/app/models.py` 的 `profile Literal`、`frontend/src/types.ts` 的 `SolutionProfile`、`SolutionWorkspace.tsx` 的 `profileShortName` 与渲染分支。

## 1. 项目定位

**装柜方案助手** 是一个面向外贸发货场景的轻量装柜测算试用版，已部署到：

- `https://packing.xingshuwen.com`

用户选择一个集装箱、录入散箱或已码好整托货物后，系统一次生成五个可比较的可行布局：

| 方案 | 目标 |
| --- | --- |
| 装得多 | 尽可能提高有效装载体积、件数（SKU 块布局，体积降序） |
| 更稳妥 | 在保持第一方案货物集合的前提下，降低柜长方向（两头）重心偏差；纯整托订单按重量网格配平，其余做同底面互换优化 |
| 易操作 | 生成整带/整排区域化布局，每个 SKU 尽量集中在连续编号区域并减少装载步骤；密单放不下时允许少装并明确披露，必装货物不删 |
| 严格完整支撑 | 与"装得多"同布局（100% 完整支撑、无悬挑），强调安全裕度，适合船公司/保险审核场景；布局相同自动 `identical_to` 去重 |
| 互叠高装载 | 在"装得多"基础上，把未装入件跨 SKU 叠放到组合支撑平面上（支撑覆盖率 ≥ 0.7、每边悬挑 ≤ 0.2×短边），装载率不低于严格方案 |

这不是数学全局最优求解器。它使用固定排序和确定性启发式，在受限时间内生成高质量、经校验的布局。对外应避免承诺“最优装柜”；实际发货前必须按实际箱单、柜体铭牌和现场条件复核。

## 2. 当前功能

- 单柜计算：20GP、40GP、40HQ 和自定义柜型。
- 散箱与整托混装；整托仅按整体长方体计算，不进行托盘内部码放。
- 核心布局为**SKU 块布局（每 SKU 集中成块）**：每个 SKU 的货物构建一个 Block（块内网格 columns×rows，同 SKU 集中成块），块沿柜长按策略放置；三方案差异：装得多=体积降序（大块靠柜头）、更稳妥=重块居中配平（有卸货顺序时先卸后装优先）、易操作=SKU 输入顺序分步。**所有方案统一遵循"先铺满底层"规则**：在柜长预算内每块尽量平铺（行数从叠层下限趋向全平铺上限），底层铺满后再把剩余件数叠到第 2 层，集中在距柜长中心近的底位（两头低中间高、重心居中）；更稳妥方案重块优先铺底层。块超长时装得多回退分层铺满、其余拆块/少装披露；门端缓冲 `door_buffer_mm`（默认 300，可用柜长=柜长-缓冲）。
- 布局策略为**分层铺满（floor-layer-first）**，纯散箱、纯整托、混装三场景统一：第 1 层把货物单件用 2D 装箱铺满整个柜底（贴壁、密集，覆盖到该尺寸物理极限），放不下的按"柱高"叠到第 1 层同 SKU 位置正上方（同 footprint → 100% 完整支撑），每位置叠高层数受栈容量（max_layers/高度/承重）约束，**多余件优先加在距柜长中心近的位置（顶层集中在柜长中间，两头低中间高，重心居中）**；三方案统一。**重的在下面、轻的在上面**：可叠轻散箱整栈上托到托盘顶面（托盘 max_top_load 承重约束，上托件按 SKU 重新编号避免编号冲突）；托盘带用网格排布（重托盘放中间行 → 整托重量集中中间；行内蛇形排序 → 左右 y 向重量均衡），网格放不下时回退 rectpack（允许旋转）；易碎货物不参与叠放；必装货物优先铺底。
- **输出 5 个方案**：装得多 / 更稳妥 / 易操作 / 严格完整支撑 / 互叠高装载。前四个均为 100% 完整支撑（严格模式）；**互叠高装载**（`interstack`，默认开启）参考成熟装柜软件"互叠开关"做法：在装得多布局基础上，把未装入的件贪心叠放到任意可叠单件顶面（支撑覆盖率 ≥ `support_coverage_min` 默认 0.7、每边悬挑 ≤ `overhang_ratio_max` 默认 0.2 × 自身短边），允许跨 SKU 落在组合平面上，装载率不低于严格方案；只叠在单层可叠件顶面（不超 max_layers/柜高/顶部承重），放不下的少装披露。`PackRequest` 支持 `enable_interstack`（默认开）与覆盖率/悬挑参数；布局相同方案以 `identical_to` 标记、前端去重显示。
- 前端整托货物始终显示"顶部承重"输入（默认 500kg，即使"可叠"未勾选），`api.ts` 对整托也提交 `max_top_load_g`，保证用户能从表单启用散箱上托；选择"整托"时一次更新多个字段（kind/可叠/承重），不会回退成散箱。
- 支持**先卸后装（后卸先装）**：货物可设卸货顺序（`unload_order`，数字小者先卸，0=不指定）；布局按卸货顺序排布，后卸的货物先装进柜头、先卸的靠柜门，符合目的港卸货顺序操作。前端货物表单提供"卸货顺序"输入。
- 整托支持垂直叠放：整托货物开启“可叠”（表单可配层数/顶部承重）后允许叠整托，按柜内高度（`by_height`）与下层托盘 `max_top_load_g` 自动判定层数；未开启“可叠”的整托上方再放整托时校验器报 `PALLET_STACKING`（提示开启可叠）。顶部承重按支撑物 `max_top_load_g` 校验。
- 最多 30 个 SKU、总计最多 5000 件。
- 货物约束：尺寸、重量、数量、允许朝向、可叠放、最大层数、顶部承重、易碎、必装。
- 可设置货物间隙与柜体安全边距；前端厘米/千克输入，后端统一使用整数毫米/克。
- Excel 固定模板下载和浏览器内解析，原始 Excel 不上传。
- 浏览器 `localStorage` 保存最近一次草稿；服务端不保存订单或布局。
- 结果页提供 3D、俯视、侧视、柜门视图、分层查看、SKU 高亮、装载步骤和打印/PDF。
- 打印报告为汇报级版式：第 1 页方案对比表（含推荐方案）+ 货物清单表；每个方案一页（优缺点、3D 快照、俯视/侧视图、装入明细表、区域说明）；推荐方案的分层布局以 4 列网格小图呈现（`compact` 模式纯色块、无文字标注，避免小图内元素重叠）。
- 结果页指标区展示"前后偏差/左右偏差"；长度方向偏差超过 10% 时显示偏柜警告，并自动推荐更配平的方案（"更稳妥"标签带"推荐"）。
- 俯视/分层图与打印稿按方案 `zones` 绘制区域边框、编号徽标与 SKU×件数标注，打印稿附带"区域说明"清单（区域编号、SKU、件数、柜长区间）。
- 结果页顶部提供“换柜”下拉框和“确认重算”；会保留原货物与计算设置并重新调用 API。
- 首页使用 `frontend/src/assets/voyage-banner.jpg` 横幅，图片内已有“一帆风顺，满载启航”文案，不要在页面重复添加同一句。

明确未做：账号/租户、计费、云端历史、多柜联算、自动码托、ERP/WMS、异形货、危险品规则、手动拖拽布局和复杂兼容矩阵。

## 3. 工程结构

```text
backend/
  app/
    main.py        # FastAPI、静态文件、限流、超时、API
    models.py      # Pydantic 请求/响应与领域模型
    packing.py     # 候选生成、五方案、指标与说明
    validator.py   # 独立布局校验器
  tests/           # pytest：API、算法、校验、大订单
frontend/src/
  App.tsx          # 录入页状态、本地草稿、调用 API
  components/
    ContainerPicker.tsx
    CargoTable.tsx
    SolutionWorkspace.tsx  # 比较页、换柜重算、打印报告
    LoadVisualizer.tsx     # Three.js 3D 和共享坐标的 2D 视图
  lib/
    api.ts          # 毫米/克转换及 API 请求
    cargo.ts        # 货物默认值、输入校验、朝向映射
    excel.ts        # Excel 模板和解析
  styles.css
Dockerfile          # 多阶段构建，FastAPI 同源托管前端 dist
README.md           # 面向开发者的基础说明
output/             # 发布包、systemd/nginx 示例和视觉验收产物
```

前端：React 19、TypeScript、Vite、Three.js、ExcelJS、Vitest。

后端：Python 3.12、FastAPI、Pydantic、`rectpack`、pytest。项目根目录存在 `pnpm-lock.yaml`，优先使用 pnpm；Windows 上已验证的命令可使用 `npm.cmd` 执行同名脚本。

## 4. 本地启动和验证

前置：Node.js 20+、Python 3.12+。

```powershell
# 首次安装
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
pnpm install

# 终端一：后端（从项目根目录执行）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload

# 终端二：前端
pnpm dev
```

打开 `http://127.0.0.1:5173`。`frontend/vite.config.mjs` 将 `/api` 和 `/health` 代理到本地 `http://127.0.0.1:8000`。

修改算法或接口后，至少执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
npm.cmd test -- --run
npm.cmd run build
```

前端修改还应启动页面，在 390px 手机、平板和桌面检查：货物录入、五方案切换、换柜重算、3D 画面、Excel 导入和打印。已有截图与 PDF 位于 `output/playwright/`，仅作参考，不应替代本次变更的验证。

## 5. API 约定

所有 API 与前端同源：

| 方法和路径 | 用途 |
| --- | --- |
| `GET /health` | 返回 `{"status":"ok"}`，用于健康检查 |
| `GET /api/v1/container-presets` | 返回标准柜型 |
| `POST /api/v1/pack` | 接收柜型、货物、间隙，返回五个方案 |

标准柜参数定义在 `backend/app/main.py` 的 `CONTAINER_PRESETS`。当前内尺寸、柜门尺寸和最大载重均为常用参考值；不要在没有业务确认的情况下修改。

`POST /api/v1/pack` 请求中的关键字段：

```json
{
  "container": {
    "id": "20gp",
    "name": "20GP",
    "inner_length_mm": 5898,
    "inner_width_mm": 2352,
    "inner_height_mm": 2393,
    "door_width_mm": 2340,
    "door_height_mm": 2280,
    "max_payload_g": 28200000,
    "clearance_mm": 0
  },
  "item_gap_mm": 0,
  "door_buffer_mm": 300,
  "enable_interstack": true,
  "support_coverage_min": 0.7,
  "overhang_ratio_max": 0.2,
  "cargo_items": []
}
```

请求级互叠配置（均可选，有默认值）：

- `enable_interstack`（默认 `true`）：是否生成"互叠高装载"方案。关闭后只输出 4 个方案（装得多/更稳妥/易操作/严格完整支撑）。
- `support_coverage_min`（默认 `0.7`）：互叠方案中上层货物底面至少被下层支撑的比例（0~1）。
- `overhang_ratio_max`（默认 `0.2`）：互叠方案中上层任一边悬挑不超过自身短边的比例（0~0.5）。

响应 `solutions` 固定有 `high_fill`、`stable`、`easy`、`strict_support` 四项，开启互叠时追加 `interstack`。每项包含：

- `placements`：逐件的坐标、实际朝向尺寸、朝向、重量和装载步骤。
- `loaded_counts` / `unloaded_counts`：每个货物 ID 的装入与未装数量。
- `metrics`：体积/重量利用率、重心、前后偏差（`length_imbalance_pct`）、左右偏差（`width_imbalance_pct`）、重量偏差（两者较大值）、步骤数、货区数。
- `zones`：区域清单（step、SKU、区域矩形、件数），供图与打印稿标注使用；区域按同 SKU 连续块合并。
- `pros` / `cons` / `warnings`：由指标直接生成，不使用 AI 文案。
- `identical_to`：如果布局签名相同，标出与此前方案相同，避免制造虚假差异。

注意：`easy`（易操作）不再承诺与 `high_fill` 装入相同件数。订单过密导致整带/整排布局放不下时，会按"体积最小的 SKU 先删"的顺序删减非必装货物，未装数量进入 `warnings` 与 `cons`；必装货物若连简化布局都放不下，则回退为原排布并保持件数。

错误统一为：`{"error":{"code":"...","message":"..."}}`。参数错误为 `422 INVALID_REQUEST`；必装失败为 `422 MUST_LOAD_UNSATISFIED`；请求过大为 `413`；频率限制为 `429`；繁忙为 `503`；计算超时为 `504`。

## 6. 算法和正确性边界

### 候选生成

核心实现在 `backend/app/packing.py`：

1. 根据允许朝向、门宽/门高、有效内尺寸和堆叠约束选择可用朝向。
2. 货物按完整竖直货栈 `StackUnit` 处理。易碎或不可叠货固定为单层；可叠货受柜高、最大层数和顶部承重限制。**注意**：`_stack_capacity` 与 `_build_sku_blocks` 的承重层数公式都是 `max_top_load_g // weight_g + 1`（缺 `+1` 会把可叠 2 层误判成 1 层，曾导致整托被强制平铺、其它 SKU 装不下）。
3. 采用 `rectpack` 的 `MaxRectsBssf`、`MaxRectsBaf`、`GuillotineBssfSas` 生成多个二维栈位候选；所有排序固定，结果可复现。
4. **SKU 块布局**（`_sku_block_layout`）是三方案的主路径：每 SKU 构建一个 `Block`（块内网格 columns×rows），块沿柜长按策略放置（装得多=体积降序、更稳妥=重块居中配平、易操作=输入顺序分步）。`Block` 有 `rows`（当前行数）与 `flat_rows`（全平铺行数）；`_grow_block_rows` 在柜长预算内尽量给块加行（装得多按 columns/length 效率、更稳妥按重量优先），实现"**先把底层铺满**"。
5. "装得多"从多组载重筛选策略和排列策略中按装载体积、件数、栈数选优。
6. "更稳妥"重排第一方案的同一批货物：纯整托（全为单层整托且底面规格一致）按重量从柜中心向外逐排网格配平、排内分配到累计最轻的列，再做有限次同列交换压平前后重心；其余场景先按固定排序选优，再做同底面互换优化（`_swap_balance`，有 `unload_order` 时禁止跨 SKU 交换以保护先卸后装分区）；长度方向偏差仍超过 10% 时在注意项中说明。
7. "易操作"生成区域化布局（五方案布局彼此不同）：先尝试每个 SKU 一条沿柜宽通铺的整带，再回退为整宽横排货架（排内同 SKU 连续成块），区域化放不下时回退分层铺满；仍放不下时按体积最小的 SKU 先删（必装不删，均无法装入则回退原排布保持件数）；每带/排一个装载步骤，从柜头向柜门编号。
8. **分层铺满**（`_layer_layout`）：第 1 层把货物单件用 2D 装箱铺满整个柜底，放不下的按"柱高"叠到第 1 层同 SKU 位置正上方（同 footprint → 100% 完整支撑），多余件优先加在距柜长中心近的位置（顶层集中在中间、两头低中间高）。可叠托盘也拆全部单件铺底。
9. **互叠高装载**（`_interstack_layout`）：在"装得多"布局基础上，把未装入的件贪心叠放到任意单层可叠件顶面（支撑覆盖率 ≥ `support_coverage_min`、每边悬挑 ≤ `overhang_ratio_max × 短边`），允许跨 SKU 落在组合支撑平面上；只叠在单层可叠件顶面（不超 max_layers/柜高/顶部承重），放不下的少装披露。`PackedStack` 增加 `z_mm` 偏移以表达叠放高度。布局相同方案以 `identical_to` 标记。
10. 每个候选在返回前必须调用 `validate_solution()`；任何校验失败均不返回给用户。`zones` 由后端从布局中按"同 SKU 连续块"计算并随方案返回，前端不得自行推导另一套布局数据。布局校验失败时，若错误码全部命中 `LAYOUT_ADVICE` 映射（覆盖 validator 全部错误码），返回 422 `LAYOUT_NOT_FEASIBLE` 并附中文调整建议（`error.hint`），用户可按建议修改输入；未知错误码才走 500 内部兜底。其余 `MUST_LOAD_UNSATISFIED` 等 422 错误也带中文 `hint`。
11. 混装订单的布局链路：重量筛选后先把可上托散箱栈合并进托盘顶面（`CompositeUnit`，贪心按托盘重量降序、顶面 rectpack 排布，托盘顶面与两端区均按 `item_gap_mm` 预留间隙；高度不够时拆层上托）；再按托盘带居中 + 两端散箱（`_mixed_balance_layout`）或普通 rectpack 排布；必装货物按展开件数核对（`_required_satisfied`），不因合并上托而误判丢失。

### 校验器

`backend/app/validator.py` 独立验证：

- 柜内边界和安全边距。
- 每件在当前朝向下可以通过柜门。
- 朝向、实际尺寸、重量与货物定义一致。
- 实例不重复、实例序号不越界、货物 ID 存在。
- 三维碰撞与同层水平间隙。
- 总重量不超过柜体载重。
- 必装货物全部装入。
- **高层货物底面支撑**：`validate_solution` 支持 `support_coverage_min`（默认 1.0，即 100% 完整支撑）与 `overhang_ratio_max`（默认 0.0，即不允许悬挑）参数。严格方案用默认值（不允许悬空或跨箱搭接）；互叠方案用宽松值（覆盖率 ≥ 0.7、悬挑 ≤ 0.2×短边）。
- 不可叠放/易碎货物上方不得放货。
- 最大层数和顶部承重。

重要限制：当前生成器使用“完整支撑的垂直货栈 + 二维平面排布”模型，互叠方案额外允许"覆盖率阈值 + 悬挑上限"的组合平面叠放。它刻意保守，不生成局部支撑、错层搭接或复杂人工装载技巧可实现的布局。因此“未装入”不等同于物理上绝对装不下。

`item_gap_mm` 只在水平方向执行。堆叠层之间不插入竖向间隙，避免把货物误判为悬空。

## 7. 前端实现要点

- `App.tsx` 是输入、结果和草稿状态的唯一协调层。切换到结果页时不丢失输入数据。
- `CargoTable.tsx` 为数字输入维护短暂字符串草稿，使用户可以先删除默认 `0` 再输入；空值失焦后恢复到上一个有效数字。修改该逻辑时需保留此体验。
- `SolutionWorkspace.tsx` 的换柜重算直接调用 `onRecalculate`，不能复制或改写原始货物数据。自定义柜会作为下拉框首项与标准柜一起显示。方案 tab 与打印表格均按 `response.solutions` 数组渲染（不写死 3 列），`profileShortName` 需覆盖全部 5 个 profile；`identical_to` 标记的方案显示"布局与另一方案相同"提示。
- `LoadVisualizer.tsx` 的 3D 和静态 2D 图都从 API 的 `placements` 坐标生成。不要在任一视图自行推导另一套布局数据。互叠方案的 placement 带 `z_mm` 偏移，3D/分层图按 `z_mm` 渲染即可正常显示叠放高度。
- Excel 解析在浏览器完成。不要改为上传原始 Excel，除非产品的隐私策略和服务端存储设计同步更新。
- PDF 目前采用浏览器打印样式及 3D 快照，不依赖服务端 PDF 生成。

## 8. 服务端保护和部署

`backend/app/main.py` 当前包含以下保护：

- `/api/v1/pack` 请求体最大 1 MB。
- 单 IP 每分钟最多 60 次计算请求（进程内内存窗口）。
- 同时最多 2 个计算任务；单个任务硬超时 15 秒。
- 未处理异常以随机错误编号返回；日志只写错误类型/堆栈，不主动记录订单细节。
- 前端静态路径经 `resolve_frontend_path()` 限制在 `frontend/dist` 内，防止路径穿越。

生产环境当前信息：

| 项目 | 当前值 |
| --- | --- |
| 域名 | `packing.xingshuwen.com` |
| 服务名 | `packing-assistant` |
| 服务目录 | `/data/packing-assistant/app` |
| Uvicorn 监听 | `127.0.0.1:8500` |
| 反向代理 | Nginx，HTTPS 由现有站点配置提供 |

仓库中可参考的部署文件：

- `output/packing-assistant.service`
- `output/packing.xingshuwen.com.nginx`
- `output/packing-frontend-dist.tar.gz`
- `output/packing-assistant-release.tar.gz`

前端单独发布的常用流程：

```powershell
npm.cmd run build
tar -czf output\packing-frontend-dist.tar.gz -C . frontend\dist

# 使用由运维人员安全保管的 SSH 凭据上传至服务器 /tmp
```

服务器侧解包并重启：

```bash
sudo tar -xzf /tmp/packing-frontend-dist.tar.gz -C /data/packing-assistant/app
sudo systemctl restart packing-assistant
curl -fsS http://127.0.0.1:8500/health
```

公网验证：

```powershell
curl.exe -fsS https://packing.xingshuwen.com/health
curl.exe -fsS https://packing.xingshuwen.com/api/v1/container-presets
```

不要把 SSH 私钥、服务器密码或证书写入代码库、发布包或交接文档。后端修改时需要同步发布 `backend/app`，此时应使用完整发布包或容器镜像，而不是只替换 `frontend/dist`。部署前先在本地运行测试和构建；部署后必须检查 `/health`、首页静态资源和一次真实 `POST /api/v1/pack`。

Docker 方式同样受支持：

```powershell
docker build -t container-loading-assistant .
docker run --rm -p 8000:8000 container-loading-assistant
```

## 9. 测试现状和后续变更规则

现有后端测试覆盖 API、确定性、混装、超量、必装失败、朝向、载重选择、重心、边界、碰撞、柜门、完整支撑、易碎/不可叠、层数、承重、间隙和大订单性能，并新增：整托前后配平（11 重+11 轻 40GP 更稳妥长度偏差 ≤ 5%）、非对称重量配平、整托网格步骤、易操作简单单保持件数、密单少装并披露、必装回退保持件数、`zones`/前后左右偏差字段契约。混合堆叠新增 `backend/tests/test_mixed_stacking.py`：散箱上托合并（易碎/0 承重/放不下不上托）、**超高拆层上托与拆层后 instance_index 不重复**、复合展开 z 起点与多栈不重叠、托盘带居中（质心在中段）、三方案端到端（整托在下散装在上）、必装散箱合并后候选不误判、部分上托端到端、gap>0 混装、易操作保留托盘、**可叠整托垂直叠放（按高度/承重判定）与不可叠兜底**。中文报错新增：`_raise_for_invalid_layout` 已知/未知错误码分支、`LAYOUT_ADVICE` 覆盖 validator 全部错误码、API 层 hint 字段测试。前端测试含"选整托保持整托并带默认值"回归（`App.test.tsx`），另验证通过硅油纸真实案例（3 规格整托+散箱、40HQ）三方案/整托居中/散箱上托/换柜重算/移动端。

SKU 块布局新增 `backend/tests/test_sku_blocks.py`：块参数推导（columns/rows/layers）、fill/balance/easy 三策略放置（体积降序/重块居中/输入顺序）、门端缓冲（door_buffer_mm 默认 300 生效且披露）、5 SKU 69 托三方案端到端（全装/互不相同）、纯散箱大订单回退分层铺满、混装托盘块+散箱块、可旋转托盘 footprint 同步、balance 中心槽不越 clearance、**先铺满底层规则回归（底层底位数最大化 + 上层件数不超底层）**、**承重层数 +1 回归（280kg/承重500kg 叠 2 层而非 1 层，避免整托被强制平铺）**、**互叠高装载端到端（组合平面叠放装载率 ≥ 严格方案 + 宽松校验通过）**、**关闭互叠返回 4 方案**。前端测试覆盖货物数字输入、柜型选择、Excel 和可视化基础状态，`App.test.tsx` 夹具与 `SolutionWorkspace.test.tsx` 已扩到 5 个 profile。历史验证结果为前端 6 个测试文件/14 个测试通过、后端 83 项通过，构建通过；每次改动仍需重新执行第 4 节命令，不要依赖历史结果。

修改原则：

1. 涉及布局或指标时，先在 `backend/tests` 增加或更新场景测试，并确认返回布局能再次通过 `validate_solution()`。
2. 涉及 API 契约时，同步更新 `backend/app/models.py`、`frontend/src/types.ts`、`frontend/src/lib/api.ts` 和相关组件。
3. 涉及柜型参数时，保留来源、复核门洞尺寸和最大载重，并增加对应测试案例。
4. 涉及视觉布局时，复用现有墨绿、灰白、海运工作台风格；不要将首页改为营销落地页。
5. 修改视觉或 Three.js 后，要做浏览器截图和画布非空检查，避免 3D 画面空白或控件遮挡。

## 10. 后续演进建议

验证期应优先用客户历史装柜单校验：实际可装数量、现场可执行性、保守规则带来的差异、标准柜参数是否符合承运方柜型。

已知可优化点（按优先级）：

1. **互叠方案覆盖范围**：当前互叠只在"装得多布局 + 剩余件"上补位，且只叠在单层可叠件顶面（最多两层）。可扩展为"允许在更多层的组合平面上互叠"或"把互叠作为独立布局策略（而非装得多增强）"，进一步提升装载率；需同步加强覆盖率/悬挑/层数校验并补充测试。
2. **互叠方案前端提示**：互叠方案已在 `warnings` 披露"稳定性低于完整支撑方案"，前端可考虑给互叠方案加独立视觉标识（如标签色），引导用户理解差异。
3. **严格完整支撑方案**：目前与"装得多"布局相同（靠 `identical_to` 去重）。若希望它真正差异化（如禁止叠放、单层平铺），需单独实现布局策略并评估装载率影响。

若验证成功并进入 SaaS，建议按顺序增加：

1. 账号、租户隔离和订单历史，同时调整隐私告知与数据保留策略。
2. 任务队列/持久化计算，而不是继续依赖单进程内存限流。
3. 审计日志、匿名错误监控和更严格的公网限流。
4. 多柜联算、ERP/WMS 对接和更完整的货物兼容矩阵。

核心算法应继续与前端和 SaaS 层隔离，确保新的账号/存储能力不改变 `POST /api/v1/pack` 的确定性与独立校验要求。
