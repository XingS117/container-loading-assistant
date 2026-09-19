# 装柜操作统计口径（schema_version=2）

统计沿用部署中的 Umami。每次页面加载分配随机 journey_id；一次进入输入页分配 input_id；每次真正提交计算分配 attempt_id。job_id 是读取临时任务结果的凭据，不进入统计。结果历史保存 attempt_id，恢复后 source=history；没有标识的老结果使用 legacy，不纳入按计算次数的漏斗分母。

| 指标 | 事件与去重/分母 |
| --- | --- |
| 输入完成率 | pack_input_completed 的不同 input_id / pack_input_started 的不同 input_id。完成指首次提交通过前端校验的清单，不指打开默认样例即完成；同页失败重试不重复完成事件。 |
| 计算成功率 | pack_solutions_generated 的不同 attempt_id / pack_calculation_started 的不同 attempt_id。initial 与 recalculate 分开；预算安全备选由 budget_fallback 标注，也算接口成功，不等于最优。 |
| 计算失败率 | pack_calculation_failed 的不同 attempt_id / 开始的不同 attempt_id；按 category/reason 分组。若浏览器提前关闭、屏蔽统计或断网导致未收到终态，单列未知，不直接算失败。 |
| 超时率 | pack_calculation_timeout 的不同 attempt_id / 开始的不同 attempt_id。它是失败子集，不能与失败相加。last_phase 为最后已知阶段，非推测算法进度。 |
| 计算耗时 | 成功/失败 elapsed_ms 统计 p50/p95，单位毫秒，包含客户端等待和轮询，不与离线 solve_s 混比。 |
| 切换/导出 | pack_solution_selected 与 pack_export_print 按 attempt_id/profile 关联。打印事件只代表请求打印/PDF，不证明文件已保存或现场使用。 |
| 人工调整 | pack_layout_workbench_applied 为一次应用调整；moved_pieces/removed_pieces 为本次前后变化，不是鼠标拖动次数。按有调整的不同 attempt_id / 成功的不同 attempt_id，或每成功单平均应用次数。 |
| 满意/需调整 | pack_solution_feedback 同 attempt_id/profile 按时间取最后一次；adjusted 区分调整后反馈。不将未反馈算满意，不将测试人员点击算客户现场认可。 |
| 调整原因 | pack_solution_adjustment_submitted 仅传预设 topics 和 has_note，自由文本不上传；当前反馈不会自动变更算法或布局。 |

样例与真实使用必须分别筛选 is_example。is_example=false 只说明没有标记为示例，不代表已验证实际发货。source=history 的重复导出和反馈可独立统计，不新增计算次数。开发环境和本次发布验收产生的数据不计入客户满意度基线。

事件字段白名单会丢弃订单名称、SKU、尺寸/重量明细、API Key、job_id、补充文字和未知字段；统计脚本异常/网络拒绝不会阻断计算。脚本初始化前最多缓冲 32 个事件，加载后发送；屏蔽脚本或关页仍可能丢失事件，不能作为财务或运输审计账。

网络接收可能乱序。事件额外携带 occurred_at_ms（客户端时间）和 journey_id 内递增 event_seq；同页反馈顺序按 event_seq，跨页按客户端时间辅助核对，不直接按 HTTP 响应先后判定最后反馈。客户端时间不作为可信运输审计时间。

## 核查与使用

1. 在 Umami 当前站点的 Events 页面按上述事件和 schema_version=2 查看数据。先按生产域名、日期范围、is_example=false、mode 分组；不要把旧版无关联标识的事件混入漏斗。
2. 关联分析按 attempt_id，输入漏斗按 input_id；反馈取最后状态。导出事件明细后才能做跨事件去重，不直接用按钮点击总数相除。
3. 每周对比固定日期窗口的成功率、超时率、p95、人工调整比例、最后满意比例，同时列出样本量和未知终态。少量发布测试不能证明实际改善。
4. 浏览器 Network 中检查 Umami /api/send 的事件名、上述关联字段和 2xx 接收；这证明上报被接收，不等于已核查统计后台的聚合报表。

本轮只将可复现事件与接收结果作为工程验收；账号后台、真实客户使用样本及连续下降趋势须另外留存截图或导出数据。不要在未取得这些证据前宣称业务指标提升。
