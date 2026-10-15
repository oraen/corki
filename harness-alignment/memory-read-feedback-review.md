# D4：记忆读取与实际引用的反馈边界

参考Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc：
ext/memories/src/prompts.rs读取并裁剪memory_summary.md，作为developer读取指导；
专用read/search只通过本地backend读取、返回JSON与工具指标，不更新stage1使用次数。
core/src/stream_events_utils.rs::record_completed_response_item_with_finalized_facts
仅从完成的assistant引用提取thread ids，交给state/runtime/memories.rs的
record_stage1_output_usage更新usage_count/last_usage，数据库错误不阻断用户Turn。
普通文件读取指导也不等于远程history服务。本任务不复制官方指标上报。

当前Corki summary→普通memories::search/read→Observation和图层引用已接入。
但LocalMemoryBackend.read/search额外调用_record_usage，正则扫描输出中的任意UUID，
再增加stage1使用次数；Runtime构造时实际传入memory_repository，因此不是死代码。
同一来源仅被检索/读取就增加次数，再被答案引用还会再加。候选内容或普通UUID
不应等同实际采用；这一差异会影响后续使用排序和保留时间，优先级D4/D3。

修复方案：删除backend的内容扫描计数，只保留graph对完成消息引用的反馈。
保留LocalMemoryBackend旧构造参数的兼容性，但不让读取产生反馈数据库写入。
验收：真实Runtime依次搜索→读取→有/无引用回答，批量和逐item完成都应在
检索阶段usage_count为0，仅完成引用消息增加1；冷重开不能重复记账。
摘要注入/路径/字段/错误策略和真正引用失败隔离沿用已有专项，不改写旧计数历史。

## 实施与验收

已删除backend的UUID扫描与两处读取计数调用，构造参数repository仅作旧SDK兼容，
不保存或写入它。图层实际完成引用、流式item去重及已提交Step冷恢复逻辑未更改。
这删除的是错误计数代码，不删除用户记忆、SQLite记录或旧使用次数；旧次数没有
独立来源明细，不能凭猜测反向扣除，历史排序偏差仅对新反馈停止扩大。

test_memory_retrieval_contract新增批量/流式×有/无引用四例，使用真实stage1来源
与SQLite计数，第一轮4失败/旧例1通过（300409，1.47秒），搜索刚完成时计数已为1。
修复后backend/text、检索、read policy、HTTP引用冷恢复、长期记忆、普通/Code Mode
工具协议与错误边界联合240通过（006271，33.33秒，18581退出0）。新链每个读取
阶段直接断言计数0，引用答案并冷重开后为1，无引用为0，归档前缀未改变。
既有实际双适配器引用测试仍验证UI隐藏、结构化持久化、原始body回放，以及每个
独立完成消息计数一次；旧Lite标志只能走普通Responses，不开启专属传输。
ruff、1184文件格式、compileall、77依赖及diff通过（af6b30）。参考commit及干净
状态再核实（780411）。没有联网请求真实模型，也不证明模型引用的事实正确性。

D4按当前普通读取路径已核验：摘要有界注入、可选专用工具的搜索/细读、完成消息
引用及使用反馈均接真实Runtime。memories_use/功能关闭/缺summary/shell-only门控
和普通HTTP字段亦在本次联合中覆盖。外部污染与显式遗忘归D5，来源失效与全部
并发恢复窗口归D3，不能从此核销完整Memory生命周期。
