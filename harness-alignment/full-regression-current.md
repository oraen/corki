# 当前全量回归与下一核心差异

## 最终结果：34854 已结束，不再轮询

终态 f3f9e6，exit 0：**13142 passed, 7 skipped in 2025.40s (0:33:45)**。
以下所有“仍活动”的文字均为历史观察，不代表当前状态。没有失败需要重跑修复。
本次启用当前 native compiler，七项跳过不能记为通过：

| 测试 | 项数 | 最终跳过原因 |
|---|---:|---|
| test_metadata_compiler_contract.py:20 | 2 | 缺实际 pre-product-metadata compiler |
| test_native_patch.py:119 | 1 | 缺实际 native-read-only compiler |
| test_native_read_helper.py:245 | 1 | 缺实际 pre-native-fs compiler |
| test_patch_approvals.py:223 | 1 | 缺实际 batch214 compiler |
| test_patch_deltas.py:110 | 1 | 缺实际 native patch approval compiler |
| test_project_trust_selection.py:164 | 1 | 当前文件系统以 EILSEQ 拒绝非 UTF-8 文件名 |

额外读取 a199b9/4244c3 确认旧 patch 和 delta 测试分别依赖
`CORKI_TEST_PRE_NATIVE_PATCH_COMPILER`、`CORKI_TEST_PRE_PATCH_DELTA_COMPILER`；
3fe673 确认最后一项是在实际 rename 失败后跳过，不是逻辑断言通过。
六项旧编译器拒绝契约仍缺运行证据，非 UTF-8 场景需支持字节文件名的环境验证。
不伪造旧编译器夹具，也不为消除 skip 改弱测试。

完成后静态检查：Ruff 全项目通过（97f511），format 1110 文件通过（1f4daf），
compileall src/tests/examples/native 通过（f28738），77 包依赖兼容（f51e13）。
期间 CLI 快捷键、搜索 Top-K、隐藏工具调用及记忆断言有后续修改，已分别有专项
证据，但此次全量不是全部最终文件的原子快照。不能用此结果直接关闭 A–F。
下一步回到 acceptance-index 的未关闭行为项（尤其 CLI 呈现、上下文组合、记忆
共享发布/污染入口），待实现稳定后再执行最终最新快照门禁。

## 最新观察与跳过项预审

最新 verified wait：34854 在 e06bd5 仍返回活动 session，持续输出至约 50% 测试
计数；本次新增输出未出现失败标记，但没有退出码，不能宣称全量通过。继续同一
句柄。此前的记忆来源与启动连续状态联合 77 通过（02d463）是独立专项证据，
详见 memory-source-review.md；运行期间后加的测试不计入此次全量采集覆盖。
本轮只更新运行记录，未修改生产或测试。等待最终失败/skip 汇总后决定回归优先级。

同一34854在89490e继续输出，约30%测试计数，尚未返回退出码/最终失败与skip汇总。
本轮属于 verified wait 加验收前置核对，没有修改生产/测试，不重启进程。
03250f再次确认参考HEAD为ddf04ad26789d040f9ef6a96736f76602e35a6cc且工作树干净。
同次仅检查变量存在性：PRE_METADATA、PRE_NATIVE_FS、PRE_PATCH_APPROVAL三类
CORKI_TEST_*_COMPILER均未配置。源码test_metadata_compiler_contract.py（两入口）、
test_native_read_helper.py、test_patch_approvals.py要求实际旧编译器才能证明拒绝
旧契约；当前后端receipt正确不替代旧版本兼容证据。等待最终-ra列表后逐项记录，
这里不是最终skip数量，也不把未执行场景计为通过。整个A–F目标未完成。

最新 poll f10bf3：同一34854仍活动，已到约13%测试计数，尚无终态。
快捷键实际后端桥接已另跑20通过（1999e1），执行取消/规则/单位扩大84通过
（69a8df）。下方快捷键待实现/桥接缺口是历史阶段，不再是当前缺口。

## 后续状态覆盖

session 34854 仍在运行，最新8ada74已到约7%测试计数。期间已实施F3快捷键与
BracketedPaste隔离，并修改/新增对应测试；因此此进程不是所有最终文件的原子快照
验收。继续读取结果用于发现跨模块问题，但不得将其冒称覆盖新采集项或全部最新CLI。
本批CLI另跑350通过（c7bc94），额外最终19单位通过（c57d6c），详见approval-scope-ui。
下方“未实施”“运行期间未修改”属于初始阶段记录，已由本节覆盖。

本轮已重新读取完整目标。上一轮是进展：新增 Runtime 媒体预算取消/关闭证据。

## 正在执行，尚非通过证据

实际启动的 exec session：34854。最近 poll 288d23 仍返回同一活动 session，输出
继续增长。后续必须继续轮询此句柄，不因观察超时、旧审计中的其他句柄而重启全量。
命令：

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest -o addopts='' -q --tb=short -ra
```

启动前通过 receipt.py 的 source_digest 与 manifest 核对原生源码及二进制：
source 2438561fd3afbd02041affe1b345d98288bb697633bfd11d6af1fab7aa049860；
binary 966d3aaf44f77a78447d8c0758c0fe9594904733da52d8e49a0d2fcb89e4c3ba
（2cdd20）。启用当前后端不等于旧版本兼容编译器 fixture 已存在，必须检查最终 skip。
运行期间未修改生产与测试文件；当前新增的审计记录不改变已采集测试。

## 下一项 F3 键盘差异的源码依据

Codex tui/src/keymap.rs 默认 approval：y=approve、a=session、p=prefix、d=deny、
Esc/n=decline。approval_overlay.rs::try_handle_shortcut 仅匹配实际 options 的
shortcuts，再 apply_selection；不是对任何请求都无条件启用全部授权范围。
exec_options 映射 d 到 Decline（不执行但继续），n/Esc 到 Cancel；界面字段名
decline 与宿主拒绝含义不同，不能机械按名字对应。Corki cli/approval.py 当前只提供
方向键/Enter/Esc/Ctrl-C/Ctrl-D，忽略字符键；确认存在交互差异，而非官方服务缺口。

下一批方案需先按实际 scope 绑定 y/a/p，d 与 n 分别保持现有 decline/cancel 语义，
明确展示提示；未提供的 rule/session 不得由按键增加授权。单键显式同意必须与粘贴
文本区分，避免粘贴 y 或输入尾部误授予权限。需要实际 pipe 键盘、真实 PTY、范围
缺失及历史/焦点测试。这里只记录已核对的差异，尚未实施，不冒称 F3 已关闭。

即使全量通过，也不能替代 acceptance-index.md 的 A–F 逐项收敛。明确剩余渲染与
键盘差异仍在原目标范围内，整个目标保持 active。
