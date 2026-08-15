# 装柜方案助手：技术交接

最后更新：2026-08-15

## 0. 最新交接摘要

### 当前结论

- 本轮改造（单方案 + 优化目标偏好开关）已在 `main` 分支完成开发与测试，
  并于 2026-08-15 部署到 `https://packing.xingshuwen.com` 并通过线上验证。
- 生产服务为 `packing-assistant.service`，后端监听 `127.0.0.1:8500`，Nginx 负责 HTTPS 和反向代理。
- 线上镜像工作树为 `.worktrees/algorithm-rebuild`（不要修改）；算法工作已合并到 `main`。
- `POST /api/v1/pack` 每次返回**单个方案**，由请求字段
  `optimization_goal` 选择优化目标：`high_fill`（装载率优先）、
  `stable`（重心稳妥）、`easy`（易操作），默认 `high_fill`；
  切换目标由前端重新发起计算。
- 每个方案返回平移归一几何指纹 `layout_fingerprint`（12 位哈希，见 §4）；
  前端切换目标后对比指纹，几何相同（含仅整体平移/步骤编号不同）时
  显示"布局几何相同"披露提示，而不是展示两张一样的图不作说明。
- `strict_support` 已删除：旧请求传 `optimization_goal: "strict_support"`
  会返回 422（INVALID_REQUEST）。
- 旧版 `enable_interstack`、`support_coverage_min`、`overhang_ratio_max`
  仍可被解析，但仅用于兼容旧调用，不会生成 `interstack` 方案，
  也不会降低正式方案的完整支撑安全约束；`_interstack_layout` 死代码已清理。

### 最新变更

- （本次，三目标布局差异化改造，见 §3.5）：修复用户实测发现的三目标
  布局收敛缺陷。① 后端新增 `_layout_fingerprint` 平移归一几何指纹（含
  rotation/尺寸，不含 step 与 instance_index——排序键含实例编号曾导致
  stable 平移兜底洗牌编号后"同一张图"指纹不同而漏披露）；② stable 链
  候选加 qualifies 门（件数守恒 + 指纹 != 基线），并追加
  sku_block(balance)+swap / pallet_grid / stable_balance / repack /
  layer 候选链，兜底升级为 `_recenter_blocks` 真正居中重排
  （按 cargo_id 分组、重块居中、守 clearance 与 door_buffer）；③ easy 链
  区域布局优先（`_easy_region_layout`），四重门（件数守恒、门端、叠放时
  顶层集中 ≤ 柜长/8、步骤/区域数上限）只用于"优先采用"、不禁止回退；
  ④ 前端 `SolutionWorkspace` 按 request_id 对比前后指纹并披露。
- `7389f64`：修复两个真实 bug——stable 目标少装 118 件（`_layer_layout`
  柱高分配数学与 stable 回退链顺序）、easy 目标碎片化 37 区（错误复用
  stable 朝向货栈）；X/Y 场景三目标均全装 700 件。
- `2184dd5`：`/api/v1/pack` 收敛为单方案 + `optimization_goal` 目标偏好开关（后端契约）。
- `ed98368`：stable 散件候选按同 SKU 连续段分组装载步骤（修复 123 步碎片化）。
- `a8315b9`：后端测试重写为单方案契约，新增 X/Y 端到端、`_layer_layout`
  柱高数学、`_shelf_layout` 排内纯净化与三原则回归。
- `76971a0`：前端改为单方案 + 三个优化目标切换按钮（类型/API/组件/测试）。

### 已验证

- 后端测试：178 项通过（含新增 `test_goal_distinctness.py` 10 条指纹与
  目标差异回归 + `test_api.py` 指纹端点断言）。
- 前端测试：21 项通过（含 `SolutionWorkspace` 披露提示条 3 条）。
- `npm.cmd run build`：通过。
- 实测三目标指纹对照（`_temp/diag_*.py`，40HQ，door_buffer=300）：
  - X/Y 700 散箱、A/B/C 63 托、2 SKU 60 托、4/5 SKU 整托：三目标两两几何互异。
  - 1 SKU 30 托：high 与 stable/easy 互异（stable 前后 46%→0%），
    stable 与 easy 收敛为同一居中排布 → 披露。
  - 4 SKU 20×4 装不下（26 托）：三目标几何相同 → 披露。
  - 2/3 SKU 完全不可叠少量整托：easy 与 high 相同（stable 互异）→ 披露。
  - 14 个整托形态扫描中 high vs stable 全部互异。
