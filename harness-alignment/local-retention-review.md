# C5/C6：普通压缩的保留与原始证据

## 新差异：相同文本的独立提交被当成恢复副本

原生session/input_queue.rs::get_pending_input按队列取走TurnInput，不按正文去重；
compact.rs::build_compacted_history_with_limit逐条保留用户消息，不合并相同文本。
Corki window.prepare用_same_user_input识别已持久化pending的恢复副本，但仅比较
turn_id、正文、附件和内容块。这样同Turn的新steer与旧输入文字相同，就会把旧
输入也加入protected_ids，从摘要请求及retained_users中排除。

真实Runtime探针test_compaction_input_identity通过在pending准入时临时降低自动
压缩阈值固定该窗口，不依赖sleep；同文repeat与异文different对照结果为1失败/
1通过（912bfc，0.62秒）。失败处摘要用户项为空，而已采样的原输入应按原id保留。
此测试固定触发窗口，不声称已证明默认阈值下所有输入时序。
修复方案：内容比较之前核对原始提交身份（retained_from_id或id），仍保留已有
turn/正文/附件一致检查。真正保留副本可关联原输入，不再用文本相同推断身份相同。
验收需同时覆盖独立同文提交、摘要/后续请求/归档，以及已有checkpoint恢复不重复。

已在_same_user_input内容比较前加入原始提交身份约束。上下文全部单位与新身份、
摘要输入、用户保留、pending采样及Turn输入边界联合428通过（1d83a0，13.85秒；
293913确认退出0），含原有已落库pending及retained副本恢复测试。
随后新身份测试补同Thread冷重开：两个独立提交仍按各自原id/保留id进入请求，
归档前缀未改、没有再次摘要；与steering/混合恢复/CLI pending联合34通过
（334f51，13.25秒）。静态ruff、1184文件format、compileall和77包依赖通过。
这是真实Runtime中强制该压缩窗口的先红后绿，不是默认时序发生概率的量化结论。
本修改晚于77351全量与63766 e2e基线，不能把旧基线当作含此修复的新全量。

## 参考链与当前实现

参考Codex基线ddf04ad26789d040f9ef6a96736f76602e35a6cc，上一轮核实工作树干净。
compact.rs向普通摘要请求提交history和压缩指令；窗口错误才调用
history.remove_first_item并重置重试次数。成功后collect_annotated_user_messages
提取用户文本，build_compacted_history_with_limit按最近用户优先的20k文本预算
构建用户消息和摘要，随后按入口重注入当前上下文。reasoning、正文和工具对是
摘要输入，不作为独立旧项放进这个替换窗口。这里不参考专用远程压缩路径。

Corki ContextWindowManager._summarize提交接受的历史，_drop_oldest_pair只改变
摘要请求视图并成对移除工具项。prepare/compact安装CompactionItem及保留副本；
active_history解释替换窗口，不删除SQLite原文。local_retention保留用户文本，
用retained_from_id关联原输入并去除附件；自动压缩另为当前输入及完整请求预留
空间。这个额外准入约束不能描述为原生字节预算算法的逐行复刻。

## 真实Runtime补验

test_local_compaction_inputs的pre_turn、mid_turn、manual三例现在从模型实际返回
reasoning、进行中正文和工具调用的同一Step；检查摘要收到reasoning、正文、
匹配的call/result及已接受用户输入，尚未接受的下一输入不进入摘要。
成功后原始reasoning和工具对各保留一次，活动历史不再带旧reasoning或工具对。
关闭Runtime后，用新注册表重开同Thread，后续模型请求仍不带旧工具/思考项，
用户顺序保持，旧归档前缀完整且没有再次摘要。

最初冷重开夹具误复用已sealed的注册表，三个失败均在构造而非压缩边界
（e95e2a：3 failed/44 passed）；改成新注册表，不修改生产生命周期或放宽锁定。
这不是生产修复的先红后绿证据。本轮仅加强行为验收，没有生产改动。

context全部单位、local_compaction_inputs、local_user_retention、pending_input_sampling_boundary
联合400 passed（b3071b，10.19秒），无跳过。包含两普通HTTP适配器的18种
手动/轮前/轮中×ASCII/Unicode/超长用户保留及冷回放、两个小窗口连续压缩场景。
ruff、format（1183文件）、compileall及依赖检查（77包）通过；测试已退出。

限制：混合reasoning三例使用脚本ModelPort，HTTP矩阵验证的是用户文本与工具链，
不冒充实际供应商reasoning输出或真实模型摘要质量。摘要中的事实完整性仍由模型
决定；本批证明输入选择、窗口替换和归档保留，而不是任意事实无损压缩保证。
全部取消、提交故障和其他A–F验收仍按各自专项处理。
