# MCP 用户等待与响应交付的计时边界

## 源码依据与适用范围

参考 commit：`ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
Codex `codex-rs/rmcp-client/src/elicitation_client_service.rs::create_elicitation`
以 pause guard 包围宿主回答 future；返回后 guard 释放，不包含后续传输交付。
`rmcp_client.rs::active_time_timeout` 暂停时继续等待 operation，恢复时只使用剩余预算。
Corki `src/corki/mcp/inbound.py::_elicit` 同样只包围宿主回答，随后 `_reply`；
`active_time.py` 用计数暂停和剩余 deadline 支持嵌套预算。
本项是通用 MCP 行为，不引入参考文件中的官方专有 elicitation 扩展。

## 原始失败与确定性证据

全量 1765 的唯一失败原文未完整保留，不能断言其根因就是响应交付超时。
原 36 参数串行/并行重跑通过也不算修复证据。后续注入回答后的 250ms
HTTP 交付延迟，在 200ms 请求预算下复现 success 断言失败，完整 observation
为 MCP tool timed out、execution outcome may be unknown、Do not automatically retry。
这是合法超时边界的确定性证据，并非原始失败的精确重建。

测试现在分开两类要求：普通 tools/call 使用 1s 预算、用户等待 1.1s，仍必须
成功；专门的交付超时场景保留 200ms 预算和 250ms 延迟，要求模型收到未知结果
错误且 initialize/tools/call 各一次，不重放。宿主私有内容隔离、回答匹配、
accept/decline/cancel、HTTP/stdio、三种工具模式和关闭断言全部保留。
没有修改生产超时或恢复逻辑。

曾将 tools/list 也改为 1s/1.1s，导致 18 个列表场景失败：optional startup
有独立有界准备宽限期，脚本模型不能假设超出宽限后目录仍已可用。该实验已撤销，
列表保持原 200ms/300ms。不能把此实验抹去或视为需要延长生产 startup 超时。
该次命令还误拼了 sandbox compiler 环境变量；不作为有效验收记录。

## 当前联合验证

2026-09-13 确认无 pytest/execnet 活动进程，旧诊断句柄 36122 已不存在。
此前丢失输出的批次不认定通过；重新执行以下命令（当前 compiler 显式设置）：

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox \
.venv/bin/pytest tests/integration/test_mcp_elicitation.py \
tests/unit/mcp/test_elicitation.py tests/unit/mcp/test_elicitation_wire.py \
tests/unit/mcp/test_sse_resume.py tests/unit/mcp/test_http_recovery.py \
tests/integration/test_code_mode_error_channels.py \
-n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

句柄 46356 实际退出 0，244 passed / 35.95s（389e19）。独立临时目录、
离线 HTTP、独立 stdio；4 worker 控制子进程负载，机器 12 核/18GiB，
运行中查询内存空闲比例 34%。本结果不替代全量：1765 仍是失败记录，
下一次全量须重新检查资源并保留失败诊断。

负向对照已完成：在独立 Python 进程内临时将 `ActiveTime.pause` 替换为
`nullcontext()`，调用实际 HTTP/tools-call/Code-Mode/accept 成功场景；
断言确实拒绝 MCP 超时结果，输出
`NO_PAUSE_NEGATIVE_CONTROL: observed timeout rejected by success assertion`。
50523 实际退出 0（944b1d），同批 ruff check / format --check 通过。
补丁只在进程内存在，没有写入生产代码；它证明 1s 普通场景仍能发现暂停失效，
不是把等待缩到预算以内来获得通过。Code Mode 会等待宿主交互结束才发布结果，
所以负向故障在最终 observation 断言被发现，不要求中途 task.done() 为真。
