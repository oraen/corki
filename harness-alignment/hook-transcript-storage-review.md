# Hook 转录：存储链路与实施边界

## 基准和已确认差异

Codex参考ddf04ad26789d040f9ef6a96736f76602e35a6cc。本轮回到真实存储链路，
不把新增可空transcript_path字段当作转录功能已完成，也不实现官方远程history。

原生 core/src/session/mod.rs::hook_transcript_path 先调用 live_thread 的
local_rollout_path；没有本地路径时直接None，不要求非本地存储写文件。有本地
路径则调用 ensure_rollout_materialized → live_thread.persist，失败记日志。
该函数目前即使materialize失败仍返回已有路径；路径字段本身不能证明文件可读。

rollout/src/recorder.rs::record_canonical_items 把 AddItems 交给writer队列；
rollout_writer → add_items/flush_if_materialized。在未物化时保留pending，
物化后每次AddItems继续写入，不是每个Hook只导出一张不再更新的快照。
Persist/Flush/Shutdown有ack，write_pending_with_recovery遇到失败重开并重试，
只移除实际写出的pending项。JsonlWriter输出每行timestamp、可选ordinal和扁平
RolloutItem，再写换行、flush。该物化和持续写入能力属于本地Harness。

原生session/tests.rs两个明确边界：
- hook_transcript_path_does_not_persist_non_local_thread_store：返回None且不persist。
- hook_transcript_path_materializes_lazy_local_thread：先无文件，调用后能读到SessionMeta。
这些是源码证据，本轮没有执行Rust测试。

core/src/hook_runtime.rs::run_turn_stop_hooks：根线程提供自己的路径；ThreadSpawn
提供父thread_store.read_thread返回的rollout_path，并将子线程物化路径放入
agent_transcript_path。父查找失败记日志并保留None，不能伪造为子线程路径。

实施前Corki StopHooks.run的根/子字段仍为None。SQLiteSessionRepository只有数据库path；
VolatileSessionRepository.path会报错且不应有文件副作用。状态：**本地转录缺失**，
而非所有存储模式均缺失。影响：依赖读取完整上下文的已授权Hook无法工作；异步
Hook尤其不能只拿到一次性旧快照。

## Corki应接入的位置

canonical记录在conversation_items，以(thread_id, sequence)排序。
_append_items_in_connection统一处理用户/上下文/工具结果/模型提交等追加，并用
item_to_payload和item_kind保留内部类型及稳定id，重复相同id不重复追加。
append_partial_item也调用该入口，已完成的流item并非只存在独立临时表；不能只
导出model_steps而遗漏它们。load_items返回完整canonical历史，不是压缩后的窗口。

关键持久调用均通过sqlite.py::_joined_write拥有worker直到完成；取消时仍join，
避免仓库关闭后迟到写。转录发布必须使用同样的所有权，不能启动无主to_thread。
只在StopHooks里调用load_items然后写文件不足以对齐持续更新；只改append_items
也会漏掉事务内的工具完成、模型完成和partial item提交。

## 下一实现必须覆盖的契约

1. 为SQLite本地存储提供按需物化的可读JSONL与明确版本/身份；不能把SQLite文件
   当文本转录，也不能假冒包含官方字段的原生Rollout。保留Corki自身canonical内容。
2. 物化后从所有canonical提交入口持续发布，且只发布已提交内容。数据库仍是恢复
   权威；转录写失败不能把已提交业务写伪装为未执行从而触发副作用重放。
3. 跨任务/Runtime重开保持稳定身份与顺序，避免重复行、半行、较旧快照覆盖较新
   数据；重试需按实际已写内容或事务性发布边界去重，不能只更新内存水位。
4. 路径不能直接拼接不可信thread_id；目录/文件权限、符号链接/路径替换和部分写
   失败都需处理。不得扫描或导出其他线程、账号密钥或配置环境到转录。
5. ephemeral仓库不物化；父线程不存在时只给None，父子都存在时内容不可混淆。
6. Hook v1/v2批次已持久化空路径，不能在恢复时改写其不可变request。新路径契约
   需版本化；冷恢复旧未知/完成批次继续使用旧身份，新批次才启用新字段值。
7. 后续压缩是模型窗口替换，不删除原始canonical转录；输出文件不是新的模型
   注入来源，更不是启用namespace或专用压缩协议的理由。

## 验收要求和当前状态

真实Runtime命令必须实际打开输入中的路径读取用户、模型和工具历史；同时验证
异步命令在后续提交后能读到更新。加入根/父子隔离、无父/ephemeral、压缩后原文
保留、冷重开、重复追加、写失败/取消及路径攻击测试。

## 已接入实现与行为证据（2026-09-12）

活跃线程优化边界：考虑按inode/长度/mtime/ctime缓存已验证前缀，但这不是完整内容
身份，文件系统时间精度和并发外部写入可能让旧前缀失效。当前私有目录仍可能被同uid
Hook访问，不能直接以stat相等宣称内容未变。尚不实施这类缓存；先补跨repository并发
追加与发布失败恢复的实际验证，再决定文件所有权/增量校验方案。不把优化计划当成果。
本轮新增四repository实例并发32项，验证文件中sequence/ID顺序与canonical一致、
同inode打开reader继续可读、四实例重复提交无新行、pending清空；另用两个独立
Python子进程各追加8项，验证跨进程锁实际保持同一文件的有序完整记录。不是以同一
asyncio任务冒充多进程测试。转录文件13项通过（29fa36，0.52秒）；全项目ruff、
1175文件格式、compileall、77依赖及diff检查通过。没有新增生产缓存或改变安全边界，
也没有实现/宣称完成活跃长线程性能优化。

