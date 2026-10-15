# 初始窗口记忆修复后的全量失败处理

## 最新全量

1765 已实际退出 1（82dbcc）：15530 passed、1 failed、1 skipped，546.65 秒，
完整输出 full-regression-1765.txt。此前 1728 的 38 个失败场景本次均通过；
新增唯一失败为 MCP elicitation 的 accept/code_mode/tools-call/http 场景，
并非本文件记录的 SSE resume deadline 用例。运行期间未改生产或测试。
该新失败尚未定因；不以窗口召回测试全过而宣称整个全量通过。

全量 1728 原始结果保留：15481 passed、38 failed、1 skipped，556.78 秒，
退出 1。前轮已适配 memory_read_policy/reset_runtime 三项。本轮未改生产代码。

## 其余 34 项记忆召回预期

冷恢复不等于新窗口。Codex `record_context_updates_and_set_reference_context_item`
只有缺失 reference 才走 initial；`MemoriesExtension::contribute_thread_context`
在 initial 聚合，不参与普通 world-state diff。Corki 对应新窗口生命周期修复后，
应在真实新窗口验证新生成摘要，不能恢复逐 Step 重读以满足旧测试。

本轮六文件采用公开 `runtime.compact()`，再执行普通 recall Turn，检查原始历史
前缀完全保留，没有直接改数据库、伪造 CompactionItem 或绕过 Runtime：

- memory_model_http：6 个 provider/API 组合保留准确端点、认证、普通请求字段、
  客户端关闭、源历史与新摘要断言，准确增加一次 main 摘要请求。
- memory_pipeline_runtime：12 个生成/源终态/取消观察者组合与 1 个显式 note 冷
  合并场景，单独记录普通摘要调用，不占用原搜索→读取→引用的 scripted 序号。
  仍断言实际工具结果、源 thread 引用、usage_count、note 字节及合并次数。
- memory_agent_runtime：3 种直接/流式/嵌套 worker 路由，保留文件修复、资产、
  worker 清理及无主线程工具调用断言；新窗口读取 RECALL_VERIFIED。
- memory_change_evidence：10 个删除/修改 × 发布故障组合，在最后冷恢复 run 后
  压缩再召回 CURRENT_ONLY；保留 baseline、watermark、部分发布与旧源移除断言。
- long_term_memory 与 memory_retention：各 1 个旧失败场景，保留污染源失效、
  清理批次上限、实际合并次数、归档不删除及有效来源索引断言。

五文件先 62 passed（55928 实际退出 0，5a8405）；合并前轮三个文件与 pipeline
后九文件 119 passed / 23.77 秒（c670ee；38938 随后实际退出 0，301627）。
其中包含全部 37 个原失败 memory 场景，并非只运行修订断言。

## SSE deadline：先证明旧预期问题，再分离故障阶段

原全量仅 tools/list-401 的 GET 数为 0，配置的 30ms 覆盖完整请求。无修改串行
12 通过（833bbb）不是修复证明。独立内存诊断给原 POST SSE 增加 60ms 延迟，
tools/list 与 tools/call 均稳定在原 321 行“GET 必须一次”断言失败（7bf31d）：
截止时间可以合法发生在 GET 之前。没有证据表明原全量的精确延迟源是什么。

`MCPClient.request` 外层与 `_http_request` carrier 各有 ActiveTime timeout；
http_recovery._run 的 wall deadline 只在重试时参考。`sse_resume._reconnect`
拒绝 GET 后关闭响应，在指数退避中接受调用方取消，不把 GET 错误交给 POST 恢复。

修订测试显式覆盖 before_get / after_rejected_get × 六 HTTP 状态 × list/call。
准备阶段允许 10s，但进入指定故障阶段后将真实外层 ActiveTime budget 重排为
30ms；不是手动抛 TimeoutError，也不是取消整个测试任务。before_get 阻塞 SSE
60ms，要求零 GET/一个关闭；after_rejected_get 在拒绝响应关闭后启动 30ms，
要求一个 GET/两个关闭。均保持 POST 一次、initialize 一次、会话身份不变、拒绝
GET 正文不可读。没有放宽为任意 GET 数，也没有修改生产重试/超时策略。

夹具开发失败如实记录：58866 为 12 fail/45 pass（0bb55d），误捕获未被当前路径
使用的 recovery asyncio.timeout；38087 为 24 fail/33 pass（c7ec1f），遗漏了
carrier 的第二个 ActiveTime budget。改为源码确认的外层 request budget 后，
完整 SSE 文件 57 passed / 1.05 秒（66055 实际退出 0，68d244）。全项目 ruff
check/format --check（1311 文件）通过（d9a937）。尚需扩大回归和刷新全量。

最终十一文件联合 212 passed / 24.22 秒，90907 实际退出 0（34c232）：两文件
MCP SSE/HTTP recovery 加上述九个 memory 集成文件，4 workers/loadfile/禁重启。
本轮 diff --check 通过（a3dd9b）。新非 CLI 全量 1765 已启动（9a7167），与
1728 同范围/环境，8 workers；资源和句柄跟进见 remaining-implementation-priorities.md。
没有本轮新生产改动，不能把旧全量失败日志覆盖成通过；最终全量结果仍待确认。
