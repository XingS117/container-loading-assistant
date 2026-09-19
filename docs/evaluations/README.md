# 布局评测使用说明

这套工具回答“同一订单在不同算法版本中，具体哪些指标变了”。它离线调用当前本地求解器，不调用 AI、不读取密钥、不上传订单、不改生产网站，也不自动修改算法。

## 运行与比较

在仓库根目录的 PowerShell 执行：

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe -m evaluation run --output output/layout-current.json
.venv/Scripts/python.exe -m evaluation run --baseline docs/evaluations/2026-09-19-six-case-baseline.json --output output/layout-compare.json
```

每条命令同时生成 JSON 和同名 Markdown。默认六个案例，各运行两次，每次三方案。求解器预算 15 秒、独立进程硬超时 45 秒；超时进程会结束，不将失败案例从报告删掉。`--repeats` 支持 2 至 5，`--budget`、`--timeout` 可调整，但与基线参数不同会被标为不可比。旧五案例 baseline/refinement 报告保留用于审计，不与新增案例后的集合直接比较。

输入指纹、案例来源/验收状态、指标版本、预算、重复次数和运行环境须相同；缺案例、超时、校验失败或重复运行产生不同结果时，报告明确标为不完整且命令非零退出，不输出可能误导的质量差值。不同算法版本应使用同一套评测代码和案例，指标定义变更必须增加 `schema_version` 并重建基线。报告记录 Git 提交、未提交状态、代码指纹和依赖版本。

差值为“当前减基线”。件数减少和空白缩小必须一起看，不能把少装换来的紧凑直接宣布为全面改进。比较命令没有自动的综合得分或软指标退化阈值；硬安全/数据一致性失败会非零退出，软指标变化需按方案目标审阅。两次采样不能证明性能提升，耗时应在同一机器空闲条件下多次复测。

## 现有数据的可信边界

| 案例 | 来源 | 可以证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| historical-six-sku | 既有 `_temp/user-case-40hq.json`，六 SKU、54 托历史复现 | 已保留参数下的布局和时长回归 | 原始抓取过程、实际发货重量和用户认可未经核验 |
| user-abc-test | 原任务中用户提供 A/B/C 尺寸、重量及 30/30/3 件输入 | 可追溯用户测试输入的回归 | 实际发货、顶部承重与现场认可未经核验 |
| customer-five-sku | 已有客户模板测试，五 SKU、55 托 | 客户规格模板的回归 | 每托 100 kg、顶部承重 500 kg 是测试值 |
| safety-fragile-gap | 构造的必装、易碎、间隙与承重场景 | 组合约束下的安全回归 | 现场装卸顺序和用户满意度 |
| stress-30-sku | 已有 30 SKU / 5000 件测试 | 上限输入下的可运行性与指标变化 | 全部装入或数学最优 |
| stress-5000-cartons | 已有单 SKU / 5000 箱测试 | 大件数下的性能与合法性 | 真实包装强度和运输动态稳定性 |

所有案例当前为 `unreviewed`，真实订单核验数和现场认可数均为 0。不能据此勾选“至少一组真实订单及人工认可布局验收完成”。

## 指标定义

- 安全：复用 `validate_solution` 的完整支撑、边界、碰撞、朝向、尺寸、重量、叠放、承重、必装和间隙校验；同时核对逐件计数、未装数量、重量与每步区域件数。柜门预留是现有软目标，报告实际距离与目标值，不误作新的硬规则。
- 空白面积：在同一底面高度，将货物平面外接矩形面积减去矩形并集面积。分别记录底层和上层单层最大值，单位平方米。包含凹口、间隙，不等于封闭“窟窿”、可继续装入面积，也不跨不同高度混算。
- 支撑率：逐个上层件底面与恰好接触的下层顶面并集的重合比例，记录最小值。交叠支撑面不重复计数；没有上层时为 N/A，不能伪报 100%。100% 支撑不表示运输过程中不会侧倾。
- 重心：报告前后、左右偏差百分比，沿用当前服务口径。空柜与部分装载不能仅按空白面积与满柜比较。
- 步骤/货区：沿用当前算法生成的步骤，不等于实际搬运距离、工时或叉车可达性。
- 求解时间：一次产生三方案的本地耗时，中位数与最大值；另记校验/评测时间。排队、网络、模型和前端渲染不在此范围。

## 后续导入真实订单

本工具接收 `/api/v1/pack/jobs`（旧版 `/api/v1/pack`）的请求体 JSON，毫米/克制，不接收整份 HAR、截图或带 Cookie/Authorization 的请求头。可以使用浏览器 F12 的 Network 选中该请求，在 Payload 中复制请求体；也可由开发者从已有资料转成相同格式。不要复制 AI 配置或请求头。

```powershell
$env:PYTHONPATH = 'backend'
.venv/Scripts/python.exe -m evaluation import-request output/private-request.json --output output/private-cases/order-001.json --id order-001 --source-kind real_order --evidence 'Confirmed physical order; anonymous reference 001' --verified-on 2026-09-19 --limitation 'No field-approved layout yet'
.venv/Scripts/python.exe -m evaluation run --cases output/private-cases --output output/private-order-report.json
```

导入只保留已定义的物理字段，替换柜名、货号和锁定货物 ID，删除 AI hint/Key/额外元数据；不改变尺寸、数量、重量、间隙或规则。案例来源说明由操作者填写，必须不含客户信息；工具不会替你证明“真实”或“已认可”。缺实际重量时应归为 `historical_reproduction`，并在 `--limitation` 注明假设。导入默认独占创建，已有文件不会被覆盖。原始输入仍在用户指定本地位置，不会被删除或上传。

运行前检查脱敏文件。尺寸和数量组合也可能是商业资料，`output/` 默认不提交；只有明确可分享的案例才纳入仓库。人工确认满意、现场装载照片与实际布局坐标应另建可核验记录，本轮未实现自动接受现场参考布局。