- Python `compileall`：通过。
- `git diff --check`：通过。
- 线上（2026-08-15 部署后）：
  - `/health` 与 `/api/v1/container-presets` 正常；
  - X/Y 场景三个目标各一次真实 `POST /api/v1/pack`：均返回单个方案、
    全装 700 件（high=2 区 2 步、stable=3 区 3 步、easy=2 区 2 步），
    `request_id` 随目标互异；
  - `optimization_goal: "strict_support"` 返回 422 `INVALID_REQUEST`；
  - 首页已服务新构建（JS 含 `optimization_goal` 与三个目标按钮文案）。

### 当前工作区注意事项

以下目录是测试或运行产生的临时目录，不应加入提交：

- `_temp/`
- `.pytest-tmp-final-profile/`
- `.pytest-tmp-full-distinct/`

## 1. 项目定位

「装柜方案助手」是面向外贸发货场景的装柜测算工具。用户录入散箱或整托货物后，
系统按柜型、尺寸、重量、叠放和操作约束生成可执行的装柜布局；
每次计算返回单个方案，并可按优化目标偏好重新计算。

系统使用确定性的启发式算法，不承诺数学意义上的全局最优。实际发货前仍需结合
真实箱单、柜体铭牌、货物包装强度、现场装卸设备和承运方要求复核。

## 2. 优化目标与方案

每次 `POST /api/v1/pack` 只返回一个方案；请求字段 `optimization_goal`
决定使用哪个优化目标（默认 `high_fill`）：

| Goal ID | 显示名称 | 核心目标 |
| --- | --- | --- |
| `high_fill` | 装载率优先 | 在满足物理约束的前提下优先装入件数和体积 |
| `stable` | 重心稳妥 | 保持与装载率优先相同的装入货物集合，重新排布以降低前后/左右重心偏差 |
| `easy` | 易操作 | SKU 连续区域、较少装载步骤；订单过密时允许少装非必装货物并明确披露 |

所有目标都必须满足以下硬优先级：

1. 必装货物完整装入。
2. 通过 `validate_solution()` 物理校验。
3. 遵循三条基本原则（见 §3.2/§3.3）：底层先铺满、同规格参数才可叠放、
   上层集中到柜长中部而不是分散到两边。
4. 上层货物形成合格的连续区域，不把孤立单件作为正常候选。
5. 在上述条件满足后，再按目标比较装载率、重心和操作性。

切换目标由前端重新发起一次计算；`optimization_goal` 参与 `request_id`
哈希，前端用 `request_id` 变化重置视图状态。

## 3. 当前算法规则

### 3.1 同规格叠放

- 只有尺寸、朝向和货物规格一致的产品可以上下叠放。
- A 只能叠在 A 上方，B 只能叠在 B 上方；不同 SKU 或不同规格之间禁止互叠。
- 上层货物只放在已存在的同规格底层支撑货位上。
- 每个 SKU 仍受 `stackable`、`max_layers`、柜内高度和 `max_top_load_g`
  约束。
- C 类不可叠货物只放在底层。
- 校验器使用 `STACKING_SPEC_MISMATCH` 拦截不同规格互叠。

### 3.2 铺满底层和上层集中

- 先生成底层货位，再处理剩余货物。
- 底层先形成连续覆盖区，而不是先在局部位置堆高。
- 上层货物从柜长方向的中部向两侧展开，优先形成一个主要连续区域。
- 上层货物优先放到同 SKU、同 footprint 的底层支撑位置正上方。
- 只有在满足支撑、层数和承重条件时才允许叠放。
- 上层区域被拆散、出现孤立单件或明显偏离中部时，候选会被淘汰或加入明确 warning。

### 3.3 A/B/C 典型布局

验收案例按 40HQ 处理：

