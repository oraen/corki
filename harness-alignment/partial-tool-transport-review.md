# 流中工具调用、失败重试与冷历史（A7/A8/E5）

## A3 混合输出补验与当前收敛

重新追踪 Codex session/turn.rs 的 OutputItemDone→handle_output_item_done，分别
记录last_agent_message、in_flight及needs_follow_up；退出读流后仍drain_in_flight。
Corki graph 的 ModelItemCompleted 分支先验证并append_partial_item，再输出正文/
reasoning或live.submit工具；最终ModelCompleted必须包含先前已完成item，提交
model step后分别完成展示，不能因有正文就忽略同批工具。

当前 test_recovery_and_concurrency 的混合测试18组合：批响应/逐项完成/结果写入中
取消 × Runtime/CLI恢复 × 无填充/历史分页/26个预热Step。每例Reasoning、Working
和两个工具调用恰好一次，工具结果顺序固定；冷Runtime仅一次后续采样，两工具各执行
一次，CLI40/100列各内容只显示一次，重复resume无待恢复项。
六文件联合84 passed（eaf5f1，21.79秒），收集早于下述新增四个混合HTTP分支。

test_partial_tool_transport_boundary新增mixed维度，共8例。Responses先完成
reasoning和Working消息，再完成工具item，确认工具执行后以EOF/ReadError断流。
这三项partial及两个文本种类在持久历史中保持唯一；重试包含已完成Observation，
之后冷打开历史前缀不变、工具不重放。Chat对应分支只发送Working与tool_calls
delta，无完成标记，因此不持久化这些未提交项、不提前执行；成功重试后才调用。
Chat该分支没有注入provider特定reasoning扩展，不冒称测试了该协议。

新增后8 passed（4815b6，2.29秒），静态cb4f65通过（1182文件、77包）。
A3“同时正文/reasoning/工具”的核心分流及不提前结束已有上述直接证据，状态可收敛。
这些脚本与MockTransport测试不是模型质量证明、物理进程强杀测试或A7/A8全部窗口证明；
本轮无生产修改，亦不据此关闭所有错误终态和资源所有权条目。

## 源码与协议边界

参考 core/src/session/turn.rs：try_run_sampling_request 在 OutputItemDone 后调
handle_output_item_done 并收集 in_flight，流失败仍 drain_in_flight，逐项记录工具
结果；run_sampling_request 的后续尝试从 clone_history 构建 prompt，而非重发旧输入。
这不意味着任意工具参数 delta 都可以提前执行，也不提供外部系统 exactly-once。

Corki Responses 的 output_item.done 转为 ModelItemCompleted；graph 先保存 partial
item、再 live.submit。失败路径等待 live.finish、补齐持久 Observation，再保存
ModelFailure 并重建下一请求。Chat 的 _stream_once 没有对应逐项完成事件：它缓冲
调用，只有完成标记及响应消费成功后才 yield ModelCompleted；没有终态的调用片段
不能提前进入工具执行。这是普通传输可用提交信号的差异，不能凭收到 delta 就声称
已拥有一个完整、可执行、持久化的调用。

## 新增真实适配器组合证据

test_partial_tool_transport_boundary.py 共 4 例：普通 Responses/Chat × EOF/ReadError。
Responses 首响应发送完整普通 function call item 后等待工具实际执行，再断流；
Chat 首响应只发未终结的 tool-call delta，断流时必须尚无执行。重试时前者已有
OBSERVED_ONCE，后者没有工具结果，重新成功完成调用后才执行。

断言：只有一次采样重试；首采样无 ModelCompleted；Responses 有一个 partial
call、Chat 无 partial call；canonical 用户输入、工具调用和工具结果各一条。
随后关闭并冷建相同 Thread，检查原请求前缀及完整历史前缀原样保留、执行仍一次。
响应关闭一次。这里的冷恢复发生在该 Turn 正常完成后；崩溃中恢复由既有独立故障
窗口测试提供证据，不能把本测试描述成已杀进程重启。

初轮 fixture 漏传适配器必填 capabilities（d7c600），未到达业务断言，不算生产
缺陷红灯。补齐后 4 passed（f6307f）。联合 history_retry、response_errors、
recovery_and_concurrency、tool_claim_shutdown、tool_argument_recovery 共
78 passed、0 skipped，15.69 秒（6bb4e1）。静态 6d2ab0 全通过：ruff check/format、
compileall、uv pip check、git diff --check，1136 文件、77 包。所有进程已退出。

本轮新增组合行为证据，未修改生产逻辑；测试是脚本 HTTP 响应，不能证明真实模型
选择质量。只关闭上述普通接口提交/断流/重试/完成后冷历史的验证缺口，不据此
关闭 A7/A8/E5 全范围；未知结果、任意外部副作用、所有终态/关闭组合仍须分别验收。
