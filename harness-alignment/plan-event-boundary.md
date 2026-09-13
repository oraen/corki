# 计划工作状态与Code Mode事件边界

## 计划 → 手动压缩 → 分支 → 冷启动的组合证据

本轮重新核对固定原生 plan.rs::handle_call：计划调用发布 PlanUpdate，工具结果
只返回普通确认；compact.rs:357–398、670 起以用户消息与摘要构建替换窗口，
没有把压缩前计划调用重新执行或重新发布为事件。

扩展 test_thread_fork_plan 为压缩/不压缩两条真实 Runtime 路径。压缩路径断言：
摘要请求包含旧计划调用；压缩不发 PlanUpdated，原持久前缀完整保留；fork 复制
旧结果的 typed state_update 并重映射调用身份，但首次模型请求不再含压缩前的
计划调用；分支只发布其新计划；冷启动不重发旧计划，模型保留摘要和分支的新
计划历史，源 Thread 展示快照始终未变。摘要只生成一次，没有恢复时重新采样。

初批 17 passed（0330ad，94298 退出 0）；扩大七个 fork 文件、plan_contract 与
storage/plan_explanation 共 54 passed、7.68 秒（b4d9eb，46315 实际退出 0），
4 workers/loadfile/禁重启，null keyring、当前 sandbox compiler。Ruff、格式及
diff check 通过。只补测试证据，没有生产改动；15450 全量早于这一新增组合用例。
该组合从 D1 的笼统“其它边界待收敛”中明确核销，不推导 Code Mode store、外部
进程会话、发现状态等其它工作状态均已验证，也不扩展成长期记忆质量保证。

固定参考core/src/tools/handlers/plan.rs::handle_call每次解析UpdatePlanArgs后
send_event(PlanUpdate)，返回普通“Plan updated”及Code Mode空对象。这是工作状态
事件，不是长期记忆发布，也没有从任意计划历史生成新system指令。

Corki UpdatePlanTool保留普通结果及typed state_update；直接工具路径在graph
完成结果归档后发布PlanUpdated。但Code Mode service只保留pending_plan最后一份，
外层exec/wait结束时才转交graph，导致同一脚本的中间计划丢失且事件身份为外层调用。
实际Runtime反例test_each_nested_plan_update_is_published_with_its_call_identity
连续执行in_progress→completed，只收到completed；1失败11通过（9696ef）。

修复方案：嵌套调用结果已提交后、返回JS前发布每次计划及嵌套调用身份；保留外层
最后计划作为checkpoint状态，但不重复发布本Turn已发出的同一嵌套计划。
冷恢复只有外层缓存结果而本Turn没有嵌套事件时仍允许发布聚合状态。事件不是
跨数据库/消费者的exactly-once事务；不得为避免事件重复而重新执行已提交工具。
CLI样式不在范围内，不修改teach.md。实现与验证如下。

实现已接通：graph._execute_bound_tool在结果真实complete_tool_call之后、嵌套
ToolCallCompleted之前发布PlanUpdated，使用内层call.id。GraphRunContext只保留
最近一份已发布嵌套计划用于外层echo去重，不随历史无界增长、不写入checkpoint。
外层结果仍更新graph.plan；遇到相同计划及解释的CodeModeExecTool/WaitTool不
重复发事件。独立外层/direct更新清除旧echo记录；冷恢复无此临时记录时保留
原有外层聚合发布行为，不声称重放全部已完成内层事件。

反例已转绿，另加两次更新后脚本报错：两份已提交计划仍逐项发布，内层身份正确，
且先于各自完成事件，外层脚本仍如实标错。计划/协作模式/主工具循环/Code Mode
错误与生命周期/恢复联合132通过、0跳过（e2acfc，32.30秒）。随后加入独立更新
清理旧echo的边界，最终计划/协作/Code Mode错误定向90通过（5e21ae，16.09秒），
Ruff及格式检查通过；两次重叠测试不相加。未重跑核心全量。

D1边界：计划是一份显式工作状态及用户可见事件；参考PlanHandler没有把计划
另行变成长期记忆或自动system注入。Corki原始工具结果保留typed plan，压缩窗口
与原始归档分离；不能因为新Turn初始graph.plan为空就推导出需要重放所有历史
计划。其他工作状态/跨Thread隔离仍按D1清单收敛。