- 柜体内尺寸：`12032 × 2352 × 2698 mm`。
- 柜门端预留：`300 mm`。
- A：`650 × 650 × 1200 mm`，150 kg，30 托，可叠 2 层，顶部承重 500 kg。
- B：`890 × 750 × 1120 mm`，280 kg，30 托，可叠 2 层，顶部承重 500 kg。
- C：`1080 × 800 × 1250 mm`，400 kg，3 托，不可叠。

典型底层顺序为：

1. 柜内一侧的 B 连续区域。
2. 中部的 A 连续区域。
3. 另一侧的 B 连续区域。
4. 靠近柜门端的 C 货物，3 托只放底层。

底层完成后，剩余 A、B 分别叠放在各自规格的支撑位置上，
并尽量从柜长中部向两侧连续展开。该案例三个优化目标均装入 63/63 托，
上层不放 C，且上层区域通过连续性检查。

### 3.4 候选生成

核心实现位于 `backend/app/packing.py`：

1. 根据允许朝向、柜门尺寸、安全边距和门端缓冲筛选可用朝向。
2. 将货物转换为可叠放的完整竖直货栈。
3. 为纯整托订单优先生成底层分带候选：
   SKU 连续条带、受控混排横排以及高覆盖率二维候选。
4. 使用 `rectpack` 生成二维底层候选，再由业务规则筛选。
5. 根据 `optimization_goal` 选择块排序、底层行数、重心位置和装载步骤。
6. 展开为现有 `Placement` 结构，计算 `zones`、指标和 warning。
7. 方案返回前调用 `validate_solution()`。

`_expand_stacks()`、`_compute_zones()` 和现有 `Placement` 结构保持不变，
3D、俯视、侧视、分层图和打印报告直接使用后端返回的 `placements` 与 `zones`。

### 3.5 目标差异

- `high_fill`：按装入件数和体积利用率筛选布局，是另两个目标的基线
  （件数契约 `high_counts`）。
- `stable`：基于 `high_fill` 的装入集合重新排布。候选链依次尝试
  swap(floor_first)、`_sku_block_layout`(balance)+swap、pallet_grid、
  `_stable_balance_layout`、repack、`_layer_layout`；每个候选必须
  qualifies（展开后逐 SKU 件数 == `high_counts` 且指纹 != 基线指纹——
  与基线几何收敛的候选跳过）。全部失败时兜底：`_recenter_blocks`
  （按 cargo_id 分组重块居中重排，守 clearance 与 door_buffer）与
  `_center_stacks`（整体平移）取配平更优者。
- `easy`：**区域布局优先**——`_easy_region_layout` 居中后过四重门
  （①逐 SKU 件数 == `high_counts` ②最远件不越门端 ③存在叠放时顶层集中
  偏差 ≤ 柜内长/8 ④步骤数与区域数 ≤ max(4, SKU 数)）即采用；否则回退
  块布局（easy 策略）与旧链（region 保必装 → stable region → repack →
  基线）。与装载率优先的件数差写入 `cons`「为便于装载少装 N 件」。
- 无差异披露：`_layout_fingerprint` 对展开后的 placements 计算平移归一
  几何指纹（减去 min_x/min_y，按 cargo_id/z/x/y 排序，载荷含朝向与
  尺寸，不含 step 与 instance_index）。前端切换目标后对比前后指纹，
  相同（含仅整体平移）即提示"布局几何相同"。个别订单形态（完全不可叠
  的少量整托、装不下且剩余无法重排等）收敛是货物本身决定的，如实披露
  而不是假装有差异。

### 3.6 三条基本原则（所有目标生效，由测试固化）

1. 底层先铺满：先把柜底铺满，剩余件数才叠到同规格底位正上方。
2. 同规格参数才可叠放：同一列内所有件必须同 SKU、同 footprint，
   校验器用 `STACKING_SPEC_MISMATCH` 拦截不同规格互叠。
3. 上层集中中间：叠高层从柜长中部向两侧展开，顶层质心距柜长中心
   不超过 1500 mm（40HQ 场景）。

## 4. API 约定

所有 API 与前端同源：

