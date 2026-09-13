# A6：事件队列、慢消费者与关闭所有权

## 默认流控修改后的输入与真实终端回归

默认输出队列改为无界后，重新运行 pending_input_sampling_boundary、
realtime_steering、cli_pending_history、turn_input_boundary 和 CLI pending_input
五文件：40 passed（a4c9b5，5.67秒），覆盖运行中输入的采样边界、持久化身份、
取消交还及冷启动历史回放。本轮没有进一步修改输入策略。

完整 stopped_queue_pty 文件：32 passed（9a7d9b，71.38秒）。真实40/100列终端
覆盖两种停止入口、重复Enter steer与Tab排队、Shift+Tab和default/plan模式；
停止后草稿按序恢复，用户重新提交前不发下一次模型请求。
这是暂停中的模型场景，不等同于高速输出恰逢提交的所有竞争窗口均已验证。

ruff check、format（1183文件）、compileall及依赖检查（77包）通过。
两组测试均已退出，无活动测试；本轮仅补验收记录，不将局部回归作为A–F全量完成证明。

## 默认流控差异已修复

重读参考session/mod.rs的通道创建，确认无界是输出事件默认路径而非实验能力。
新建test_event_delivery_policy真实Runtime反例覆盖SDK默认值与CorkiPaths生成配置：
只读取TurnStarted，暂停消费1024个正文delta，要求owned Turn独立完成并提交终态，
关闭Runtime后再读出全部有序delta及唯一TurnCompleted。修复前两例均等待超时
（04d561，6.68秒），清理仍完成，没有留下运行任务。

settings默认event_queue_size改为0，配置模板同步；0代表asyncio.Queue已有的无界
语义，正数继续启用宿主选择的背压，负值/布尔/小数仍拒绝。没有改现有配置文件，
其中显式256仍保持有界；没有改变输入队列、工具并行上限或终态/取消所有权。
README明确无界积压的内存成本，宿主应持续消费或显式关闭。这里只对齐事件通道，
不是承诺全Runtime永远不因任何输出资源而等待。

修复后事件策略、关闭、混合恢复、Turn准入、手动压缩、配置及模板七文件
133 passed（0597b3，16.48秒）；静态ebcfc1通过（1183文件、77包）。
既有容量1/4/8满队列关闭测试保留。下文默认256差异描述为修复前状态。
补充CLI完整单位与task ownership/HTTP消费者交接/执行审批取消联合533 passed
（41cb37，14.64秒），无跳过；不将两组相加冒充全仓单次通过。当前无活动测试。

参考Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。
session/mod.rs在创建会话时使用有界submission通道、无界event通道（564–565）；
send_event_raw_with_persistence完成记录后发送到tx_event（2421）。不能把输入
队列的背压误认为输出事件的默认背压。

Corki config/settings.py默认event_queue_size=256；TurnRun以此创建asyncio.Queue，
_QueueEventSink.emit直接await put，消费者暂停且队列填满时会暂停生产者。
这是可观察的默认流控差异，不是语言实现细节，当前A6不能标为一致。
后续修复应将默认输出流控与参考对齐，并保留宿主显式选择有界背压的能力；
需要同步修改配置校验与默认行为测试，明确无界输出对长期不消费宿主的内存影响。
不以“有界更安全”为由把不同的默认行为直接判定完成。

关闭链另有已实现保护：TurnRun独立持有task与done，terminal不占数据队列槽；
Runtime事件生成器finally取消并join，aclose可以不依赖消费者继续读取来清理。
join使用shield并等待真实task结束，不能将取消请求当作关闭确认。已经选定的
终态写入与清理过程另由runtime_shutdown中持久化闸门测试覆盖。

本次把test_close_does_not_need_paused_consumer_to_make_progress扩展为队列容量
1/4/8 × started/full_queue，共6例。模型信号与容量关联，full_queue分支显式断言
真实队列已满，不能只靠固定sleep推断背压。关闭后无运行中模型/Turn任务、无running
数据库Turn、realtime已停；恢复读取唯一TurnCancelled后抛取消，不再访问关闭资源。
初验6 passed（27dc38，0.77秒）。这验证关闭保护，不解决上述默认队列差异。
runtime_shutdown、recovery_and_concurrency、http_consumer_handoff联合72 passed
（3ab2dd，15.54秒），静态2730f9通过（1182文件、77包），无活动测试进程。

本次未修改生产流控，未新增官方产品依赖，也未将A6/E7所有资源路径一并核销。
