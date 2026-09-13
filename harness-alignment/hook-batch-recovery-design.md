# A7/A8/E5：Hook 配置变化与整批冷恢复

状态：版本化批次持久化与授权分离已接入 Runtime；主要冷恢复矩阵已验证，完整边界仍未关闭。
参考 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。

## 不同命令换序的直接冷恢复证据

损坏持久数据补证：在真实 warm Runtime 关闭后修改独立 SQLite 测试库，
Stop 批次 version=999、第一条已完成 request_json={}、execution_key 增加
unexpected 后缀，分别带/不带晚到输入。真实 cold.resume_pending 必须失败，
保持模型一次、命令仅原先 once、第二条不执行、用户输入不丢、再次 resume 为空。
使用真实仓库 JSON 解码及 Stop 全批预检，不以 mock load 返回替代持久恢复。
基础批次不可变/归属/旧记录拒绝/取消 join 已有 unit/storage/test_hook_batch 覆盖。

首轮 25544 退出 1：75 passed/2 failed（01d6ef）；version 注入最初仅按 turn_id
选首行，误改同 Turn 的异步 prompt plan，实际报 Invalid asynchronous prompt
hook plan。已限定 batch_key LIKE 'stop:%'，没有放宽预期错误。正确目标后
两文件 **77 passed / 5.30s**，22530 实际退出 0（f0836a）。本批未改生产。
这些证据关闭本页原列换序与三类损坏身份的直接场景，不宣称任意数据库篡改
都可安全恢复，也不增加历史 CLI/PTY 门槛；C8 其它窗口仍需具体核定。

已补此前明确列出的换序场景：原批次两条实际 Python 命令分别写 once/second，
第一条在批次已存但无 claim、claim 后结果未知、结果已存三种窗口中断；
分别带/不带晚到用户输入。冷配置交换两条命令及匹配的批准指纹，仍使用原
位置 key。恢复必须在新副作用之前报 identity collision，而不能借换序替换
原调用身份。命令日志仅保留原先执行的 once，second 不执行，模型采样仍一次，
用户输入保留原身份，第二次 resume 无新任务。原 60 场景未删，新增 6 场景。

源码复核：Codex hook_runtime.rs:446 在 preview/run_stop 使用同一个 hooks；
Corki StopHooks.run 先加载冻结批次、验证整批 current command/已存 request/
未知结果，再执行任何命令。原生证据支持活跃调用配置稳定，不冒充原生提供了
Corki SQLite 跨进程事务保证。未修改生产路径。

完整 test_stop_pending_recovery.py：4 workers/load/禁重启，66 passed / 5.25s
（770d88），句柄 35148 随后实际退出 0（58195d）；ruff check/format 通过。
独立 tmp_path 与离线命令，当前 sandbox compiler 显式设置。
下方“换序仍待补齐”现为历史状态；损坏批次/记录必须先检查已有覆盖再判断缺口，
CLI/PTY 对齐按用户要求暂停，不能把历史 PTY 计划继续作为当前完成门槛。

## 当前实施与验证

SQLite/Volatile 新增 hook_batches，按 batch_key/thread/turn 绑定 version=1 的
完整有序命令清单、payload 与原 source_input_id；第一条副作用前先持久化。
只捕获命令既有的限定环境，不持久化完整进程环境/模型凭据。旧 execution key
与 request 格式不变；批次表和查询范围索引采用 IF NOT EXISTS 扩展，不删旧数据。
SessionRepositoryProtocol 新增 load_hook_batch/save_hook_batch；自定义仓库需要
实现同样契约。批次与执行记录在同一读取事务中读取，写入不可变且幂等、所有者
不匹配拒绝；查询和写入均 join 连接所属 worker 后才传播取消。