| 方法和路径 | 用途 |
| --- | --- |
| `GET /health` | 返回 `{"status":"ok"}` |
| `GET /api/v1/container-presets` | 返回标准柜型 |
| `POST /api/v1/pack` | 返回单个装柜方案（`optimization_goal` 指定目标） |

请求字段：

```json
{
  "optimization_goal": "stable",
  "door_buffer_mm": 300,
  "enable_interstack": true,
  "support_coverage_min": 0.7,
  "overhang_ratio_max": 0.2,
  "cargo_items": []
}
```

字段说明：

- `optimization_goal`：`high_fill`（默认）/ `stable` / `easy`；其他值返回 422。
- `enable_interstack`：无论传入 `true`、`false` 或省略，都不增加额外方案。
- `support_coverage_min`：不改变正式方案的完整支撑安全约束。
- `overhang_ratio_max`：不改变正式方案的完整支撑安全约束。

响应中的 `solutions` 只包含一个方案，其 `profile` 与请求的
`optimization_goal` 一致；`request_id` 由完整请求 JSON 哈希而来，
切换目标会得到不同的 `request_id`。

每个方案包含：

- `placements`：逐件坐标、尺寸、朝向、重量、层高和装载步骤。
- `loaded_counts` / `unloaded_counts`：每个货物 ID 的装入和未装数量。
- `metrics`：件数、体积利用率、重量利用率、重心、前后左右偏差、步骤数和货区数。
- `zones`：连续区域清单，供前端图形和打印报告使用。
- `layout_fingerprint`：平移归一几何指纹（12 位 hex）。整体平移、装载
  步骤编号或实例编号变化不改变指纹；逐件位置或朝向变化才会改变。
  前端切换目标后与上一方案对比，相同即披露"布局几何相同"。
- `pros` / `cons` / `warnings`：由布局指标和质量检查生成。

错误统一为：

```json
{
  "error": {
    "code": "LAYOUT_NOT_FEASIBLE",
    "message": "当前货物参数无法生成有效的装柜方案，请根据下方提示调整后重试",
    "hint": "..."
  }
}
```

常见错误码包括 `INVALID_REQUEST`、`MUST_LOAD_UNSATISFIED`、
`LAYOUT_NOT_FEASIBLE`、`REQUEST_TOO_LARGE`、`429`、`503` 和 `504`。

## 5. 工程结构

```text
backend/
  app/
    main.py        # FastAPI、静态文件、限流、超时和 API
    models.py      # Pydantic 请求/响应模型
    packing.py     # 候选生成、目标分支（high_fill/stable/easy）、指标和说明
    validator.py   # 独立物理校验器
  tests/           # API、算法、校验、混装、大订单和目标回归测试
                   #   test_goal_distinctness.py = 指纹语义 + 三目标差异 + 披露场景
frontend/src/
  App.tsx
  components/
    CargoTable.tsx
    LoadVisualizer.tsx
    SolutionWorkspace.tsx
  lib/
    api.ts
    cargo.ts
    excel.ts
  types.ts
  styles.css
Dockerfile
README.md
```

前端使用 React、TypeScript、Vite、Three.js、ExcelJS 和 Vitest。
后端使用 Python、FastAPI、Pydantic、`rectpack` 和 pytest。

## 6. 校验和正确性边界

`backend/app/validator.py` 独立检查：

- 柜内边界、柜门通过性和安全边距。
- 朝向、实际尺寸、重量和货物定义一致。
- 货物实例不重复、索引不越界。
- 三维碰撞和水平间隙。
- 总重量不超过柜体载重。
- 必装货物完整装入。
- 上层货物有完整的同规格支撑。
- 不可叠或易碎货物上方不得放货。
- 最大层数、柜内高度和顶部承重。

当前生成器采用「完整支撑的竖直货栈 + 二维平面排布」模型，
不生成局部支撑、错层搭接或依赖现场技巧的复杂布局。
因此，未装入不等同于物理上绝对装不下，而是表示当前安全启发式没有找到
可接受候选。

`item_gap_mm` 只作用于水平方向。堆叠层之间不插入竖向间隙，
避免把正常支撑误判为悬空。

## 7. 前端实现要点

