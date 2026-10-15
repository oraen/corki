# 记忆模块当前组合回归

本次在发布故障、重试、冷 Runtime 与 skills 修复测试全部加入后执行。
没有生产修改，不改变完整 goal 的 A–F 范围。

## 已终态的两组

环境：CORKI_TEST_SANDBOX_COMPILER 指向当前工作区
src/corki/_native/sandbox/corki-sandbox（已验证文件存在），
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring；离线脚本模型与模拟网络。

1. `.venv/bin/pytest -o addopts='' -q -ra tests/unit/memory tests/integration/test_memory*.py --tb=short`
   788 passed，0 skipped，204.10 秒（5ede60，session 14004 已退出 0）。
2. 同样 pytest 参数，显式运行 integration 的 test_thread_memory_initialization、
   test_thread_memory_mode、test_long_term_memory、test_native_memory_citation_runtime、
   test_mcp_memory_admission、test_ephemeral_runtime、test_runtime_shutdown 七个 .py。
   120 passed，0 skipped，19.10 秒（766d53，session 87534 已退出 0）。

第二组补充文件名不以 test_memory 开头的相关入口，包含非记忆专属的 Runtime
关闭检查；不能把文件 glob 当作目标覆盖定义。两组全部结果均已终态，没有活动句柄。
Ruff、1125 文件格式、compileall、77 包兼容和 diff 检查通过（e929e9）。

## 覆盖解释与未证明事项

本轮回归同时包含来源/claim/租约、共享目录/worker/权限、发布故障与重试、
重置/遗忘、工具读写/搜索/引用、取消/关闭，以及冷模型 HTTP/预算等既有专项。
最新新增的发布故障直接断言与参考调用链见 memory-publication-review.md。
已有专项的源码映射见 memory-source-review.md、memory-forgetting-review.md、
memory-close-recheck.md。全绿证明当前采集的行为测试可同时成立，不自动证明
所有 D1–D7 场景、未知外部副作用 exactly-once 或全部 A–F 已完成。

附带源码复核：Codex secrets/src/sanitizer.rs 的匹配顺序为 Bearer、sk、AKIA、
赋值形式；Corki memory/sanitizer.py 使用对应启发式。phase1 的 contextual user
片段过滤使用 ASCII 不敏感 marker 与 trim，Corki transcript.py 对应过滤 AGENTS/
skill、去除非事实上下文与 retained 副本。Corki 先处理结构化字符串再 JSON 编码，
避免脱敏破坏 JSON；相关单元与真实提取发布测试在第一组中通过。
这些规则是 best-effort，不是“所有凭据均能识别”，也不证明真实模型抵御任意
恶意记忆内容。OS 掉电/进程强杀和真实模型质量没有在本轮执行。