StopHooks 在 discover 空列表返回之前加载原批次。恢复冻结的声明顺序、原 payload
与反馈源输入 ID，不用晚到输入重建；预检整个批次的当前命令身份、请求记录与
unknown 状态后才开始任何新副作用。已完成结果即使声明被移除/撤销仍按原身份
复用；未执行命令必须在本次调用捕获的当前批准清单中且身份相同，否则失败。
新增命令不加入旧批次；改命令/路径即使重新批准也不能替换旧执行身份。

旧版本若只有单条 hook_executions 而没有批次快照，无法证明原声明清单完整，
现在给出 Legacy hook execution has no batch snapshot 错误，不猜测恢复或重跑。
这是明确的旧未完成 Hook 恢复限制，不是旧数据库整体不可用；普通新 Turn 和
没有旧 Hook claim 的恢复照常。没有提供自动补写历史批次的迁移。

证据：

- test_stop_pending_recovery：1/2 handlers × 批次提交后/结果未知/结果完成 ×
  有/无晚到输入 × 不变/删除/撤销/新增/改命令，共 60 场景。删除/撤销只有缓存
  结果可以复用，不能执行未运行兄弟；新增不运行，改命令在新副作用前失败。
- 核心/存储/Stop 全来源/冷恢复/输入边界/预算联合 273 passed（6d930e，30.68 秒），
  当时含前三种配置变化；追加新增/改命令 24 passed（ba9e3f，4.35 秒）。
- CLI/双入口 Hook PTY 446 passed（e4d09a，52.44 秒）。这是普通及无 checkpoint
  恢复 PTY，不冒充全部中断窗口与真实终端的交叉组合。
- 批次存储专项 5 passed：两 backend、重复写/身份/读取无新 claim、冷重开、
  legacy 拒绝、写入提交后挂起并重复取消仍等待 owned worker（d55a93）。
- ruff/format/compileall/uv pip check/diff check 通过（46ddf6）。
- 最终批次存储 + 全部 60 个恢复场景联合 65 passed（d01bed，10.39 秒），
  包含新增范围索引后的复验；参考源码工作树仍干净。本轮所有测试句柄已终态。

新增提交前证据（2026-09-12）：test_stop_batch_admission 使用实际 Runtime 与批准
命令，在 SQLite/Volatile 两 backend 注入真实 BEFORE INSERT ABORT，以及 writer
调用实际事务前挂起并请求取消。前者 TurnFailed 且批次/claim/命令副作用均无；
后者事件流等待 owned writer 收尾，批次写入完成但没有 claim/HookStarted/副作用，
随后 TurnCancelled 并传播 CancelledError。cancel_active 本身只请求取消，不负责
join，不能用它的返回时机判断资源已释放。移除注入后新 Turn 正常执行同一批准
命令作为正对照。新增 4 项与存储/冷恢复联合 69 passed（bb94d9，11.19 秒）。
这是故障边界补证，没有为通过测试修改生产路径。

跨 A/B/C/D/E/F 组合复验 215 passed（88db1a，35.14 秒）：deferred search、
model search compaction、compaction wire、memory pipeline、search error recovery、
recovery/concurrency。首次运行 9 个 CLI 恢复案例失败于旧 fixture 假定第一次
read_message 已处于 idle；活动 composer 已接入后应在 busy 时等待/被收拢，idle
再核验恢复。仅修正 fixture 时序，原单次采样/工具/历史/渲染断言全部保留。

仍需补齐：不同命令换序的直接场景、损坏批次与记录
的故障注入、相关 checkpoint×PTY 组合；async/non-root 与全 A–F 也未由此完成。
下文保留实施前分析，不将计划项当已执行验收。

## 调用链与差异

原生 core/src/hook_runtime.rs::run_turn_stop_hooks 在 preview/执行前捕获同一个
hooks 对象；session/mod.rs::refresh_hooks 用 config Arc 身份检查阻止旧刷新
覆盖新配置。它证明一次活跃调用捕获稳定配置，不证明 Corki SQLite checkpoint
在进程关闭后的恢复契约。官方 executor sources 不属于本项目范围。

