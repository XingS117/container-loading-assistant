# 装柜方案助手：技术交接

最后更新：2026-08-17

## 0. 最新交接摘要

### 当前结论

- 当前工作树已完成本轮装柜算法和常见规格预制入口实现，本地全量验证通过，并已发布到生产环境。
- 生产服务为 `packing-assistant.service`，后端监听 `127.0.0.1:8500`，Nginx 负责 HTTPS 和反向代理。
- 当前算法工作树为 `codex/container-layout-rebuild`，工作目录为 `.worktrees/algorithm-rebuild`。主分支不要直接覆盖或重置。
- `POST /api/v1/pack` 正式返回 3 个方案：
  `high_fill`、`stable`、`easy`。
- 当前三个方案的显示名称分别为：
  「装载率优先」「重心稳妥」「易操作」。
- 旧版 `enable_interstack`、`support_coverage_min`、`overhang_ratio_max`
  仍可被解析，但仅用于兼容旧调用，不会生成第五个 `interstack` 方案，
  也不会降低正式方案的完整支撑安全约束。

### 最新变更

- 当前未提交工作：增加三 SKU、四 SKU、五 SKU 常见组合和 12 个单品规格的前端预制入口；
  四 SKU、五 SKU 案例缺少客户重量时显示「需补充重量」，补齐前禁止计算。
- `7b2ec79`：优化同规格叠放与 PB-PA-PB-PC 中部集中布局。
- `f97c403`：区分四种正式装柜布局策略。
- `c728a28`：增强四方案的侧视图差异，避免不同方案在视觉上完全相同。

### 已验证

- 后端测试：100 项通过。
- 前端测试：19 项通过。
- `npm.cmd run build`：通过。
- Python `compileall`：通过。
- `git diff --check`：通过。
- 生产服务重启后状态：`active`，服务器本机 `/health` 返回 `{"status":"ok"}`。
- 公网健康检查：通过，`/health` 返回 `ok`，容器预设返回 3 个。
- 本次构建首页资源：`assets/index-B6v2EXMg.js`。
- 公网真实 `POST /api/v1/pack`：返回 3 个正式方案。
- 公网 A/B/C 40HQ 回归：3 个方案均装入 63 托，`identical_to` 均为空。

### 当前工作区注意事项

以下目录是测试或运行产生的临时目录，不应加入提交：

- `_temp/`
- `.pytest-tmp-*/`

## 1. 项目定位

「装柜方案助手」是面向外贸发货场景的装柜测算工具。用户录入散箱或整托货物后，
系统按柜型、尺寸、重量、叠放和操作约束生成多种可比较布局。

系统使用确定性的启发式算法，不承诺数学意义上的全局最优。实际发货前仍需结合
真实箱单、柜体铭牌、货物包装强度、现场装卸设备和承运方要求复核。

## 2. 正式方案

| Profile ID | 显示名称 | 核心目标 |
| --- | --- | --- |
| `high_fill` | 装载率优先 | 在满足物理约束的前提下优先装入件数、体积和底层覆盖率 |
| `stable` | 重心稳妥 | 保持与装载率方案相同的装入货物集合，优先降低前后和左右重心偏差 |
| `easy` | 易操作 | 优先 SKU 连续区域、较少装载步骤和较少区域切换 |

所有正式方案都必须满足以下硬优先级：

1. 必装货物完整装入。
2. 通过 `validate_solution()` 物理校验。
3. 遵循底层优先规则。
4. 上层货物形成合格的连续区域，不把孤立单件作为正常候选。
5. 在上述条件满足后，再比较装载率、重心和操作性。

三个方案可以在货物集合相同的情况下使用不同的底层分带、叠放位置、
装载顺序和重心策略。若某些场景确实产生相同布局，响应中的 `identical_to`
会明确标注，不制造虚假的方案差异。

## 3. 当前算法规则

### 3.1 同规格叠放

- 只有尺寸、朝向和货物规格一致的产品可以上下叠放。
- A 只能叠在 A 上方，B 只能叠在 B 上方；不同 SKU 或不同规格之间禁止互叠。
- 上层货物只放在已存在的同规格底层支撑货位上。
- 每个 SKU 仍受 `stackable`、`max_layers`、柜内高度和 `max_top_load_g`
  约束。
- C 类不可叠货物只放在底层。
- 校验器使用 `STACKING_SPEC_MISMATCH` 拦截不同规格互叠。

### 3.2 底层优先和上层集中

- 先生成底层货位，再处理剩余货物。
- 底层优先形成连续覆盖区，而不是先在局部位置堆高。
- 上层货物从柜长方向的中部向两侧展开，优先形成一个主要连续区域。
- 上层货物优先放到同 SKU、同 footprint 的底层支撑位置正上方。
- 只有在满足支撑、层数和承重条件时才允许叠放。
- 上层区域被拆散、出现孤立单件或明显偏离中部时，候选评分会优先选择更好的布局；
  如果没有完全符合的候选，则返回物理安全的次优方案并加入明确 warning。
- 单一中部连续区域属于软质量目标，不会因为未满足就直接中断整次计算；
  所有正式方案仍必须通过物理支撑、重叠、门端和承重校验。

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
并尽量从柜长中部向两侧连续展开。该案例 3 个正式方案均装入 63/63 托，
上层不放 C，且上层区域通过连续性检查。

### 3.4 候选生成

核心实现位于 `backend/app/packing.py`：

