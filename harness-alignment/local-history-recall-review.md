# C7 本地历史检索核验

参考Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，不把官方后端列为待实现。

## 原生路径与范围

ext/history-notes/src/extension.rs::update_config只有token_budget请求、OpenAI
provider和Codex backend鉴权同时满足时才安装HistoryNotesExtensionConfig。
ThreadLifecycleContributor绑定agent_name；ContextContributor请求alpha/notes/v2/
thread_hint并以4000字节限制注入当前窗口。tools.rs的HistoryNotesAction映射四个
history动作和五个notes动作；搜索契约为大小写敏感的字面子串。extension_tools.rs
适配ToolExecutor并将这些动作识别为内置控制工具。

这里的官方鉴权、alpha远程端点、encrypted字段及namespace模型协议均被用户明确
排除。需要保留的是上下文重置后按需找回历史和工作笔记，不是复制其远程产品。

## Corki实际调用链与边界

Runtime.__init__在history_notes_requested为真时创建LocalHistoryNotesBackend，
将HistoryNotesService.tools注册到实际registry。条件是token_budget_enabled和
use_history_notes_extension，与provider名称无关。工具为DIRECT_MODEL_ONLY，
普通工具账本执行；内部history::命名通过已有普通函数平铺传输，不启用原生namespace。

HistoryNotesTool.execute → LocalHistoryNotesBackend.call → repository.load_items
（捕获的thread_id）→ joined worker history_action。它读完整canonical历史，而不是
已压缩的模型窗口。project_history保留item/window/ordinal身份及原始调用文本，跳过
reasoning和仅快照状态；search按字面子串及role/window/tool过滤，read按opaque ID和
字符偏移继续，输出受预算约束。这与CLI分页显示、上下文窗口保留、长期记忆的提取/
合并/search/read分别是不同入口；不需要向量库，也不是自动把整个旧窗口重新注入。

backend使用捕获的线程身份，不信任工具参数的session_id/context；notes同样按线程
隔离。即使两个thread共享session_id也不能跨线程读取。仅当前/root视图可用，这是
本地兼容契约，不宣称复制原生跨agent远程读取。

## 当前证据

三个现有文件联合34通过（529d75，9.52秒）：test_local_history_notes、
test_history_notes_runtime、test_local_history_archive。逐项核验测试体后确认包含：

- 九动作真实Runtime执行及direct/code_mode_only入口；普通两种HTTP传输只访问
  fixture端点，openai名称不会启用官方history接口。
- 自动小窗口摘要移除大工具输出后，search在原始输出尾部命中，read用返回ID/偏移
  取回目标，再进入后续模型请求；不是只搜索本来仍可见的当前用户输入。
- 畸形调用原文在摘要与冷启动后召回，原始JSON拼写及tool call/result身份保持；
  Unicode小预算分页无损，图片与正文分开返回。
- notes冷恢复、写入故障与取消join、可作为Observation返回的可选存储错误。

另加强真实跨线程测试：第二线程即使已知第一线程的准确item_id/window_id并伪造
context，也必须读失败；共享/不共享session两组合。没有观察到需要修改生产逻辑的
新缺陷，不把补测写成先红后绿修复。
新增用例首次两项失败来自过宽的测试断言：将模型主动发出的搜索query也当成历史
泄漏。实际read已返回not found。修正为检查私有item身份没有进入请求且所有工具
结果均不含私有正文，保留已知ID直接读取失败和搜索无匹配断言。
最终包含增强断言的同组三文件34通过（4314c5，9.57秒）。随后仅应用格式化并复核
ruff/format/compileall/diff；依赖检查77包兼容。没有生产行为改动。

这核销C7“本地历史搜索完全未核验”的旧描述，不关闭所有Context项目。查询当前会
加载整线程历史，大历史性能未有专项验收；跨agent兼容能力与真实模型检索质量不由
这些ScriptedModel/mock HTTP测试证明。本轮没有运行原生Rust测试。
