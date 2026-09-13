# 核心扩大回归（本批已结束，不等于目标完成）

终态：09fb0c输出 **14193 passed, 7 skipped in 2232.58s (0:37:12)**；
随后2e748c确认session57623退出码0，cd005f确认PID94121已不存在。
同一批次从启动到退出未修改生产代码或测试，没有因等待重启套件。以下运行中
进度为历史记录，已由本段终态覆盖。

7个跳过项为下文已单独核实的1个macOS非UTF-8文件名条件项及6个历史helper
条件项，不计通过。范围排除CLI单元、CLI命名集成和e2e；不能称全项目测试。
这是同步Post解析、原子保存、冷恢复及既有核心实现的扩大回归基线；不证明
异步Post已实现，也不关闭A–E仍开放的故障矩阵。下一步新增真实异步Post反例，
再接入任务所有权、模式解析、采样边界及持久化恢复。

最新续读08878c：仍为session57623，已到92%，持续产生结果且未见失败标记。
这是测试执行进度，不是目标完成比例；尚无终态或最终通过总数。此次仅补充
async-post-tool-review.md中finalize只识别用户输入的实际接入缺口，未修改
生产代码或测试，未重启当前回归。上一轮/本轮等待均由同一活句柄确认。

最新续读7663cd：session57623约81%，仍输出新结果且未见失败，不是终态。
单独重跑test_project_trust_selection.py -rs核实文件系统拒绝非UTF-8文件名，
对应test_non_utf8_worktree_backlink_and_lossy_config_key跳过；该专项36通过1跳过
（95f77f，session24391已由b2ec6e确认退出0）。这证明该条件项未执行，不能
归类为通过；主套件最终跳过总数仍等终态确认。被测生产代码保持不变。

后续六个历史helper条件项已单独用-rs核实：pre-metadata双入口2项、pre-native-
patch/read-only、pre-patch-approval、pre-patch-delta、pre-native-fs各1项。
原因是未设置对应CORKI_TEST_PRE_*的历史编译器；当前compiler不能替代，因为
这些测试要求旧版本拒绝新字段。专项b02262为8通过5跳过35未选（-k old_同时
匹配其他old场景），90475d为1跳过20未选；两个进程均已退出0。不计六项通过，
也不据此断言历史二进制在所有本地目录均不存在。本次没有构建/替换编译器。

再次续读：session57623最新f79c1b仍活跃，约61%，没有失败标记；本轮只等待
同一进程并核验状态，没有修改被测代码、没有重新运行套件。继续等待终态。

续读更新：仍为session57623，最新eadc2e约51%，继续正常输出，未见失败标记。
尚未获得终态，未重启测试。期间只新增异步Post源码审计
async-post-tool-review.md，未修改生产/测试代码，因此本次运行基线未被改变。

执行命令：

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' -o addopts='' -q --tb=short
```

2026-09-12启动的exec session为57623。最新轮询b6fb81仍返回同一活句柄，
进度约45%，尚无F/E标记，已见一个skip；未获得终态，不报告全绿或通过总数。
后续先继续读取同一句柄，不因观察超时或本轮结束重新启动整套测试。
该范围不包含CLI单元/CLI命名集成及e2e，不等于所有项目测试；CLI风格对齐暂停。
本轮静态ruff/1210文件格式、compileall、77包依赖及diff检查通过（859e5e）。

已核对并修正acceptance-index的A7/B10/C8旧描述：同步Pre/Post已接入，不能仍
笼统写“执行链缺失”；具体剩余矩阵保持开放，不因扩大回归就关闭所有A–E。
同步create在运行中loop的公开兼容入口仍需用户选择是否迁移，已发非阻塞问题；
未擅自改变API，其他核心工作不因此停止。