- `App.tsx` 负责输入、结果和草稿状态，切换到结果页不能丢失输入数据；
  `goal` 状态与结果同批更新，回输入页不重置。
- `CargoTable.tsx` 的数字输入草稿体验需要保留。
- `SolutionWorkspace.tsx` 只渲染 `response.solutions[0]`，顶部渲染三个
  优化目标切换按钮；点击非当前目标调用 `onRecalculate(container, goal)`
  重新计算，`recalculating` 时按钮禁用；不得恢复多方案对比或推荐逻辑。
- 披露提示条：`useRef` 记录上一方案 `{goal, fingerprint}`，
  `useEffect([response.request_id])` 中在目标切换且指纹相同（且非空）时
  显示 `.identical-layout-notice`（非 alert、无按钮），否则清除；
  StrictMode 双跑幂等（第二次 prev.goal === goal 走 else）。
- `LoadVisualizer.tsx` 的 3D、俯视、侧视和分层图只读取后端 `placements`。
- `zones` 由后端计算，前端不重新推导另一套布局。
- Excel 在浏览器内解析，原始 Excel 不上传服务端。
- 打印报告为单方案版式，直接使用当前方案的布局和区域数据。

## 8. 本地启动和验证

前置：Node.js 20+、Python 3.12+。

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
pnpm install

# 后端
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload

# 前端
pnpm dev
```

修改算法或接口后至少执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q --basetemp C:\tmp\packing-pytest
npm.cmd test -- --run
npm.cmd run build
```

前端改动还需检查 390 px 手机、平板和桌面视图，重点确认货物录入、
3 个优化目标切换、3D 图、俯视图、侧视图、分层图、Excel 导入和打印。

## 9. 生产部署

生产环境：

| 项目 | 当前值 |
| --- | --- |
| 域名 | `packing.xingshuwen.com` |
| 服务名 | `packing-assistant` |
| 服务目录 | `/data/packing-assistant/app` |
| Uvicorn 监听 | `127.0.0.1:8500` |
| 服务器 | `ubuntu@111.229.187.91` |

后端修改时必须同时发布：

- `backend/app`
- `frontend/dist`

服务器解包并重启：

```bash
sudo tar --overwrite --no-same-owner --no-same-permissions \
  -xzf /tmp/packing-release.tar.gz \
  -C /data/packing-assistant/app
sudo systemctl restart packing-assistant
curl -fsS http://127.0.0.1:8500/health
```

最近一次发布：2026-08-15（单方案 + 优化目标改造）；发布前旧代码备份在
服务器 `/tmp/packing-backup-20260815.tar.gz`，回滚时解包回
`/data/packing-assistant/app` 并重启服务即可。

公网检查：

```powershell
curl.exe -fsS https://packing.xingshuwen.com/health
curl.exe -fsS https://packing.xingshuwen.com/api/v1/container-presets
```

发布前必须先通过本地测试和构建，发布后还需执行一次真实的
`POST /api/v1/pack`，确认返回单个方案（`optimization_goal` 生效、
`request_id` 随目标变化）且首页静态资源正常。

不要把 SSH 私钥、服务器密码或证书写入代码库、发布包或交接文档。

## 10. 后续变更规则

1. 修改布局算法前，先增加或更新 `backend/tests` 场景测试。
2. 修改布局后，必须确认所有正式方案再次通过 `validate_solution()`。
3. 修改方案 ID 或接口字段时，同步检查 `models.py`、`types.ts`、
   `SolutionWorkspace.tsx` 和相关测试夹具。
4. 不要恢复 `interstack` 或 `strict_support` 为目标/方案，除非重新设计
   安全规则、API 契约、前端展示和完整回归测试；也不要恢复一次返回
   多方案的接口形态（目标切换 = 前端重新发起计算）。
5. 不要删除或覆盖用户未提交的工作区文件，尤其是 `_temp/` 和测试临时目录。

下一阶段优先使用客户历史装柜单验证：

- 实际可装数量与系统结果的差异。
- PB-PA-PB-PC 分带是否符合现场装卸顺序。
- 同规格叠放和顶部承重参数是否与真实包装一致。
- 标准柜尺寸、柜门尺寸和门端操作空间是否符合承运方数据。
