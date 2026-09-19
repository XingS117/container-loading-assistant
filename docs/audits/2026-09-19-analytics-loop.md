# 操作统计闭环验收

本轮前端将输入流程、每次计算、失败/超时、方案选择、人工应用、打印请求和结构化反馈关联，历史保留原计算标识。新增字段白名单和统计故障隔离，默认测试单/完整示例明确标记 is_example。

129 项前端测试通过，覆盖关联一致、重算独立标识、输入完成不重复、密钥/任务编号/订单字段过滤、统计同步异常不影响主流程。TypeScript/Vite 构建通过；后台计算代码未再改动，沿用本轮已通过的 218 项后端回归。

本地浏览器使用真实 Umami 脚本及接收服务，8 类事件（input_started、input_completed、calculation_started、solutions_generated、solution_selected、solution_feedback、solution_adjustment_submitted、export_print）均返回 HTTP 200。input_id 一致，计算与后续行为 attempt_id 一致，is_example=true；补充文字测试标记未出现在上报中，只发送 has_note。实际接收曾乱序，因此增加客户端时间及序号用于排序，不使用响应先后作为用户操作先后。

已验证的是工程上报与服务器接收。未登录 Umami 账号后台，未取得客户使用报表；不能据此声称真实成功率/满意度提高。去重、分母、历史/示例过滤与未知终态处理见 `docs/analytics-measurement.md`。发布测试需从客户统计中排除。