Corki StopHooks.run 捕获 session 内存快照，但每条命令开始时才写
hook_executions。表只含 execution_key/thread/turn/request/result，没有整批声明
列表、顺序或已完成批次。冷 Runtime 会重新 discover 当前批准的配置。空配置
在读账本前返回 ALLOW；撤销授权的命令从当前 commands 中消失，也不再读取旧账本。
上轮 has_hook_executions 仅修复 pending 输入绕过“仍可发现的”已有执行，不能
跨越空配置或被过滤掉的来源，不能被宣称为完整恢复修复。

## 本轮真实探针

复用 test_stop_pending_recovery 的实际命令、同步 checkpoint、完成提交前/后
挂起以及关闭 warm Runtime→重建 cold Runtime 流程。仅在 cold acreate 前替换
配置：removed 为 LocalConfigState()；revoked 保留声明并写 enabled=false。
晚到的用户输入保持已持久化。没有模拟命令结果，没有重新批准任何命令。

输出 46339a：

| 冷配置 | 旧执行结果 | 观测 |
|---|---|---|
| 删除 | unknown | 原契约违反，终态 TurnCompleted |
| 删除 | completed Stop | 原契约违反，终态 TurnCompleted |
| 撤销 | unknown | 原契约违反，终态 TurnCompleted |
| 撤销 | completed Stop | 原契约违反，终态 TurnCompleted |

completed 两例违反的是既有测试的单次模型采样断言，不能仅凭终态类型区分正确性；
unknown 两例违反的是必须失败而不能伪装完成。探针捕获 AssertionError 作诊断，
不是四个通过测试，也不是已修复证据。临时库由探针清理，未更改用户配置。

## 必须保持的边界

1. unknown 不因移除配置、换 key 或撤销授权变成成功，不自动重跑命令。
2. 已完成结果可按原调用身份复用；复用结果不等于获得执行旧命令的权限。
3. 未开始的命令不得仅因旧快照曾批准就在新进程中越过当前撤销/变更的授权。
4. 新增或换序的命令不偷偷加入已经开始的旧批次；下一次新调用才捕获新集合。
5. 保存原批次顺序和 Stop 优先级；不能逐条恢复时提前发布后来应被 Stop 抑制的反馈。
6. 每次异步写入拥有者必须 join；批次与单条结果不能因取消处于无法解释的状态。

## 下一实现批次

- 增加按 thread/turn/step 绑定的批次记录，在第一条外部副作用之前保存完整声明
  清单、顺序及构建实际执行请求所需的冻结数据；不把 credentials 或任意全环境
  写入批次。沿用已明确允许的命令环境边界，不存官方账户数据。
- 冷恢复先加载批次，再处理当前配置与旧条目，而非先按新配置过滤历史。单条
  unknown 立即按现有安全语义失败；completed 复用；尚未执行部分重新验证当前
  用户授权及实际执行身份，不能恢复已撤销的执行许可。
- 比较完整清单，明确处理删除/新增/换序/改命令/改路径/撤销，避免只校验当前仍
  存在的第一条记录。不能仅增加“空 commands 就报错”代替批次恢复。
- 旧库没有完整批次时，不能凭现有零散行编造缺失的原声明清单；需明确兼容策略，
  不可证明安全恢复时给出可诊断错误，不重复副作用、不暗改旧 key。
- 验收矩阵：两条真实命令，第一条 claim 前/unknown/完成后/反馈后中断；冷配置
  不变/删除/撤销/换序/增加/改命令；断言结果、授权、命令次数、模型次数、输入
  身份与再次 resume 无重复。覆盖 SQLite/Volatile 与批次写取消/关闭。

此设计尚不是实现，A4/A7/A8/E5 不能据此关闭。完整 A–F 目标不收窄到 Hook；
本项因未知副作用被误判成功而优先于纯呈现缺口。
