# A6/A7/E5/E7：兼容 Patch 线程的取消所有权

参考 Codex `core/src/tools/parallel.rs::handle_tool_call_with_source` 的取消
终态归属：执行尚未达到可确认终态时，宿主必须等待已启动调用的清理，不能
先宣告 Turn 终态再让本地副作用继续。Corki 受执行权限管理的 patch
走独立编译器链；本项仅针对 `execution_permissions=None` 时保留的
`ApplyPatchTool.execute` 兼容分支，不扩展到官方产品或 CLI 风格。

旧分支直接 `await asyncio.to_thread(_apply_operations, ...)`；任务取消只
取消 awaiter，不停止 Python 工作线程。真实 Runtime/SQLite/ScriptedModel
测试门控 `_apply_operations`，取消活动 Turn 后在门未释放时观察到
consumer 已结束，而文件线程仍在等待：**1 failed / 0 passed**，反例
不是单纯 helper 测试。此时释放门仍可写入文件，违反 Turn 与资源关闭
顺序，也不能把尚未观察到的副作用结果标成确定成功。

现用 `joined_tool_work` 持有已启动线程至实际退出，取消期间可重复接收
取消但不提前释放；线程失败不能覆盖取消控制流。新增取消/关闭 × 写入
成功/失败 4 个 Runtime 场景通过，均要求门未释放时 Turn/close 不结束，
释放后唯一 TurnCancelled，写入成功时文件存在、失败时文件不存在。
兼容 patch、受控 patch diff、记忆 reset 交错和工具提交相关五文件
**37 passed / 38.61s**，4 workers/loadfile/禁重启，实际退出 0。
首次扩大命令误写不存在的 `test_tool_result_commit_cancellation.py`，
pytest 零收集退出 5；按实际 `test_post_tool_commit.py` 重跑完整目标范围。

全量前资源检查：12 逻辑核、18 GiB 内存、系统空闲约 36%，swap
已用约 25 GiB，且未见 pytest/execnet/sandbox 测试进程；因此采用
8 workers/loadfile/禁重启，而非机械使用用户允许的 96 上限。
首轮非 CLI 全量在 8 workers 下出现大量与本修复无直接关联的超时：
Pre/Stop Hook 的 5 秒门禁和 MCP stdio 的 0.5 秒初始化。运行至约
20 分钟、21% 时为诊断主动发送 SIGINT，实际退出码 2，结果为
**29 failed、3422 passed**，不是完整回归或通过。各取一项代表用例
串行复现均通过（2 passed / 16.54s）；这只说明并发/机器负载值得
检查，尚不能证明 29 项全为环境噪声或本修复无回归。随后将失败聚集的
六个文件降到 2 workers 重跑，因同类超时持续出现而在 154.90 秒后
为诊断 SIGINT，实际退出 2：**16 failed、111 passed**。traceback 包括
0.5 秒 MCP stdio 初始化、0.2 秒 HTTP 初始化清理，以及 5 秒 Hook
门禁；暂未出现 patch 所有权测试失败。此时系统 `uptime` 报告 load
average **503.50 / 448.34 / 382.77**（12 逻辑核），`vm_stat`
空闲页 4254（每页 16 KiB），`fseventsd` 约占一个核。高负载足以
污染短期限测试，但不等于每个失败已证明为环境原因；未修改产品超时或
测试期限来掩盖。

负载回落后以互斥文件集合重新覆盖完整非 CLI 范围：短期限敏感的六文件
单 worker/loadfile/禁重启 **459 passed / 59.83s**；其它
`tests/unit tests/integration`（排除 CLI 与上述六文件）
4 workers/loadfile/禁重启 **15250 passed、7 skipped / 734.74s**。
两批实际退出码均为 0。全范围单独 `--collect-only` 得 **15716 tests**，
恰等于两批 459 + 15250 + 7，无分组遗漏；合计 **15709 passed、
7 skipped**。全量前重新检查 12 逻辑核、1 分钟 load 6.27、
无其它 pytest/sandbox 测试进程及可用内存；选择串行敏感组和并行
4 workers 其余组，不把 96 上限当固定值。七项跳过为六项所需旧编译器
不存在、一项文件系统不接受非 UTF-8 文件名，均未冒充执行通过。

后续仅新增 C8 压缩计划保存故障八个测试，未再改本项生产代码；同一分组
命令重新执行，敏感六文件 **459 passed / 55.58s**，其余文件
**15258 passed、7 skipped / 726.42s**，两批退出 0。全范围独立
收集 15724 项，合计 **15717 passed、7 skipped**，这是当前较新的
非 CLI 基线。重跑前再次确认 12 逻辑核、1 分钟 load 6.75、无其它
pytest/sandbox 测试进程；七项跳过原因不变。

复现命令（均设置 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` 与
`CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox`）：

```text
.venv/bin/pytest tests/unit/mcp/test_initialization_contract.py tests/unit/mcp/test_tool_catalog_wire.py tests/unit/mcp/test_inbound_service.py tests/integration/test_pre_hook_recovery.py tests/integration/test_stop_pending_recovery.py tests/integration/test_stop_hook_recovery.py -n 1 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs
.venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' --ignore=tests/unit/mcp/test_initialization_contract.py --ignore=tests/unit/mcp/test_tool_catalog_wire.py --ignore=tests/unit/mcp/test_inbound_service.py --ignore=tests/integration/test_pre_hook_recovery.py --ignore=tests/integration/test_stop_pending_recovery.py --ignore=tests/integration/test_stop_hook_recovery.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs
```

本项不声称强制终止卡死线程，或
外部进程/远端 Patch 事务具有 exactly-once 保证。
