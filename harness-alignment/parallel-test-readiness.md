# 测试并行接入与验证

## 非CLI单元测试全组已并行验证

当前代码执行tests/unit（仅排除tests/unit/cli），8个worker、--dist=loadfile、
--max-worker-restart=0：5859通过、1跳过、23.00秒、退出0（e0fd66）。跳过仍是
文件系统拒绝非UTF-8文件名；未放宽断言、增大测试超时或自动重启worker。
检查了根conftest进程内catalog隔离、临时编译器receipt/manifest写入、技能脚本
临时CORKI_HOME、Git/SQLite临时目录与子进程目标；loadfile保留每文件内测试顺序。
命令仅配置null keyring和当前CORKI_TEST_SANDBOX_COMPILER，未改用户环境或源码。

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/unit --ignore=tests/unit/cli -n 8 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs
```

这关闭“单元测试只做局部并行”的待办，不代表集成组隔离已经全验。完整核心回归
还需刷新非CLI integration组；当前无测试进程待续读，session88800已经退出0。
无同范围串行计时对照，不报告精确加速倍数。

## 当前实现：8个worker首批通过

pyproject.toml的dev extra新增pytest-xdist>=3.6,<4；uv lock --offline仅新增
pytest-xdist3.8.0与execnet2.1.2（c94a5e），使用本机缓存安装这两个包（d2280c），
无其它包升级。uv lock --check --offline、pip check、全src/tests Ruff与1254文件
format及git diff --check通过（7920d9）。默认pytest仍不强制并行，按审计后范围显式-n。

首批四文件通过8个worker调度：74通过、无跳过/失败，pytest耗时3.91秒、外部wall
4.99秒（342dbd）。范围为Interrupt输出/身份契约、取消意图冷恢复、真实取消写入
故障和关闭回收。各测试自己的tmp_path包含数据库、home和脚本PID文件；配置/
runner monkeypatch为进程内状态，无固定监听端口，进程测试只回收自身创建的PID。
使用--max-worker-restart=0，不掩盖worker崩溃。未改断言、超时或测试选择条件。

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring /usr/bin/time -l .venv/bin/pytest tests/unit/evaluation/test_interrupt_hook_contract.py tests/integration/test_cancellation_intent_recovery.py tests/integration/test_interrupt_recovery.py tests/integration/test_interrupt_shutdown.py -n 8 --dist=load --max-worker-restart=0 -o addopts='' -q --tb=short -rs
```

time报告maximum resident set size134021120字节、零swaps；此指标不冒称整个worker
进程树的同时峰值。尚未做同范围串行计时对照，不给出加速倍数，也不宣称全量已可
安全并行。后续继续分组隔离检查及完整目标剩余项，不将并行工具接入变成新的长期外围任务。

## 以下为接入前的检查与计划

最新状态：串行核心回归96933已退出0，14913通过/7跳过（846f9d）。现在可开始
下方并行依赖配置及隔离验证；以下“仍在运行”描述为准备阶段的历史条件。

用户已授权独立测试最多96个进程worker；完整规则已同步外部goal文件和objective.md。
当前串行核心回归session96933仍在运行，不中止、不重复启动，不修改该批代码/测试。

## 已检查的环境与隔离证据

- sysctl报告12个逻辑CPU、19327352832字节内存（18GiB），不是96核机器。
- pyproject.toml开发依赖未包含pytest-xdist，当前环境pip show也未找到该包。
- tests仅有根conftest.py；自动fixture逐测试替换进程内MCP目录缓存，临时worker
  目录由tempfile.mkdtemp分配。这些是局部隔离证据，不是全量可并行证明。
- test_plugin_package_enablement中的动态插件写入发生在tmp_path/home下，不能把
  脚本中的Path(__file__).write误认成项目源码写入。
- test_execution_permissions实际listener绑定127.0.0.1端口0，使用系统分配端口。
  其它搜索命中的固定地址可能是纯配置/mock文本，仍需逐项追踪，不能仅按文本分组。

以上依据16c7f7/f8085d；当前只读检查，不宣称已完成隔离审计。

## 下一执行顺序

1. 跟进96933至终态，记录失败和跳过原因；不要为加速重启正在运行的回归。
2. 在开发依赖中接入pytest-xdist并验证安装，保持生产依赖不变。
3. 先审计并运行无共享可变资源的单元测试子集，初始8个worker；记录峰值资源与耗时，
   再决定是否增加并发，96仅为上限。测试创建额外子进程的成本一并计算。
4. 集成测试逐组检查数据库/home/文件/端口/原生编译器产物/进程回收与超时假设。
   隔离不足则修复隔离或显式串行分组，不通过删减覆盖和放宽断言取得并行绿灯。
5. 保留并发失败证据并定向串行复现；最终汇总并行组与串行组的完整测试范围，
   不将串行复跑通过改写为原并发通过。CLI对齐继续暂停。