后续发布成本修复方案：原生rollout_writer把AddItems加入该线程pending_items，仅在
成功写入后drain已写前缀。Corki目前_after_durable_write却扫描全部登记线程，连
无canonical变更的账本操作也反复读文件。需持久化待发布线程集合，由canonical INSERT
同事务标记；成功fsync及路径校验后在持锁事务内清除。失败/进程退出保留标记，
下次提交可重试；升级旧库一次性将已登记线程加入集合。不以仅内存dirty缓存替代
恢复依据。本批先消除无关线程扫描，活跃线程前缀校验成本仍单独待优化。

现已实施transcript_pending和canonical INSERT trigger，事务回滚同时撤销待发布
标记；_after_durable_write只取待发布线程。flush在同一BEGIN IMMEDIATE锁下校验
前缀、追加、fsync及路径身份后删除标记，失败则数据库回滚保留标记。显式物化也先
登记pending；旧库通过transcript_pending_v1迁移一次性补标记，后续重开不重置。
只改第一线程却调用第二线程flush的反例修复前失败（fb079b）；基础9项修复后通过。
新增失败后冷重开/旧库升级重试、重复追加不刷新、事务回滚不留pending。
首次扩大回归200通过、1失败（6d2f55）：旧测试将整个schema_migrations条数固定为1，
与新增合法迁移冲突。改为查询legacy_messages_to_conversation_items_v1恰好一条，
保留实际历史重开不重复断言；新迁移本身另有升级/重复重开测试，不是放宽迁移正确性。
最终全部storage单元与四个Hook集成文件201通过（ac1cd0，19.99秒）。全项目ruff、
1175文件格式、compileall、77依赖及diff检查通过。不将减少无关扫描称为已经完成
活跃长线程的增量发布优化，也未声称本批完成全量压缩/记忆性能验收。

后续恢复审计：Codex的session::hook_transcript_path将本地路径身份与物化结果分开，
物化失败仍返回既有路径；hook_runtime::run_turn_stop_hooks只在组装请求时解析。
Corki v3恢复却重新物化并以当次结果重建payload，导致首次失败/恢复成功或反向变化时，
已有批次被错误判定identity collision。新增真实冷恢复测试12个组合均暴露该缺陷：
根/子 × 首次/冷恢复物化失败 × unknown/completed/feedback。
修复约束：已登记批次的路径字段属于不可变请求，恢复从snapshot读取；其他身份字段
及整批账本校验仍保留，未知副作用仍不得重放。仅新批次根据当前存储解析路径。
这是本地恢复语义，不引入官方服务或专属协议。
修复前60项运行中12失败、48通过（95db68）；修复后四个Hook集成文件与storage单元
联合198通过（6ee225，20.48秒），包含全部12个反例及v1/v2兼容、未知拒绝、完成复用。
原request_json逐字保持，副作用次数与原用户历史断言未放宽。全项目ruff、格式、
compileall、依赖检查和diff检查通过（441c35）；不将该集合当作全量A–F验证。

SQLite的transcript_threads登记需要持续发布的线程；materialize_transcript按需创建
私有JSONL，_joined_write在业务提交后发布并拥有worker直至结束。Volatile明确不落盘。
storage/transcripts.py按canonical sequence写入，保留payload_json原始数值拼写；线程名
散列成文件名，目录0700、文件0600，拒绝符号链接、硬链接、非普通文件和路径替换。
验证已有内容是canonical前缀后只追加缺失后缀，包含半行恢复；不截断外来内容。
同一inode持续追加，因此已打开文件的Hook也能观察后续Turn，而非只得到一次性快照。

StopHooks新批次使用v3，提供各自父/子路径；父不存在为None。v1/v2批次仍保持原空路径
与请求身份，不因新增文件改变已claim请求。文件是Corki本地格式，不伪装原生Rollout。

验证结果：
- input_contract、stop_hook_recovery、async_stop_hooks、sync_hook_concurrency与全部
  storage单元测试联合182通过（b5e59a，16.74秒）。实际命令打开路径读取内容；含根/子、
  父缺失/存在、同步/异步和两种审批策略，旧v1/v2冷恢复保留不可变身份。
- 后续新增存储故障及真实异步持有FD跨Turn/手动压缩用例；async_stop_hooks与
  test_transcripts联合23通过（a2e68c，3.37秒）。配置移除后旧命令仍读取后续提交；
  压缩不删除转录原文。两次取消须等待已提交写的发布结束；fsync失败不回滚业务历史，
  再次物化不产生重复行。
- ruff check、format check（1175文件）、compileall、uv pip check（77依赖）、
  git diff --check通过（394dac）。本轮未执行Codex Rust测试。
- 最终扩大到全部storage单元、上述四个Hook集成文件及全部名称含compaction的
  集成测试：505通过（3e61da，72.78秒）。与前两组重叠，不累加成唯一测试数，
  也不是整个项目全量回归。参考Codex commit再次核对未变且工作区干净（8bc809）。

仍为部分一致，不宣称完整转录验收结束：现仅扫描待发布线程，但刷新该线程时仍校验
完整前缀并持有SQLite写锁，大历史吞吐尚需改进及验证；当前安全文件实现限POSIX。
Corki新批次物化失败给None，
与原生已有路径仍返回Some不同；已登记v3批次恢复则保留原路径身份（见上方后续审计）。
父线程在同一仓库时会被按需物化，而原生父路径是查询已有记录。工具/partial提交的
专门转录故障组合、更多路径替换竞态仍待验证。完整A–F目标保持不变。