1. 根据允许朝向、柜门尺寸、安全边距和门端缓冲筛选可用朝向。
2. 将货物转换为可叠放的完整竖直货栈。
3. 为纯整托订单优先生成底层分带候选：
   SKU 连续条带、受控混排横排以及高覆盖率二维候选。
4. 使用 `rectpack` 生成二维底层候选，再由业务规则筛选。
5. 根据方案目标选择块排序、底层行数、重心位置和装载步骤。
6. 展开为现有 `Placement` 结构，计算 `zones`、指标和 warning。
7. 每个正式方案返回前都调用 `validate_solution()`。

`_expand_stacks()`、`_compute_zones()` 和现有 `Placement` 结构保持不变，
3D、俯视、侧视、分层图和打印报告直接使用后端返回的 `placements` 与 `zones`。

### 3.5 方案差异

- `high_fill`：按装入件数、体积利用率和底层覆盖率筛选。
- `stable`：基于 `high_fill` 的装入集合重新排布，优先降低重心偏差。
- `easy`：按 SKU 连续区域和装载步骤组织布局，必要时才少装非必装货物。

## 4. API 约定

所有 API 与前端同源：

| 方法和路径 | 用途 |
| --- | --- |
| `GET /health` | 返回 `{"status":"ok"}` |
| `GET /api/v1/container-presets` | 返回标准柜型 |
| `POST /api/v1/pack` | 返回 3 个正式装柜方案 |

请求中的旧字段仍保留解析能力：

```json
{
  "door_buffer_mm": 300,
  "enable_interstack": true,
  "support_coverage_min": 0.7,
  "overhang_ratio_max": 0.2,
  "cargo_items": []
}
```

兼容字段说明：

- `enable_interstack`：无论传入 `true`、`false` 或省略，都不增加第五方案。
- `support_coverage_min`：不改变正式方案的完整支撑安全约束。
- `overhang_ratio_max`：不改变正式方案的完整支撑安全约束。

响应中的 `solutions` 固定按以下顺序返回：

```text
high_fill
stable
easy
```

每个方案包含：

- `placements`：逐件坐标、尺寸、朝向、重量、层高和装载步骤。
- `loaded_counts` / `unloaded_counts`：每个货物 ID 的装入和未装数量。
- `metrics`：件数、体积利用率、重量利用率、重心、前后左右偏差、步骤数和货区数。
- `zones`：连续区域清单，供前端图形和打印报告使用。
- `pros` / `cons` / `warnings`：由布局指标和质量检查生成。
- `identical_to`：布局签名相同时标记对应的前一个方案。

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
    packing.py     # 候选生成、四方案、指标和说明
    validator.py   # 独立物理校验器
  tests/           # API、算法、校验、混装和大订单测试
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

### 5.1 常见产品规格预制

- 预制目录位于 `frontend/src/lib/cargoPresets.ts`，是前端静态数据，不新增服务端接口、
  不写入数据库，也不影响其他用户。
- 默认提供 3 个组合：A/B/C、四 SKU、五 SKU；另提供案例中的 12 个单品规格。
- 四 SKU 使用已确认的 `76 × 76 × 100`，不是早期记录中的 `76 × 76 × 110`。
- 四 SKU 和五 SKU 的客户重量未提供，预制加载后重量为空并要求用户补齐；
  A/B/C 使用当前测试重量 150/280/400 kg，顶部承重测试值为 500 kg。
- 选择预制项会在当前清单非空时要求确认，并为每次加载生成新的货物 ID；
  用户仍可继续修改、删除和添加自定义产品。

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

- `App.tsx` 负责输入、结果和草稿状态，切换到结果页不能丢失输入数据。
- `CargoTable.tsx` 的数字输入草稿体验需要保留。
- `SolutionWorkspace.tsx` 按 `response.solutions` 动态渲染 3 个方案，
  不得写死第五个互叠方案或相关推荐。
- `LoadVisualizer.tsx` 的 3D、俯视、侧视和分层图只读取后端 `placements`。
- `zones` 由后端计算，前端不重新推导另一套布局。
- Excel 在浏览器内解析，原始 Excel 不上传服务端。
- 打印报告直接使用当前方案的布局和区域数据。

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
3 个方案切换、3D 图、俯视图、侧视图、分层图、Excel 导入和打印。

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

公网检查：

```powershell
curl.exe -fsS https://packing.xingshuwen.com/health
curl.exe -fsS https://packing.xingshuwen.com/api/v1/container-presets
```

发布前必须先通过本地测试和构建，发布后还需执行一次真实的
`POST /api/v1/pack`，确认返回 3 个方案且首页静态资源正常。

不要把 SSH 私钥、服务器密码或证书写入代码库、发布包或交接文档。

## 10. 后续变更规则

1. 修改布局算法前，先增加或更新 `backend/tests` 场景测试。
2. 修改布局后，必须确认所有正式方案再次通过 `validate_solution()`。
3. 修改方案 ID 或接口字段时，同步检查 `models.py`、`types.ts`、
   `SolutionWorkspace.tsx` 和相关测试夹具。
4. 不要恢复 `interstack` 为正式方案，除非重新设计安全规则、API 契约、
   前端展示和完整回归测试。
5. 不要删除或覆盖用户未提交的工作区文件，尤其是 `_temp/` 和测试临时目录。

下一阶段优先使用客户历史装柜单验证：

- 实际可装数量与系统结果的差异。
- PB-PA-PB-PC 分带是否符合现场装卸顺序。
- 同规格叠放和顶部承重参数是否与真实包装一致。
- 标准柜尺寸、柜门尺寸和门端操作空间是否符合承运方数据。
