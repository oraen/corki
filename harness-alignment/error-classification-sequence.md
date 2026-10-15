# E1/E5/E6：同一Turn中的分类边界组合

参考固定Codex tools/src/function_call_error.rs的RespondToModel/Fatal、
core/tools/registry.rs的未知工具/载荷分类，结合model-failure-acceptance.md记录的
sampling重试所有者。Corki executor.execute将普通异常转错误Observation，显式
FatalToolError传播，finally检查新增取消计数；graph的嵌套Code Mode另有catch边界，
不能从直接调用推断嵌套Fatal一定结束Turn。

新增test_error_classification_sequence.py，通过acreate/stream实际graph运行：
首次工具ValueError → 错误结果进入下一采样 → 一次可重试TRANSPORT错误 →
携带完全相同items重采样 → 新工具调用分别FatalToolError或等待用户取消。
验证三个模型请求、两个不同工具调用各执行一次、一个ModelRetryScheduled，
最终唯一TurnFailed（保留fatal dispatch）或TurnCancelled，无TurnCompleted；
终态后resume为空且不新增模型/工具执行。本例无真实网络请求，tmp_path隔离
数据库/home，仅有进程内Event，适合独立worker。

首版自定义ModelError遗漏retryable=True，默认False导致两个夹具失败（9b61ea）；
根据base.py构造契约补齐，而非更改生产重试策略或放宽断言。两文件8worker最终
35通过/3.28秒/退出0（08c627），禁用worker自动重启；Ruff/格式/diff通过
（4f3579）。本批仅新增测试，证明同Turn普通路径分类不串扰，不代表全部E1已关闭。

独立证据：工具分类见tool-failure-acceptance.md，模型分类见
model-failure-acceptance.md，摘要取消见summary-cancellation-review.md。
仍需逐项核对Hook诊断/执行错误、后台记忆故障与构造错误的所有权；E4运行中loop
同步create无owner问题仍待公开API兼容选择，未擅自禁用该入口。CLI对齐暂停。
