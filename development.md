# Corki 总体设计与开发指引

> 当前正在按新的完整目标重新审计 Harness。下面的旧里程碑“已完成”不代表已逐项与 Codex
> 等价；最新源码证据、修复和未关闭项以 [harness-alignment/audit.md](harness-alignment/audit.md) 为准。

## 1. 项目目标

Corki 使用 Python 和 LangGraph 实现一个以本地代码仓库为工作对象的 coding agent。
产品行为尽量接近 Codex CLI，但只实现编码 Agent 的核心闭环，不追求复制 Codex 的全部
产品、平台和安全基础设施。

核心闭环是：

```text
用户输入
  -> 构造当前 Turn 的上下文
  -> 调用模型并流式接收输出
  -> 模型请求工具时执行工具
  -> 将工具结果写回上下文
  -> 再次调用模型
  -> 模型明确要求继续时重新准备上下文；有效完成且无待执行工具时结束 Turn
```

当前版本明确不实现：

- 命令审批、危险操作判断和完整 CLI 用户确认；MCP 已有独立宿主输入接口，见 README；
- OS sandbox、网络权限和文件访问策略；
- 多 Agent、后台 Agent 和代码审查子 Agent；
- Telegram、Discord、Web、桌面端等其他入口；
- Cron、任意文档向量知识库、语音会话和浏览器自动化；
- 多执行环境、SSH 和云端 sandbox。

这些是当前里程碑的实现边界，不是永久排除项。提示词组装、协议事件、工具注册和 runtime
依赖注入必须保留扩展接口，使审批、sandbox、多 Agent 和语音 realtime 后续能以
独立 contributor/adapter 接入，而不是改写 Agent 主循环。

命令超时、输出截断、子进程回收、异常隔离属于运行稳定性，不属于安全审批，核心工具
从第一版开始就必须具备这些能力。

## 2. 源码调研后的取舍

### 2.1 从 Codex 保留什么

Codex 最值得借鉴的是边界，而不是 Rust crate 的数量。

1. **协议与 UI 分离。** `CodexThread` 通过 submission/event 双向通道工作，核心代码不直接
   打印终端。Corki 同样使用类型化 runtime event，CLI 只负责显示。
2. **Thread、Turn、Step 分层。** Thread 是长期对话；Turn 是一次用户任务；逻辑 Step 是
   一次模型决策，可包含多个失败后的采样 attempt。step_count 与持久化 sample_count 分开。
3. **每个 Step 使用一致快照。** Codex 的 `StepContext` 保证模型看到的提示词、工具和配置
   与随后执行工具时一致。Corki 应在进入一次 model node 前冻结 `TurnContext`。
4. **模型、上下文和工具相互解耦。** 模型适配器只处理模型协议；上下文构造器只组装
   prompt；工具 registry 同时提供模型 schema 和可执行 handler。
5. **结构化工具结果。** Tool call/result 是状态的一部分，不能只作为终端文本输出。
6. **终端进程与 patch 是独立能力。** 长进程需要 session id 和后续 stdin；代码编辑需要
   可验证的结构化变更，二者不应塞入 graph node。
7. **会话存储有接口。** Graph 不直接写 SQLite，恢复、查询和 checkpoint 通过 storage port。
8. **大输出进入模型前必须截断。** 终端原始输出可以保留，但模型上下文必须使用有预算的
   表示。

不复制 Codex 的地方：

- 不拆成大量细粒度 Python 包；仅在职责或依赖方向不同时拆包。
- 不复制 feature gate、兼容层、远程环境和历史协议。
- 不在第一个版本实现 JSONL 与 SQLite 双写。
- 不复制审批、sandbox、Guardian、attestation 等当前范围外能力。

### 2.2 从 Hermes 保留什么

Hermes 在 Python 工程中有几个值得借鉴的设计：

1. provider transport 将消息转换、工具 schema 转换和响应标准化隔离；
2. coding context 在一个位置判断项目类型、Git 信息和项目指令；
3. 文件写入、语法检查、LSP 和验证可以作为编辑后的反馈扩展；
4. SQLite 很适合 Python Agent 的会话和检索需求；
5. prompt-toolkit 与 Rich 的组合适合增量构建终端 Agent UI。

需要避免的问题：

- `run_agent.py`、`conversation_loop.py` 和 `hermes_cli/main.py` 承担过多职责；
- provider 兼容、消息平台、记忆和工具恢复逻辑进入同一关键循环；
- 顶层包之间存在较多横向导入，修改一个领域容易影响其他领域；
- 大量能力默认进入同一 Agent，使状态组合和测试矩阵快速膨胀。

Corki 因此采用“小而明确的 orchestration 层 + 独立 adapter”，不建立万能 `agent.py`、
`utils.py` 或几千行的 conversation loop。

### 2.3 主要参考源码

本设计基于以下本机源码快照：

- Codex Agent loop：`/Users/corki/IdeaProjects/ad/codex/codex-rs/core/src/session/turn.rs`
- Codex step context：`/Users/corki/IdeaProjects/ad/codex/codex-rs/core/src/session/step_context.rs`
- Codex model prompt：`/Users/corki/IdeaProjects/ad/codex/codex-rs/core/src/client_common.rs`
- Codex tool router：`/Users/corki/IdeaProjects/ad/codex/codex-rs/core/src/tools/router.rs`
- Codex thread store：`/Users/corki/IdeaProjects/ad/codex/codex-rs/thread-store/src/store.rs`
- Codex project instructions：`/Users/corki/IdeaProjects/ad/codex/codex-rs/core/src/agents_md.rs`
- Hermes Agent loop：`/Users/corki/PycharmProjects/hermes-agent/agent/conversation_loop.py`
- Hermes provider transport：`/Users/corki/PycharmProjects/hermes-agent/agent/transports/base.py`
- Hermes coding context：`/Users/corki/PycharmProjects/hermes-agent/agent/coding_context.py`
- Hermes tool registry：`/Users/corki/PycharmProjects/hermes-agent/tools/registry.py`

### 2.4 当前实现状态（2026-09-06）

当前已经完成一条可运行的纵向 harness：

- LangGraph 显式节点循环：prepare -> model -> evaluate -> tools/continue/finalize/fail；
- OpenAI-compatible Chat Completions 与 Responses 流式 provider adapter；
- freeform 原始输入契约：统一普通函数 JSON input wrapper，不发送 custom call/output，
  搜索加载后的定义类型、真实工具调度及持久化回放；不等于 Code Mode 执行环境；
- 独立的 thinking/reasoning 流式事件、UI 展示、SQLite 持久化和工具回合原样回传；
- `exec_command`、`write_stdin`、`apply_patch`、`update_plan`、`view_image`；
- 类型化 assistant/tool/plan/turn runtime events，CLI 实时消费 token delta；
- provider-neutral `ConversationItem` 协议和逐 item SQLite 存储；
- context/world-state 变更与本轮 user item 按确定顺序原子写入，当前输入永不参与 pre-step compaction；
- 每次模型采样冻结 advertised / dispatch 两份工具计划；所有 provider 统一通过普通
  tool_search 加载已检索的 deferred 定义，不使用原生搜索输出或 namespace 协议；
- LangGraph SQLite checkpoint、`corki resume` 和崩溃后 Turn 恢复；
- 连续历史、保守 token 估算、模型生成的自动 compaction；
- capability profile、SSE 完整性校验、分类错误、退避重试和 usage；
- 工具并发声明、取消传播、幂等执行 ledger 和 PTY shell；
- 模型 Step、工具调用数、JSON 参数、计划状态和空响应 guardrail；
- 项目根、Git 分支/dirty 状态、层级 `AGENTS.md` 上下文。
- Codex 风格 Skills 分层发现、显式 `$skill` 注入、按需读取和 10 个系统技能；
- stdio/Streamable HTTP MCP 初始化、工具/资源/模板/prompt 发现、分页、注册和调用；
- Codex `.codex-plugin/plugin.json` 与 Corki TOML 插件、插件 skills/MCP/Python tools；
- CLI realtime 文本 steering：生成中追加要求、取消旧采样、持久化输入并重建上下文。
- checkpoint 初始化失败可清理后重试；registry 只在完整启动成功后 seal；
- MCP 启动采用暂存后提交，取消或失败不会遗留半注册工具；插件模块由各 runtime 独立持有；
- provider 在 `[DONE]`/`response.completed` 立即结束，畸形 SSE、usage、重复 call id 统一为协议错误；
- Git 探测和 shell 超时/取消都回收子进程，POSIX 超时升级会终止完整进程组；
- 图片和 opaque reasoning 进入 token 预算，Chat/Responses 均可回放 user/tool 图片附件。

Code Mode 现有 opt-in QuickJS-NG 子进程路径：真实 ES module、嵌套通常工具调度、cell/wait、
session store、取消和父进程退出回收；shell/MCP typed return 已接入并持久化，但其余工具契约、
V8 及部分生命周期边界仍未对齐。有序媒体/helper 已接入 provider、历史及 context budget；
图片集中解码/缩放、模型图片/detail 能力与实验 unified budget 已接入；resize notices、
媒体来源元数据和压缩音频时长探测仍待对齐；音频MIME/base64规范化、大小检查与能力过滤
已接入。审批/OS sandbox、多 Agent，以及 Codex 的语音/音频
realtime 尚未实现。
当前 realtime 是 coding harness 的双向文本 steering，不应表述为语音能力。

## 3. 总体架构

```text
                        +-----------------------+
                        |      corki.cli        |
                        | input / render / keys |
                        +-----------+-----------+
                                    |
                                    v
                        +-----------------------+
                        |   CorkiRuntime facade |
                        +-----------+-----------+
                                    |
                   RuntimeEvent <---+---> SessionService
                                    |
                                    v
               +------------------------------------------+
               |             LangGraph graph              |
               | prepare -> model -> route -> tools --+   |
               |              |                  |     |   |
               |              +---- final -------+<----+   |
               +----------+------------+-------------+-----+
                          |            |             |
                          v            v             v
                    ContextBuilder  ModelPort   ToolExecutor
                          |                          |
                          v                          v
                    project/AGENTS             builtin tools

               SessionService ---> StoragePort ---> SQLite
```

LangGraph 只负责状态转换和节点路由。它不负责：

- 解析 CLI 参数；
- 绘制终端；
- 直接读写 SQLite；
- 直接拼接 provider-specific 请求；
- 直接运行 subprocess；
- 返回未经定义的任意字典给 UI。

## 4. 目录结构

```text
prompts/                            # 纯 Markdown 资源，与 Python 源码分离
├── agent/base.md                   # Codex 风格基础 coding instructions
├── modes/default.md                # 普通执行模式
├── modes/plan.md                   # 只规划、不修改的模式
├── context/agents.md               # AGENTS.md 动态上下文模板
├── context/environment.md          # cwd/shell/date/timezone 模板
├── extensions/                     # Skills/MCP/plugins 模型可见目录
├── memory/                         # 跨 Thread 抽取、合并与渐进召回提示词
├── realtime/start.md               # live text steering 指令
└── tasks/compact.md                # 上下文压缩指令

skills/                             # 10 个随发行包安装的系统技能

examples/                           # 无 API key 的可执行 harness 验收场景
├── core_loop_demo.py               # plan/shell/patch/verify/跨 Turn memory
├── builtin_tools_demo.py           # 默认工具、长进程续接、图片 attachment
└── extensions_demo.py              # project skill/Codex plugin/真实 stdio MCP

src/corki/
├── __init__.py
├── __main__.py
│
├── cli/                         # 终端适配层，不能被核心层导入
│   ├── main.py                  # argparse 和依赖组装
│   ├── application.py           # 交互生命周期与事件消费
│   ├── commands.py              # 不需要模型的本地斜杠命令
│   └── terminal.py              # prompt-toolkit、Rich、键盘输入
│
├── config/                      # 配置和 ~/.corki 路径
│   ├── paths.py                 # 唯一的 Corki home 路径来源
│   └── settings.py              # 不可变配置模型与 TOML/env 加载
│
├── protocol/                    # 最低层共享类型，不依赖具体框架
│   ├── ids.py                   # ThreadId、TurnId、ToolCallId
│   ├── items.py                 # provider-neutral canonical item
│   ├── messages.py              # 仅供旧 SQLite message 迁移
│   ├── events.py                # 类型化 RuntimeEvent
│   └── tools.py                 # ToolSpec/Call/Result/Exposure
│
├── core/                        # Agent 用例与 LangGraph orchestration
│   ├── runtime.py               # 流式 facade、Turn 生命周期和依赖组装
│   ├── state.py                 # CorkiState
│   └── graph.py                 # 显式 node 与 conditional edge
│
├── models/                      # LLM 端口、标准化和 provider adapter
│   ├── base.py                  # ModelPort Protocol
│   ├── types.py                 # ModelRequest/ModelEvent
│   ├── capabilities.py          # provider 能力 profile
│   ├── openai_compatible.py     # 流式 Chat Completions adapter
│   └── responses.py             # 流式 Responses adapter
│
├── context/                     # 模型可见上下文
│   ├── builder.py               # 按固定顺序组装 prompt
│   ├── project.py               # Git/workspace 快照
│   ├── instructions.py          # 逐级加载 AGENTS.md
│   ├── history.py               # 连续历史与 tool pair 归一化
│   ├── tokens.py                # 保守 token 估算
│   ├── truncation.py            # 工具输出预算
│   └── window.py                # 自动 compaction
│
├── memory/                      # 同 Thread 历史与跨 Thread 长期记忆
│   ├── service.py               # 连续历史 facade
│   ├── models.py                # extraction/consolidation 值对象
│   ├── repository.py            # 记忆持久化端口
│   ├── sqlite.py                # lease、watermark、usage/recency 排序
│   ├── pipeline.py              # 两阶段后台生成流程
│   ├── artifacts.py             # 私有分层 Markdown 与原子发布
│   ├── context.py               # compact summary prompt contributor
│   ├── backend.py               # scoped list/read/search/add-note
│   └── tools.py                 # 可选 dedicated memory tools
│
├── planning/                    # 用户可见的任务计划，与 provider reasoning 分离
│   └── models.py                # PlanItem/PlanStatus 及约束
│
├── evaluation/                  # 非安全型 harness guardrail
│   └── guards.py                # step/tool 上限和响应完整性
│
├── skills/                      # 安装、发现、缓存、选择和 progressive disclosure
├── mcp/                         # MCP transport、生命周期和 ToolRegistry adapter
├── plugins/                     # manifest、可信本地入口点、skills/MCP 聚合
├── realtime/                    # 活跃 Turn 的有界双向文本控制通道
│
├── prompting/                   # 提示词资源访问，不决定上下文策略
│   ├── assembly.py              # 稳定 slot、feature contribution 与确定性组装
│   ├── models.py                # PromptTemplate 不可变值对象
│   ├── renderer.py              # 严格替换 #{variable}
│   └── store.py                 # 资源定位、解析与原文缓存
│
├── tools/                       # 工具定义、注册、路由和执行
│   ├── base.py                  # Tool Protocol 与 ToolContext
│   ├── registry.py              # schema 与 handler 的唯一注册表
│   ├── executor.py              # 校验、执行、异常标准化、截断
│   └── builtin/
│       ├── shell.py             # exec_command/write_stdin
│       ├── process.py           # 长进程 session 与回收
│       ├── patch.py             # 预检后提交的 apply_patch
│       ├── plan.py              # update_plan
│       └── image.py             # view_image
│
├── sessions/                    # Thread/Turn 生命周期，不关心 SQLite 细节
│   ├── models.py                # TurnRecord/TurnStatus
│   └── repository.py            # async SessionRepository Protocol
│
├── storage/                     # persistence adapter
│   └── sqlite.py                # 非阻塞调用的 SQLite repository
```

目录随真实行为增长。不要为了让结构看起来完整而预先创建没有行为的类和空接口。

测试目录镜像产品边界：

```text
tests/
├── unit/
│   ├── cli/
│   ├── config/
│   ├── core/
│   ├── models/
│   ├── context/
│   ├── tools/
│   ├── sessions/
│   └── storage/
├── integration/                 # graph + fake model + tools + temp SQLite
├── e2e/                         # 真实 corki 命令与伪终端
└── fixtures/                    # fake streams、样例仓库、tool output
```

## 5. 依赖方向

依赖必须保持单向：

```text
cli ---------------------------> core
cli ---------------------------> config
core ---> protocol/context/models/tools/sessions
context -----------------------> protocol/config
models ------------------------> protocol/config
tools -------------------------> protocol/config
sessions ----------------------> protocol/storage
storage -----------------------> protocol/config
protocol ----------------------> Python 标准库或轻量数据类型库
```

硬性规则：

1. `core`、`models`、`tools`、`sessions`、`storage` 不得导入 `cli`。
2. `protocol` 不得导入 LangGraph、prompt-toolkit、Rich 或 provider SDK。
3. provider adapter 不得修改 LangGraph state，只返回标准化 model event。
4. tool handler 不得打印 UI，只返回 `ToolResult` 或产生 runtime event。
5. storage adapter 不得包含 prompt、模型或终端逻辑。
6. 不创建跨领域的万能 `utils.py`；帮助函数放在拥有该概念的模块中。
7. 遇到循环导入时先修正边界，不能用函数内 import 掩盖设计问题。

## 6. 核心状态设计

### 6.1 Thread、Turn、Step

- **Thread**：长期对话和 checkpoint 的身份；包含多个 Turn。
- **Turn**：从一条用户请求开始，到最终回答、失败或取消结束。
- **Step**：Turn 内一次模型调用以及它看到的不可变上下文快照。

### 6.2 LangGraph state

当前 `CorkiState` 只包含状态事实，不包含 runtime service：

```python
class CorkiState(TypedDict):
    thread_id: ThreadId
    turn_id: TurnId
    request_items: tuple[ConversationItem, ...]
    request_tools: tuple[ToolSpec, ...]
    dispatch_tools: tuple[ToolSpec, ...]
    context_instructions: str
    pending_input_items: tuple[UserMessageItem, ...]
    last_model_items: tuple[ConversationItem, ...]
    plan: tuple[dict[str, str], ...]
    step_count: int
    tool_call_count: int
    status: str
```

不要放入 state：

- model client、SQLite connection、subprocess handle；
- Rich Console、PromptSession、callback；
- ToolRegistry、Settings 等运行时服务；
- 无法稳定序列化的异常对象。

服务通过 graph build 时的依赖注入或 LangGraph runtime context 取得。状态用于描述事实，不用
作 service locator。

`ConversationItem` 是 Corki 自有 canonical 协议，不直接使用 provider 或 LangChain 消息。
User、assistant、reasoning、tool call/result、context 和 compaction 分别建模；provider adapter
只在网络边界做格式转换。`messages.py` 仅保留给旧数据库迁移，不得进入新核心逻辑。

### 6.3 Runtime event

State 用于 graph 计算和恢复；event 用于外部观察，二者不能混用。第一版事件：

```text
TurnStarted
AssistantReasoningDelta
AssistantTextDelta
ModelRetryScheduled
TokenUsageUpdated
ContextCompacted
AssistantMessageCompleted
ToolCallStarted
ToolOutputDelta
ToolCallCompleted
TurnCompleted
TurnFailed
TurnCancelled
```

事件必须带 `thread_id`、`turn_id`，工具事件还要带 `tool_call_id`。CLI 根据事件渲染，核心
不能直接调用 `console.print()`。

## 7. LangGraph 设计

第一版图保持显式，不使用封装完整循环的高层 helper：

```text
START
  -> prepare_model_context
  -> call_model
  -> evaluate
       -> execute_tools -> prepare_model_context
       -> continue      -> prepare_model_context
       -> finalize_turn -> END
       -> fail_turn     -> END
```

节点职责：

- `prepare_model_context`：刷新 world state、选择连续历史、必要时 compaction；
- `call_model`：只调用 `ModelPort`，把标准化输出追加到 state；
- `execute_tools`：只通过 registry/executor 执行调用并追加 tool result；
- `finalize_turn`：确定最终消息和终态；
- routing 函数必须是无 I/O 的纯函数。

`ModelCompleted` 表示一次采样完成，不等于 Turn 完成。Responses 的 `end_turn=false`
经模型账本和 graph state 保留，即使无工具或无正文也继续；工具调用优先处理。
未指定继续标志且无工具的有效 completed 可以空正文结束，缺失/畸形协议终态仍失败。
继续执行仅在宿主显式配置时接受 `max_steps`／`max_tool_calls` 正整数限制；默认不设
次数上限，仍受上下文、取消和资源准入约束。已有配置中的数值不会被自动删除。
finalize 在检查本次请求未见的已持久化输入后，原子检查队列并关闭
接收；存在新输入时返回 prepare。关闭后的 steer 抛出专门错误，CLI 保留为下一 Turn 输入。
输入 dequeue 不等于已记录；稳定 item id + 写入后 ack 支持失败/取消收尾的幂等补写。
普通和 realtime 模式的 cancel_active 现在都取消 Runtime 持有的 TurnRun。
第113批补齐CLI的输入所有权：realtime输出consumer与read_message任务统一cancel/join；
重复取消不再次打断prompt清理，正常完成或renderer错误也不能遗留输入reader。事件渲染器
显式关闭支持aclose的Runtime迭代器，避免错误路径靠GC才释放Turn；普通async iterator
没有aclose时仍兼容。清理错误按类型记录，不泄漏prompt数据或替换原始错误/取消。
实际LangGraphRuntime验证取消、正常完成、renderer失败及重复取消/清理失败组合。
这只是MCP elicitation宿主输入通道的前置修复，不代表审批、提问、暂停预算或CLI表单已实现。
graph 的 finalize 只选择结果；TurnRun 的 producer 负责清理与持久化终态，consumer 只转发事件，
因此暂停消费或 bounded queue 满不会阻止 Runtime 取消收尾。共享 aclose task 等待 active run
后再关闭依赖；重复取消不会打断 terminal commit，取消某个 close 等待者不会取消实际清理。
初始化早期取消、无法协作取消的 handler 和更多存储失败窗口仍见对齐审计的未关闭项。

窗口参数现区分 raw / usable / auto-compaction：`ContextLimits` 默认 usable=raw×95%，
auto=min(显式阈值或 raw×90%, raw×90%)；默认阈值跟随 raw，已有显式较低配置不被覆盖。
本地压缩保持 base instructions，追加 user compaction 请求；超窗从最旧项及其工具配对开始
裁减摘要请求，原始历史不删。旧的递归分块摘要实现已移除。当前输入完整保护优先于摘要安装；
无法同时放入窗口时失败，而不截短当前输入。远端压缩与 provider usage 校准仍在审计范围。
本地压缩独立管理stream_max_retries预算与指数退避；generic ModelError即便标注不可重试
也按compact.rs重试，输入超窗删最旧项后预算归零，取消直接退出。manager请求设置
harness_managed_retries=true，避免adapter再次做采样重试，但HTTP请求层仍保留。只安装
最后一条非空assistant摘要，失败/取消不发布replacement；UI重试事件purpose=compaction。
主采样CONTEXT_WINDOW错误结束Turn，不误称为自动压缩重采样路径。TokenBudget本地会话
预算、remote路径及partial压缩响应的独立记录仍未关闭。

本地手动压缩通过Runtime.compact()/CLI /compact接入同一TurnRun、事件队列和LangGraph：
独立compact节点，不进入普通prepare/model/tools循环，不把命令或合成摘要prompt作为
真实用户消息入库；空历史也调用摘要模型。保留历史用户文本预算与摘要，DoNotInject使
下一普通Turn重建world-state基线，已发现工具定义按当前压缩历史重新计算。
TurnRecord.operation经SQLite旧表增列默认normal，compact任务在无checkpoint时仍能
恢复正确入口；marker与retained用户原子提交，同Turn已有marker时不重复采样。
提交前失败/取消保留旧历史，提交后取消不回滚既有marker。两类开始/完成事件与临时
warning通过CLI显示，不生成助手答案；失败明确TurnFailed，不复制Codex源码已标注
known-incorrect的错误后Ok现状。top-level compact_prompt覆盖本地自动/手动摘要prompt，
strip后空串回退默认；未实现实验compact_prompt文件来源。
运行中CLI /compact先取消旧工作再开启独立任务；API事件迭代器须持续消费或显式关闭。
Runtime准入锁仅序列化注册并等待前一TurnRun.done，不持有到旧事件iterator结束；done
在终态持久化和CodeMode/realtime清理后发布。等待不持lifecycle锁，可独立取消，不取消
前一worker；Runtime关闭可取消worker并使等待的新请求发现closed。compact获得准入后
再次取消实际current worker，覆盖排队期间另一请求启动的情况。旧observer晚关闭仅
cancel/join自己持有的run，以identity检查清理active指针，不能干扰新任务或释放其等待锁。
这关闭了旧iterator阻塞准入的已复现缺口，不代表所有主循环/错误/资源边界已全部对齐。

每个 node 应尽量小于 200 行；单个模块超过约 400 行时检查是否混合了协议转换、I/O 和状态
转换。行数不是机械限制，但超过后必须在 review 中解释原因。

## 8. 模型层

内部只认一种标准化协议：

```python
class ModelPort(Protocol):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
```

`ModelRequest` 包含：

- system instructions；
- canonical context/items；
- 当前工具 schemas；
- model name 和 generation settings。

provider adapter 负责：

- 转换消息和工具格式；
- 调用 SDK；
- 将正文与 reasoning 流转换为彼此独立的统一事件；
- 按 provider 协议原样回传工具回合需要的 reasoning，而不把它混入正文或计划；
- 将 provider 异常转换为 Corki 异常类型。
- 按 capability 将记忆抽取等结构化请求映射为严格 JSON Schema 或 JSON Object，并保留
  应用层 schema 二次校验。

provider capability profile 是发送私有字段的唯一依据。`thinking`、reasoning effort、流式
usage、并行工具和 reasoning replay 不得由 adapter 猜测。当前实现 Chat Completions 与
Responses；provider fallback 和跨 provider 自动路由仍不在范围内。

流式传输必须看到有效协议终止标记，否则该 attempt 失败，不能生成成功 ModelCompleted。
主循环设置 ModelRequest.harness_managed_retries=true，内置 adapter 不重复做流层重试；graph
先等待已启动工具收尾、保存完整项和 Observation，再按错误分类从更新历史构造下一请求。
这不是重放旧 payload；只有完整项可进入 durable history，未完成的 delta 不会被执行。
独立 adapter（含 memory）仍沿用有界、未输出前重试契约；local compaction由window manager
独立管理采样重试，不由主图的ModelFailure/sample_count计数，也不叠加adapter采样预算。
Responses 无法解码的 SSE envelope 按 Codex 路径跳过；最终无终态、终态字段不可解码和
response.incomplete 是可重试的失败，canonical 已完成项改写等语义冲突仍直接失败。
OUTPUT_LIMIT 与 CONTEXT_WINDOW 分离；incomplete 可重新采样，但不会触发删历史压缩。
Responses 的 failed 现由 response_errors.py 按 code 分类，quota/usage-not-included、明确
policy/invalid request、server_is_overloaded/slow_down 都不可重试；rate_limit_exceeded
可重试且从 message 解析 try again in N s/ms。不能靠 message 中的 context/limit 猜类别。
无效 Error 结构保留 generic retryable stream failure，不能误提升为有效的 typed fatal。
SSE 错误（含已检查的 completion 结构错误）先暂存到正常 EOF；之后完整 item 仍可发布，
有效 completed 可结束响应，I/O 错误则覆盖为 transport。取消保持控制流，不安装失败或
成功采样记录。结构化 policy/usage 附加诊断尚未据此对齐。
usage 的缺字段/默认值和完整 metadata 契约仍属 C02/E02 待审计，不能由上述终态校验外推。
一旦收到权威终止事件，应立即结束读取，不能依赖 HTTP peer 主动断开；所有 provider payload
在进入 canonical item 前必须完成结构、usage 非负整数、item/call id 唯一性和响应大小校验。

第十五批 HTTP 层在 models/http_stream.py 独立持有响应：成功取得 2xx 后才交给 SSE reader，
yield 后的读流错误不回到 HTTP 请求重试。5xx 和取得响应前的 TransportError 最多重试 4
次；429/其他状态不做请求层重试。失败响应在等待前关闭，取消/steer 可以中断 HTTP wait。
诊断 body 读取失败保留 status；先解析完整错误码再截短展示，不截断 JSON 后猜类型。
HTTP api_bridge 等价分类在 http_errors.py：503 overload 致命、普通 5xx 可流重试；429
是 retry_limit/usage_limit/usage_not_included 致命；400 一般是 invalid_request，不能靠
context/token 文字触发压缩。静态 key 的其余 401/403 等状态有界流重试，未新增 OAuth。
HTTP request_max_retries 默认4，stream_max_retries 默认5，两者 cap100；旧 max_retries
是 stream 的兼容别名，新 key 优先。Runtime composition 和两种 adapter 构造器均接入。
backoff 基础200ms、指数增长、0.9..1.1 jitter、毫秒精度，不再固定截到8s。HTTP Retry-After
不覆盖本地退避；SSE rate-limit 指定延迟保留。两层都启用时预算可能相乘，这是两种责任，
不等于在 adapter 和 graph 重复实现同一种流重试。HTTP attempt 不单独保存 ModelFailure；
只有 request 层耗尽并成为 sampling 错误后才落库，因此不声称 HTTP attempt 跨进程 exactly-once。

## 9. 工具层

一个工具同时拥有静态描述和运行 handler：

```text
ToolSpec    给模型看的名称、说明、JSON schema
ToolCall    模型请求执行的名称、call id、参数
ToolResult  执行状态、模型可见内容、可选元数据
```

当前内置工具：

1. `exec_command`：命令、cwd、timeout、输出预算和长进程 session；
2. `write_stdin`：向长进程写入或轮询，进程生命周期归 `ProcessManager`；
3. `apply_patch`：add/update/delete/move，预检、临时文件、原子 replace 和全量回滚；
4. `update_plan`：维护用户可见计划，并强制最多一个 `in_progress`；
5. `view_image`：有界读取并验证 PNG/JPEG/GIF/WebP，通过有序内容进入多模态消息；
   Code Mode 返回有效原图的 image_url/detail，最终模型图片由集中 preparation 处理。

工具按需检索的评分现按Codex锁定的bm25 2.3.2：k1=1.2、b=.75、文档频率IDF、
预计算文档tf权重、倒排候选、query重复词逐项f32累加。索引先完整构建再一次发布snapshot，
构建失败不会让新specs和旧索引混用；输入与返回的嵌套schema均隔离复制。显式空search_text
保持空；freeform默认搜索文本含grammar syntax而不含完整definition。同分按注册顺序稳定
排序，上游HashSet同分顺序不确定；tokenizer.py已接入锁定版本的normalization、stemming、
stopwords与u32 hash身份并有golden验证。Rust直接差分与big-endian运行未执行，不得由评分
测试通过推断所有查询的完整排名位级一致。

虽然暂不做安全审批，但必须做到：

- 使用参数数组或明确的 shell 语义，不能暗中改变执行方式；
- 每个命令有超时；
- 捕获 stdout/stderr 和 exit code；
- 输出有模型预算；
- 取消 Turn 时终止对应子进程；
- 可恢复工具异常转成有界 `ToolResult`；显式 `FatalToolError` 结束 Turn，不能吞成 Observation；
- 所有文件路径相对 Turn 的 cwd 解析；
- tool call id 写入 durable ledger；完成结果复用，崩溃时结果未知的调用绝不自动重放；
- `PARALLEL` 只用于显式声明可并行的工具，shell、patch 和 plan 默认串行；
- `tool_search` 查询 deferred 元数据，完整定义保存在工具结果及执行账本；兼容路径从活动历史
  重建 loaded schemas，原生路径保持 schemas 在搜索输出历史中；压缩后重新检索，禁止复用失效定义；
- `exec_command(tty=true)` 使用真实 PTY，取消和 runtime 关闭时回收进程组。
- handler 必须返回当前 call 对应的 `ToolResult`；身份不匹配或返回类型错误统一转成失败结果。

后续工具若声明 `PARALLEL`，必须用重叠执行与取消测试证明互不冲突。

执行边界包含返回值校验与规范化，不只包住 handler 的 await。content/display 必须是可编码的
字符串，is_error 是 bool，附件是合法字段类型的 ImageAttachment，plan 经过领域校验后复制。
非法结果只能把规范化错误写入 ledger/history，不能把坏字段留给下一次模型序列化或状态更新。
错误消息与正常输出同样受每工具/global 字符预算约束；异常的 __str__ 出错也不能逃逸该边界。
插件的不可恢复执行环境故障可抛 `corki.tools.FatalToolError`，Runtime 生成 error_kind=tool、
retryable=false 的 TurnFailed 并回收并行 sibling；CancelledError 不进入普通异常处理。
MCP 的 per-server wait_for 到期返回“执行结果可能未知”的 Observation，不自动重放。不存在
已对齐 Codex 的全工具统一超时或任意 Python handler 强制终止保证，完整 schema/媒体校验仍见审计。

## 10. 上下文层

`[tools].deferred_tool_world_state` 默认false，对齐Codex同名实验开关；search_mode=disabled
时不注入目录。Runtime组合DeferredToolsContextContributor，ContextBuilder只提供通用
StepContextContributor入口，接收本Step已捕获的ToolSpec tuple，不在await后重读registry。
prepare、steering、token-budget手动reset均传入对应冻结定义；搜索handler缓存包含来源目录
Include/Omit策略。目录是非默认deferred namespaces，不是MCP source标签，也不是完整schema。
world_state按完整版本化map比较，只对模型文本应用首行250字符/XML转义/4096字节限制；
支持增改、删除、最后删除提示和压缩后全量注入。未知旧metadata回退完整目录。
空map仅作为Corki静默比较记录，下次非空按Absent处理；Codex不持久化空section。
Markdown片段保留Corki资源的末尾换行，并计入字节预算；metadata不使用Codex RFC7386 wire。

`ContextBuilder` 使用固定顺序构造模型输入：

```text
基础 coding prompt
-> 用户配置指令
-> 项目级 AGENTS.md
-> 当前 environment/workspace 信息
-> conversation history
-> 当前用户消息
```

顺序是存储与模型请求共同遵守的不变量，而不只是 UI 展示约定。每个 model step 先形成候选
上下文；若预计超阈值，摘要请求使用此前已经接收的历史，不包含尚未接收的 pending input。
mid-turn 已接收的当前 user 仍要进入摘要请求，和其原文副本的保留是独立规则。
Turn 开始前压缩的模型可见顺序是
`保留的旧 user -> compaction -> 当前 context -> 当前 user input`；工具回合中的 mid-turn 压缩会把
当前 context 放在最近一条保留 user 之前，并把 compaction summary 放在最后，与 Codex 的注入语义
一致。存储仍保持 append-only，因此 checkpoint marker 会先物理落库，再通过 marker 中的 replacement
长度和 summary index 重建上述模型顺序。当前用户消息的保留副本不由摘要模型改写。
没有触发压缩时，context diff 与 pending user item 在同一次 repository append 中提交，保证崩溃
恢复仍保持顺序。

本地 CompactionItem 的摘要正文仍以原值持久化；HTTP 与历史读取视图共享
prompting/compaction.py，在投影时添加固定 Codex 交接前缀并使用 user 角色，不能把
模型生成的历史提升成 system/developer 指令。Responses 的 compaction.summary 分类
继续服从 provider metadata 与 content_item_kinds 门；估算包含完整前缀及分类成本。
旧 checkpoint 无字段迁移，ID、append-only 档案、summary index 和当前输入保护不变。
opaque remote compaction 与 Token Budget reset 不套用本地文本前缀。

第161批本地摘要输入：不再因为本地 token 估算过大而在请求前删除历史。按 compact.rs
先提交已接收的模型可见历史，仅在 provider 返回 ContextWindowExceeded 后删除最老完整
message/tool pair，并重置本地 retry 计数。不采集或回传官方 turn-state 路由令牌；通用重试/取消语义保留。
只剩摘要请求仍被 provider 拒绝时终止，不无限删减或重新采样；取消保留原始记录。
普通请求和压缩后窗口的预算检查没有移除；remote/Token Budget 路径不受此改动影响。
当前仍有 pre-turn 触发估算、保留 user 的预算/选择及其他 C 项差异，不代表完整压缩对齐。

文本估算对 ASCII/code 使用约 4 字符/token，对 Unicode 使用更保守的 UTF-8 权重；图片采用与
Codex 相同的 resized 固定预算，`detail=original` 对常见图片格式按 32px patch 估算并设置硬上限。
附件和 encrypted reasoning 不能因不是普通文本而逃逸窗口预算。

`media/images.py` 在工具结果持久化、历史读取、当前输入及最终模型请求边界统一处理图片。
Pillow 显式依赖，格式按字节识别，仅启用 PNG/JPEG/GIF/WebP；错误就地替换为稳定 omission。
high/auto：2048 最大边长与 2500 个 32px patch；original-capable 模型的 original：
6000/10000。默认不启用 original 能力和 unified budget，后者需要前者。
无须缩放的 PNG/JPEG/WebP 保留字节，GIF 转 PNG；缩放复制 RGB ICC 和 EXIF，不自动旋转。
缓存32项/64MiB（按 data URL 字符串大小计，比 Codex 原始编码字节预算更保守），解码限
6400万像素。CPU/文件线程在取消时 join，不遗留后台工作。旧持久化行不回写，当前模型不支持
图片时仅请求视图替换，历史估算仍保守计入图片。过去用户消息的压缩保留只复制文字，不再让
旧 content_items 绕过截断；当前输入的完整保护不变。Pillow 与 Rust 重编码不保证字节一致，
resize notice、来源 metadata、view_image 能力驱动的 schema 隐藏尚未全部对齐。

Runtime现在统一持有`media/preparation.py::MediaPreparation`，按image→audio顺序准备
新历史、旧历史投影、重试/压缩/最终请求和pending输入。音频准备只规范化MIME别名为
audio/wav、audio/mpeg、audio/mp4、audio/webm、audio/ogg，检查严格base64和50MiB解码
限额，不强制codec解码或转码，与Codex utils/audio一致。错误、不支持格式、超限分别
变稳定omission，保留内容位置与tool success；typed nested值不变。能力过滤在请求视图
执行，旧数据库行不回写；audio不支持时模型收到明确提示而非原始音频。
时长估算是独立逻辑：现有PCM/float WAV实际字节解析继续用于tiny clip与token估算，
压缩音频的Symphonia等价容器duration探测及32项缓存仍未完成，不能把规范化等同于时长支持。

项目指令从最近配置 marker（默认仅 `.git`；无 marker 时仅 cwd）到当前 cwd 查找
`AGENTS.override.md`/`AGENTS.md`/配置 fallback；先完成所有候选发现再按 root 到 cwd
分配总字节预算，无效 UTF-8 使用 replacement character。空白项目 override 遮蔽 default，
但不扣减预算。全局 home provider 独立加载 override/default，空 override 会回退，
读取失败警告后尝试 default；global 不占 project_doc_max_bytes，也不受 project trust 禁用。
全局指令在 root Runtime 创建/冷重开时加载；non-root 不自行重读home，可由宿主显式
传入父快照，Guardian 两种来源不继承。完整活父查找/root fork/多环境API仍未实现。

InstructionManager 是会话所有者：环境、权限及 host trust 不变时不因文件编辑刷新。
初始化失败或取消会在读取任务 join 后丢弃未发布快照，下一次创建尝试重新读取。
项目指令读取必须遵守显式执行权限；受限策略在固定 sandbox helper 内发现和读取，
失败在采样前拒绝；非受限项目读取异常保留 global 并发出警告。配置支持
`project_doc_max_bytes`、`project_doc_fallback_filenames`、`project_root_markers`；
第199批接入显式用户配置中的 `[projects]` active trust 选择，联动项目指令、已配置
执行的默认审批与隐式profile；第200批接入本地用户/项目配置层准入、来源目录与规则加载。
完整system/cloud/MDM/session层及动态环境/权限更新宿主链路仍未完成，
不能视为全部上下文对齐。详见 harness-alignment/audit.md 的当前验证与剩余边界。

工具输出先经过 truncation，再进入模型历史。完整终端输出是否持久化可以后续决定，但不能用
完整输出无上限地填充 prompt。

### 10.1 Codex 风格提示词分层

普通首轮 coding 请求不把所有 Markdown 拼成一个长 system prompt，而是保持与 Codex 相同的
职责分层：

```text
API instructions: prompts/agent/base.md
developer message: prompts/modes/default.md
user context:      prompts/context/agents.md（存在项目指令时）
user context:      prompts/context/environment.md
user message:      用户问题原文
tools:             独立 JSON Schema，不属于提示词模板
```

后续同一 Thread 只追加发生变化的上下文，但不从模型历史删除旧版本。AGENTS 文件编辑
本身不使当前 Runtime 快照失效；冷重开加载的快照与旧历史比较后，更新/删除才
追加 replacement/removal notice；其它当前整段 contributor 使用显式 keyed replacement
兼容语义，尚不等于所有 Codex section 的专属 diff。`ContextItem.content` 是已渲染消息，
可选 `snapshot_content` 保存未带 notice 的比较值（空串表示删除），只比较当前 compaction
窗口的 key、role、值。角色变化先以旧角色声明失效，再引入新角色，不能用 user 消息撤销
旧 developer 消息。新字段缺省不写入 JSON，旧行不回写；旧空 tombstone 在请求视图中
转换为失效说明。压缩重新注入当前完整值，不携带之前的 update notice。
可选snapshot_state是feature-owned的不可变比较标识，不是模型正文；技能目录用它区分
skills.listed/skills.hidden。带状态的空正文记录作为静默基线入库，但从active_history、
provider请求、legacy messages中排除且token成本为0；压缩保留新的静默基线，不重放通知。
Plan 模式以 `modes/plan.md` 替换 default；`tasks/compact.md` 只用于上下文压缩。

### 10.2 Prompt 扩展位

`PromptAssembler` 不识别具体业务能力，只接收各模块提供的 `PromptContribution`。Contribution
包含稳定 key、role、slot、slot 内顺序、模板名、变量以及是否需要独立消息。input_scoped
贡献不属于可替换world state：ContextSnapshot.input_items在原始Turn用户输入之后注入，
ContextItem.source_input_id关联稳定输入身份。缺省字段不写旧JSON，正常Step/steering不重新
运行InputContextContributor.input_contributions，但普通contributions仍收到最新用户输入。
prepare重试使用已记录的正文，即使源文件改变也不覆盖。输入contributor也可返回不可变
InputContextContributions（旧tuple返回仍兼容），warnings经ContextSnapshot到Runtime事件，
不加入prompt、业务历史或checkpoint。当前预留顺序为：

```text
SESSION -> REALTIME -> PROJECT_INSTRUCTIONS -> PERMISSIONS
        -> COLLABORATION_MODE -> ENVIRONMENT -> EXTENSIONS
        -> MULTI_AGENT -> TURN
```

未来功能的接入规则：

- approval policy 与 sandbox 归入 `PERMISSIONS`；
- realtime start/end/delegation 归入 `REALTIME`；
- plugins 与 Skills 归入 `EXTENSIONS`；
- orchestrator、agent role 和 handoff 归入 `MULTI_AGENT`；
- 每个功能在自己的模块中决定是否启用并产出 contribution；
- `ContextBuilder` 只收集、组装和转换消息，不增加 feature-specific `if/else`；
- 未实现功能只保留 slot 与命名约定，不创建空目录、空模板或占位类。

## 11. Session 与持久化

当前使用两个职责分离的 SQLite 文件；长期记忆的 job/output 表由独立 repository adapter
扩展在业务库中，以便和 thread 更新事务共享事实来源：

```text
~/.corki/
├── config.toml
├── history/input-history
├── logs/
├── memories/                   # 分层检索文件，不是业务事实主存储
└── sessions/
    ├── corki.db                 # thread/turn/item/tool + memory job/output
    ├── corki.db.thread-writer-locks/ # canonical Thread 的存活写入所有权
    └── checkpoints.db           # LangGraph node checkpoint
```

当前职责拆分：

- LangGraph `CorkiState`：管理单个 Turn 的确定性状态转换，不包含 I/O service；
- `SessionRepository`：异步保存 thread、turn 和 canonical item；
- `ConversationMemory`：返回 compaction 后连续历史，不做会跳过中间 Turn 的词汇抽取；
- `SQLiteMemoryRepository`：使用 owner token、lease、retry_at 和全局 watermark 保证多 CLI
  并发时抽取幂等、合并单例；按 usage count、last-used/generated recency 选择输入；
- `LongTermMemoryService`：后台执行每 Thread 的 Phase 1 抽取和全局 Phase 2 合并；失败只记录
  warning，不能拖垮交互 Turn；发布前模型失败保留旧文件，发布中的多文件 I/O/crash 原子性仍未完成；
- `LangGraphRuntime`：管理创建、完成、失败和取消，不写 SQL。

Runtime 在首次 Thread 初始化之前取得 OS-backed writer lock，跨 Turn 持有，全部关闭清理
结束后释放。同一 resolved 业务库路径与 Thread ID 的竞争者在输入/采样前收到
ThreadWriterConflict；不同 Thread 可以并行。锁文件名是 opaque Thread ID 的 SHA-256，
独立 coordination lock 串行化 acquire、stale cleanup 和 close/unlink，避免活跃 inode 被
误删。没有按 PID、TTL 或等待时长猜测死亡；进程退出由内核释放锁。
阻塞锁操作在线程执行，取消时 join 已启动 acquisition 并关闭返回的 guard。初始化失败可重试，
关闭错误在其余资源清理和释放 writer 后报告；取消 close waiter 不取消共享清理任务。
这是合作 Runtime 的本机写入所有权，不保护任意直接 SQL/管理元数据写入，也不涵盖 hard-link
别名、分布式锁或完整安全沙箱。macOS 实际跨进程竞争/退出恢复已测试；Windows 分支尚未执行。

单 Thread 归档由 sessions/archive.py 的宿主控制器驱动：Runtime.archive 先检查已有 canonical
history，再关闭当前 Runtime，最后调用 archive store。关闭等待上限10秒，超时/关闭错误先记录，
存储仍必须取得同一 writer 锁；不能据超时抢写。取消已接纳归档时 join 真实任务。独立
storage/thread_archive.py 使用 SQLite 事务更新 archived_at，不复制虚构的 JSONL 文件，不删除
history、checkpoint、tool ledger、模型配置、source 或 memory_mode。旧库迁移只补 NULL 列。
archive 保留 updated_at；unarchive 清 archived_at 并刷新 updated_at，因此重新开始 idle 窗口。
普通 Runtime/CLI resume 拒绝归档源，latest 排除归档；显式 include_archived=True 是宿主选择的
底层 core 路径，读取/继续执行不会自动取消归档。关闭后的 Runtime.unarchive 只恢复集合，继续
对话需要新 Runtime；自定义存储可注入 ThreadArchiveStore，并负责共享写入所有权。
Stage1 在 scan limit 前排除 archived_at 非 NULL 的源；Phase2 既有 derived inputs 仍按原来的
memory_mode/retention 规则筛选，不把归档误当遗忘。当前只覆盖单 Thread host API，尚无 spawned
subtree 关闭/部分成功语义、app-server 通知或 CLI archive 命令；不能因此宣布完整 A/D 对齐。

宿主可传 Runtime.create(ephemeral=True) 创建独立非持久化会话。VolatileSessionRepository
复用 canonical SQL 的身份校验、tool claim、model/partial/failure 事务，但使用独占 :memory:
连接，以 RLock 串行化完整工作线程事务，temp_store=MEMORY。graph saver 同样使用 :memory:
及内存临时表，不生成业务库/checkpoint/writer 文件。连接在 fallible 扩展装配后才分配，Runtime
构造失败同步释放，正常/取消关闭在 join 所有写入者后释放。默认持久化路径不变。
内部 consolidation worker 已选择此真实路径；其 artifact 工作副本可以存在，但不再以删除
临时数据库作为 ephemeral 实现。临时会话仍能多 Turn、按需检索/执行、压缩与同一进程内状态
恢复；无后台memory pass，无持久化memory-mode/archive控制。read-memory prompt/tools仍按
原feature/use开关可用，repository可为None，不关闭全部Memory功能。
该模式拥有私有状态，不接受外部session/archive/memory repository覆盖；新建时不会因传入
旧Thread ID就导入磁盘历史。完整durable-source fork/resume导入及CLI ephemeral参数仍未实现。
显式工具写文件、ad-hoc note、独立history/notes服务、provider留存不属于此处canonical会话
非持久化保证；不声称安全擦除、无任何文件写入或权限沙箱。

Phase 2 在工作期间 heartbeat 续租；workspace 写与最终 publish callback 在 SQLite matching-token
写事务内执行，不能先覆盖 artifact 再检查 owner。阻塞写已经启动时，取消需要 join 真正的 worker，
之后才允许释放 claim。最终提交只标记 thread_id 与 source_updated_at 同时匹配的输入快照。
模型完成后关闭流，心跳失败取消并回收工作；Phase 1 的失败落库异常只增加 warning，不覆盖取消或
遗留其他提取任务。事务锁只防止合作进程间接管，不是文件集合与 SQLite 的 crash 原子提交；
耗时文件写占用共享数据库写锁的延迟影响、独立 service close 的全部故障窗口仍需验收。

合并 baseline 现为 v2：采样前 input digest + 发布后 output digest；skip 必须同时确认两者
未变化且产物有效。旧 baseline 无输出证据，需正常重建一次；修改/删除 MEMORY、summary、skills
会重新合并，旧 skills 文本进入有界请求。采样中新增 note 不会被误标为已消费。指纹不保存旧记忆
正文。Codex 的 local workspace 同样不是跨文件/DB 的原子发布，不能把这一点误列为其已实现而
Corki 缺失的能力；当前已验证部分写失败重试和 baseline 已写但 DB 未提交的真实进程退出恢复。

模型 step 的 usage、metadata 和 items 在一个事务内提交，并以
`(thread_id, turn_id, step_index)` 幂等读取，消除业务提交与 graph checkpoint 之间的重复采样
窗口。工具执行先 claim ledger：完成后可复用；若进程死于外部副作用之后、结果提交之前，恢复
遇到未完成的 claim 时返回“结果未知”，不会擅自重复副作用。构造 repository 不再全库把 running
改成 interrupted，否则另一个仍存活 Thread 的工具会被误判；旧 interrupted 与未完成 running
都不能凭状态名推断安全重放。

复用前同时核对 thread、turn、tool name 和 arguments_sha256；解析后的 JSON 忽略空白/键顺序，
非法 JSON 按原始参数区分。旧 ledger 缺参数证据时返回 unverified，不补写猜测的 hash 或复用旧结果。
结果提交使用写事务：matching 工具身份、相同 completed result 可幂等提交，不同内容不可覆盖。
handler 收到独立调用副本，不能通过嵌套 dict/list 修改已 claim 的请求参数。

Responses 完整 function/tool_search 项现在通过 ModelItemCompleted 进入真实 model node：
先以事务追加 partial_model_items 和 canonical history，再启动 LiveTools 所有的任务；模型
读取可继续，平行组之间以独占任务为屏障。完整响应与已发布调用核对，正常 tools 节点消费
streamed_tool_results，不重复 handler 或 UI 完成事件。Chat 缺分项终态时仍在整响应后执行。
partial-step 恢复不会伪造成功 ModelCompleted 或重采样旧 request，而是处理 durable calls 后
进入下一 step；已 claim 未完成返回 unknown，未 claim 调用可首次执行。普通模型错误等待
已启动工具收尾再记录失败，取消/fatal 会回收任务并补齐失败 Observation。steering 在结果落库
后接受新输入，避免压缩半个工具关系。第十三批补上 history-aware retry：call_model →
retry_model → call_model 保持当前 instructions，重新从 durable active history 构造请求和
发现工具视图，不重新运行普通 Step 的 contributors/compaction。steering 才进入新的准备。
model_failures 与 model_steps 同一 attempt 互斥；失败事实先于 graph checkpoint 提交，保存
普通与连接重试计数。恢复时先消费失败事实，不能重采样旧 attempt 或重置预算。sample_count
每次 attempt 前进，step_count 仅逻辑 Step 前进；旧 checkpoint 没有 sample_count 时兼容
step_count。失败写和 partial journal 写被取消时仍 join 数据库线程，原取消控制流不被掩盖。
已验证真实进程退出后的失败预算恢复；全部 provider/checkpoint 组合仍待验收，不称整体关闭。

Codex 默认 Stable 的 unbounded_connection_retries 已映射为 provider 同名布尔开关（默认
true）。请求层耗尽后，主采样 CONNECTION 错误采用独立 5→10→20→40→60s 退避，
不消耗 model_max_retries。
普通 TRANSPORT/流错误使用有界预算；两类等待都能取消/steer，结束前 join sleep/input tasks。
connection counter 持久化，崩溃后不会重新从 5s 开始。显式关闭开关时有界；
provider_name 不改变重试策略，没有原生 Bedrock adapter，不按名称启用专属分支。
内部 memory/compaction 请求不进入主图无界路径。图递归保护不替代逻辑 Step/tool 上限，也
不能提前切断持续网络恢复。当前仅 HTTP，没有 WS session，因此没有 WS→HTTPS fallback。

Responses 的 message/reasoning 现与工具共用有序 ModelItemCompleted/partial history。
ResponseContent 按 item ID/index 维护 delta、完整内容、opaque reasoning 与稳定 canonical ID；
重复 done 幂等、改写内容/加密状态/phase 或完成后 delta 均报协议错。纯 encrypted reasoning
也保存并重放，不再只保留最后一条。无分项 done 的兼容终态仍可生成规范项，匿名 delta 不再
与完整正文重复；内部 anonymous/index 标识不会伪造 provider reasoning ID。
ModelOutput 按 canonical message 隔离引用过滤和完成事件；引用规范化后才落库，usage 在项
完成后记一次。assistant phase 为向后兼容可选字段，Responses 重放时保留 commentary /
final_answer；Turn 的最终正文采用最后一条非空消息，不拼接同响应中的全部 commentary。
上述行为由实际 Runtime/Responses 混合输出、断流、下一请求重放与旧库测试覆盖，不能代替
所有 hosted 输出类型、provider 特例、context/memory 生命周期的验收。

工具结果的 conversation item id 由 thread/turn/call id 确定性生成。因此在“工具结果已落账，
graph node 尚未 checkpoint”的窗口恢复时，cached result 可以安全重放，但 append-only history
只会保留一份 tool result。

任意全局 item id 冲突只有在同 thread/turn/kind 且语义 payload 相同时才视为幂等重放；跨 thread
或内容不同的碰撞属于存储完整性错误，不能静默跳过。checkpoint context 每次启动尝试重新创建，
只有 setup、权限设置和 graph compile 全部成功后才发布给 runtime。

`corki resume` 恢复当前目录最近 thread，`corki resume THREAD_ID` 恢复指定 thread。存在
running Turn 和 checkpoint 时从 node 边界继续，否则由已持久化 user item 重建。

初期不使用 Codex 的 JSONL + SQLite 双写。只有需要完整事件回放、迁移兼容或审计时，再通过
独立 append-only event store 增加 JSONL，不能把双写塞进 graph node。

## 12. 配置与 Prompt

- 用户配置入口固定为 `~/.corki/config.toml`；
- `CORKI_API_KEY`/`OPENAI_API_KEY` 优先于配置文件中的 `provider.api_key`；配置文件密钥仅供
  本地开发兼容，必须使用 `0600` 权限且不得提交；
- `[provider]` 可配置 `thinking` 和 `reasoning_effort`；reasoning 作为独立消息字段保存、计入
  context 预算，并在工具续接请求中按 provider 要求回传；
- `[memories]` 默认关闭；`enabled` 是总 feature gate，`generate` 只决定新 Thread 的来源资格，
  `use` 控制召回，`dedicated_tools` 单独控制 scoped memory tools；Corki 专属
  `background_enabled`（默认 true）暂停自动后台任务，不能再把它与 generate 混为一谈；
- 自动记忆 pass 在每个新普通输入 Turn 首笔持久化完成后启动；初始化、手动压缩、pending
  恢复和 steer 不启动新 pass。所有并发 pass 都由 service 持有，DB claims 仲裁重复工作；
  wait 只观察已经接收的任务，返回最新报告，取消 waiter 不取消后台工作；close 先取消/join
  全部 pass，再关闭依赖。此前使用 generate=false 暂停后台的配置应显式增加
  background_enabled=false，不自动重写用户配置；
- 配置在 Turn 开始时生成不可变快照；
- prompt 正文存放在项目根目录 `prompts/`，按 `agent`、`modes`、`context`、`tasks` 分类；
- wheel 构建时将根目录资源映射到 `corki/_prompt_templates`，运行时优先使用
  `importlib.resources`，源码开发环境才回退到仓库目录；
- `PromptStore` 缓存解析后的原始模板；包含 Turn 动态值的渲染结果不缓存；
- 模板只支持 `#{variable_name}`，缺少变量立即报错，条件判断和组合策略保留在 Python 中；
- prompt 版本变化需要测试，不能在多个 node 中复制长字符串；
- 日志不得写入 API key 和完整敏感环境变量。

## 13. 分阶段开发路线

### M0：CLI 骨架（已完成）

- `corki` console script；
- Codex 风格启动页和输入框；
- `Ctrl+C` 退出；
- `~/.corki` 目录；
- 本地 `/help`、`/status`、`/clear`。

### M1：类型化协议与纯聊天纵切（已完成）

- `ThreadId`、`TurnId`、状态和 runtime events；
- async `CorkiRuntime`；
- 显式 `StateGraph`；
- deterministic fake model 与两种真实 provider protocol adapter；
- assistant token 流式显示；
- 当前进程内多轮历史；
- `Ctrl+C` 取消正在执行的 Turn。

验收：没有工具时，模型可以连续对话，事件顺序确定，fake stream 的测试完全可重复。

### M2：工具循环（已完成）

- ToolSpec/Call/Result；
- registry 和 executor；
- 先用 deterministic test tool 验证 `model -> tool -> model`；
- 实现 `exec_command`；
- 实现 `apply_patch`；
- tool output truncation。

验收：模型能够读取项目、修改一个文件、运行测试并依据真实输出完成回答。

### M3：项目上下文（已完成）

- workspace/project root 发现；
- Git 分支和 dirty 状态；
- 分层 `AGENTS.md`；
- 基础 coding prompt；
- 每个 Turn/Step 的不可变上下文快照。

### M4：会话持久化（已完成）

- thread/turn/item SQLite 持久化；
- LangGraph mid-turn SQLite checkpointer；
- session resume 与模型/tool 幂等恢复；
- usage/model metadata 持久化。

### M5：长任务稳定性（核心已完成）

- context token 估算与多段 compaction；
- 长进程、PTY 与 `write_stdin`；
- HTTP/采样/连接恢复的独立 retry 分类和预算、SSE 指定延迟、流完整性和 response 硬上限；
- 有界事件背压、取消与崩溃恢复；
- migration、fault injection 和 PTY E2E 测试。

后续里程碑包括审批/sandbox、多 Agent、语音 realtime 与更完整 telemetry。它们必须
通过已有端口接入，不能绕过 canonical item、runtime event、tool registry 或 repository。

### M6：Skills、MCP、插件与 realtime 文本 steering（已完成）

- 系统、用户、项目和插件四层 skill discovery；
- 10 个内置技能及原子指纹安装，`$name` 显式注入和 `skill_list`/`skill_read`；
- MCP stdio/HTTP 生命周期、远程工具代理、resource/template/prompt 聚合工具；
- Codex-compatible plugin manifest，加上 Corki Python tool 扩展；
- 生成中的有界输入队列、模型流打断、canonical history 持久化和重新采样。

验收：所有能力可由 fake provider/MCP 在离线测试中稳定复现；单个失效 MCP 或插件不会让
其他可选扩展失效。

### M7：Codex 风格跨 Thread 长期记忆（旧里程碑记录，完整对齐未完成）

- Phase 1 对eligible且空闲的非当前Thread做有界抽取，不要求存在成功Turn；过滤无价值内容并脱敏常见secret；
- Phase 2 使用全局 lease、watermark、usage/recency 输入选择，生成 `MEMORY.md`、
  `memory_summary.md`、rollout summaries 和可复用 memory skills；
- 普通 Turn 只注入 compact routing index，随后通过 shell 或可选 dedicated tools 渐进读取；
- 记忆引用从流式正文中隐藏，作为 canonical assistant provenance 持久化并回写 usage；
- external MCP 污染策略、过期 lease 接管、失败退避、旧 artifact 保留和 symlink 边界均有测试。

与当前 Codex 一致，这不是 embedding/vector RAG。它用模型做语义抽取和归并，用小型 summary
路由，再对 Markdown 做关键词搜索和按需读取。Corki 当前 Phase 2 已使用独立临时 Runtime，
复用正常多 Step、工具调度、Observation、上下文和关闭流程，不再用单次 ModelPort 请求
替代合并 Agent。它借用模型传输但不关闭父传输；禁用递归记忆、MCP 和插件。
合并说明是用户任务，历史证据是独立 ContextItem，不触发显式输入的技能展开；保留正常
base instructions、环境、规则、用户/系统及配置目录技能。规则和工作目录技能以原始记忆
目录为发现来源，工具 cwd 则是暂存副本；父线程历史和插件能力不继承。
可编辑产物后返回普通完成正文，也保留完整 JSON 产物的兼容输出。确认子 Runtime 关闭
后才通过既有租约检查发布并更新基线。关闭等待上限 10 秒，超时/失败保留 Runtime、
关闭任务、副本和租约；晚到的关闭成功只回收副本，不发布记忆或更新作业/基线。
失败状态通过 retained_workers 和 warnings 保留；父服务的关闭等待独立有界，取消
等待者不放弃共享清理。这是进程内任务所有权，不是重启后恢复操作系统进程。
暂存不是 OS 沙箱，跨文件/SQLite 原子性、权限配置及完整原生配置/模型指令对齐仍未完成；
不能据此认定 repository、artifact 或 read path 契约已经一致，完整差异以源码审计为准。

记忆阶段model按显式memories.extraction_model/consolidation_model→provider的
memory_extraction_model/memory_consolidation_model→固定Codex默认gpt-5.6-luna/
gpt-5.6-terra解析，不再隐式复用main。第三方provider可配置对应有效ID；不可用时仅失败
后台任务，不静默换main。提取预算使用同一个resolved model。阶段ModelRequest分别带
reasoning_effort low/medium，None的普通请求继承adapter既有值；Chat/Responses发送时
仍按capability过滤，不能为后台请求修改共享adapter字段。OpenAI Chat profile现允许
发送effort；完整逐model能力目录仍开放，不能把窗口投影当作所有model能力元数据。

Stage 1领取在SQLite BEGIN IMMEDIATE内完成：先按毫秒年龄/idle/更新时间扫描最多5000个
enabled来源，再probe输出与成功来源水位；max_threads_per_startup同时限制共享DB中的有效
running lease总量。每来源最多3次记录失败，新版本才重置次数并越过退避；过期接管本身不
消耗失败次数。独立last_success_source_updated_at不会被后续失败覆盖，无输出也推进水位。
新提取任一raw/summary为空时成功删除旧stage1输入，实际删除才enqueue合并；产物同步去掉
旧rollout summary，最终MEMORY.md仍由合并发布，不在提取节点直接改写。旧库新增列时保持
既有owner/lease/backoff，无法还原的历史失败次数从3开始。完整来源类型/归档/ephemeral、
官方账户 quota、官方认证及远程目录属于用户明确排除项；Phase 2 核心策略仍需逐项核对。

已领取提取的提交按running job的owner校验，不因Thread后来禁用/污染或更新时间改变而
拒绝已采样结果。开关用于领取和Phase2选输入，不能把DB保留等同于继续召回；重新启用后
原提取可再次被选入。产物upsert只允许来源版本不早于已存版本，等版本允许刷新；精确解析
UTC/offset/legacy时间避免julianday丢失微秒而误覆盖更新内容。job成功、水位、产物和enqueue
同事务，失败回滚，取消join已开始写线程。空输出仍删除旧输入，不另加版本过滤。

Thread来源开关现通过Runtime.set_thread_memory_mode和/memory mode enabled|disabled
持久化，普通/realtime CLI都不采样或steer模型。当前Thread可在首Turn前materialize；
显式其他UUID只能更新已存Thread，缺失报错，不创建虚假来源。SessionRepository初始化同一
memory_mode列，旧库无列默认enabled、已有mode保留，不依赖可选memory service或生成开关。
公开枚举仅enabled/disabled，polluted继续由内部污染流程负责；不修改global generate/use、
source更新时间、jobs、水位、历史或已发布内容。宿主控制器串行写入，取消join已开始的创建/
更新；Runtime关闭立即拒绝排队操作，等待已拥有写任务后才关闭repository。自定义会话存储
必须实现setter，不能把缺能力当成功。完整archived/ephemeral和live全局配置联动仍未对齐。

新Thread的初始mode现由memories_generate决定，独立于feature enabled和recall/use；
Runtime._ensure_thread把typed mode交给SessionRepository.create_thread的keyword参数，
SQLite在同一INSERT中写入，不留下先enabled后改disabled的跨连接窗口。INSERT OR IGNORE
保留已有Thread的显式/polluted模式、cwd和历史，重开时不按当前配置覆盖；旧库已有行不
追溯禁用。直接storage调用省略keyword仍默认enabled；自定义Runtime session adapter需
接收该创建元数据，不支持时明确失败，不能静默忽略禁用请求。

提取使用memory/transcript.py对原始append-only items构建provider-neutral JSON档案，不
使用active_history，也不伪造历史provider wire字段。保留调用ID/结果ID、freeform输入、
错误、phase、发现定义和有序媒体；不输入Context/reasoning/压缩marker、UI显示或工作状态。
user正文逐块排除首尾完整匹配的AGENTS/skill片段，采用ASCII大小写不敏感规则；其他文本/
媒体保留，authoritative content_items覆盖旧content/attachments，不能重复附带过滤前内容。
压缩克隆UserMessage记录retained_from_id（重复压缩保持最初source ID），主模型仍可见，
记忆提取跳过该副本；replacement内首次写入的新用户输入不打标。None字段不写旧JSON，
旧checkpoint缺字段默认None；无来源证据的旧副本不猜测丢弃。脱敏发生于JSON编码前，
避免正则破坏转义。sanitizer.py现按Codex的Bearer→OpenAI→AWS→assignment顺序、阈值
和REDACTED_SECRET标记执行，保留assignment前缀/分隔符/引号；仅为空格/tab的Bearer
分隔不再跨行。它仍是有限best-effort识别，不保证所有凭据检测或真实提取质量。
Stage1请求schema仍要求三个字段且slug nullable；解析允许缺省slug，但拒绝未知/重复
字段和错误类型。raw/summary/slug不trim，缺省或null slug为None，空slug保留为空字符串。
仅raw或summary的实际空字符串触发no-output；纯空白可持久化，但后续合并候选仍按既有
独立的trim规则过滤，不能把两个阶段的条件混为一谈。

模型窗口由不可变ModelContextInfo与[models.catalog.<完整模型名>]静态配置解析，优先
context_window、其次max_context_window；models.context_window_override显式跨模型
覆盖并按各模型max截顶。main无catalog时沿用agent窗口/有效百分比，独立未知模型不借用
main默认，而使用Codex generic context=max=272000、effective95并告警；这是fallback
假设，不是provider能力已核验。Runtime主窗口及skills预算使用同一解析结果；Stage1按先raw×effective/100、
再×70/100且最小1计算。extraction_token_limit现在默认None表示自动；已有显式值为已知
模型的额外上限，仅元数据确实无窗口时可覆盖150000 fallback。不是所有已知模型都固定150000。
memory/inputs.py的源码式截断按4 UTF-8 bytes/token保留头尾各半并附加marker，marker
不扣保留预算；截断后不保证JSON完整。该路径不替代普通上下文估算或合并预算，远程模型
目录刷新和provider默认提取模型仍为独立开放合同。静态目录名称匹配先选完整请求名的
最长前缀；只有无命中才允许剥掉单层ASCII字母数字/_/- namespace再最长前缀匹配。
不额外要求版本分隔符，不忽略大小写，不广泛剥多层路径；显式目录项本身含多层路径
仍可直接匹配。完整名前缀优先于更长suffix候选，同长首项优先，返回metadata保留原始
请求名，随后才应用global override/max clamp；不会把发给provider的model改成目录前缀。
未配置catalog时使用bundled_models.py中固定源码11条窗口投影；显式空/自定义catalog
具有权威性，不与内置合并。None与空tuple在Settings中刻意区分。内置路径的main仍用
现有agent窗口/effective设置但受内置max限制；独立记忆模型取得完整的窗口/max，不能
用generic max272000替代Luna/Terra的max872000。该静态快照不是实时provider容量保证，
远程/缓存权威性、主模型默认配置来源以及原生phase2 agent上下文仍未宣称完成。

Thread preview由SessionRepository在canonical UserMessageItem写入事务中维护，首个有效值
不被后续消息覆盖；Rust Unicode trim及用户前缀剥离后非空文本优先，其次图片/音频占位。
ContextItem(user角色)、assistant和压缩retained副本不提供来源资格。旧库在legacy message
迁移后、同一schema事务中回填，不改原始历史或updated_at。Stage1 SQL在scan LIMIT前排除
空preview，不要求源Turn成功。

SessionSource为宿主类型，不从模型metadata推断；Runtime.create传入，create_thread初建时
持久化，重开不覆盖原来源。CLI传Cli、内部合并传Internal(memory_consolidation)，通用宿主及
旧库默认VSCode。当前启动者Internal/SubAgent跳过后台pass，root Exec/Mcp不被误禁用。
历史来源SQL在scan前匹配cli/vscode/atlas/chatgpt；固定Codex版本的Custom写入JSON而
allowlist使用Display，canonical Custom(atlas/chatgpt)并不命中，legacy裸字符串仍命中。
保留该实际区别；读取未知持久化tag降为Unknown，外部startup前缀不赋予内部来源身份。
后台服务构造不再准备目录；run_once先执行cancellation-owned准备，失败告警返回failed报告、
不prune/claim、不失败主Turn。显式dedicated工具backend初始化是独立路径。单Thread归档和
独立RAM会话见第11节；完整ephemeral源导入、subtree、动态认证和父权限继承仍未关闭，不把
source门当作这些功能的替代。

官方账户配额路径已按用户范围排除：移除专用传输、解码和记忆启动门禁。
记忆只使用配置的模型服务，不查询 Codex/ChatGPT 账户。旧配额阈值不再读取；
普通模型限流错误与后台失败隔离继续保留，通用 MCP OAuth 不受影响。

生成pass开始前经MemoryRepository.prune_stage_one_outputs清理最多200条未selected的
过期输出；recency=last_used_at或source_updated_at，不使用generated_at。清理不删除
jobs/成功水位、conversation或直接改写已发布MEMORY，失败告警继续，取消会等待已启动的
SQLite写线程退出。提取更新保留上轮selected标记及版本；只有phase2成功重写baseline，
避免清理把仍属于旧成功工作区的记录提前删掉。合并输入JOIN当前enabled Thread，过滤
raw/summary均为空白后再Top-N：usage DESC、use/source recency DESC、source DESC、
thread_id DESC；结果另按thread_id ASC返回。usage port按每个输入信号计数、缺失ID忽略，
同批固定时间戳；正文引用解析自身的去重/历史恢复规则不由此改变。

Phase2领取不使用DB水位作为dirty gate：缺行也可原子创建/领取，持锁后同步输入并检查
workspace指纹/产物合法性。成功（含无变化跳过采样）后默认冷却6小时；新输入不绕过。
同步输入在既有SQLite owner写事务内清理扩展过期资源，再采样workspace/检查baseline。
只处理带instructions.md的extension中resources/直接regular .md文件；文件名UTC时间戳
达到七天即过期，不按mtime判断。notes、嵌套目录、其他类型和无效时间戳不删除，链接不跟随。
单项I/O失败日志告警继续；取消必须join已开始的写线程。过期删除不因后续模型失败回滚，
旧发布与成功baseline保持，重试仍能看到删除diff；不提供新增归档或conversation遗忘。
显式reset独立于ad_hoc忘记指令：Runtime.reset_memory与确认后的/memory reset confirm
清除outputs/两种memory jobs，再清理实际memory root与capability home的memories_extensions。
预览从Runtime.memory_reset_targets取得真实目录，不由UI猜测。目录本身保留；包含notes、
skills和私有baseline，无新增备份。线程、原历史、checkpoint与memory_mode不删除，之后仍
可能重新提取。DB与文件不是原子集合，失败报告database_cleared；旧owner不得晚到发布。
宿主内串行、开始后的取消join、Runtime关闭先等reset；不声称跨进程阻止新claim或撤回旧
上下文正文。custom root拒绝workspace/home/数据库祖先等宽泛目标；根链接拒绝、子链接
仅删除链接。自定义repository需提供clear_memory_data能力，未提供时不删除文件。
失败retry_at独立于status检查，不因重复启动变pending而失效；run_once不再根据文件/
stage1是否存在而无条件enqueue。真实提取输入变化或显式enqueue才推进单调水位，并在
非running时清除退避；保留已有finished_at/error，仍遵守成功冷却。Phase2失败计数会
递减到0但不禁止后续到期领取，与Stage1的3次硬上限不同。finished_at新增列不回填
旧updated_at，未知历史成功时间允许正常工作区检查。兼容参数enqueue(force=...)保留
调用形状，但force不再作为跳过空库或绕过冷却的开关。空库缺少产物也会进行一次隔离
模型合并初始化；失败仍不影响主Turn。完整原生subagent/git diff内容协议仍未声称完成。

## 14. 测试策略

执行策略增量（第187批）：显式配置execution后，Runtime在Thread/MCP/model之前读取
home/rules/*.rules，并用固定Codex解析器校验。普通文件按名排序，文件链接不纳入；
目录缺失忽略，其他IO/UTF-8错误致命；语法错误清空用户规则并告警。有效文本冻结进
执行快照，普通/Code Mode shell共用Never准入；全部子命令显式allow才可绕过沙箱，
独立拒读仍有效。Never不是危险命令自动许可，prompt规则在该模式下被拒绝。
启动失败重试重新加载，冷启动重新加载；显式父快照继承须配置目录/声明来源一致。
仅复制settings不构成继承，后台记忆新内部会话重新加载。编译器须同时支持
exec_policy_loaded与exec_policy_checked，否则明确失败。当前只接通home和宿主文本，
未完成默认无编译器路径、项目/系统规则层、托管exec规则、交互审批、自动规则更新和
持久化恢复；见native/sandbox/README.md与harness-alignment/audit.md的阶段证据。

第188批已进一步接入托管requirements的[rules].prefix_rules，固定native组合器保留
各层规则，再与用户规则取最严格结果；非法托管规则致命，坏用户规则回退不删除托管
约束。父快照复用在native端比较规则指纹及来源，规则重排等价，规则/来源变化重载。
普通/Code Mode/后台memory均使用捕获的托管规则；编译器新增managed_exec_policy确认。
该增量关闭上述“托管exec规则未接线”部分，不代表交互审批或整个权限系统完成。

第191批接入显式execution下的规则/危险命令交互审批：顶层approval_policy="on-request"
或granular策略经native检查后交给独立宿主审批router；CLI共享输入锁但不复用MCP授权。
accept+remember才缓存精确命令/cwd/tty/环境规则指纹，批准仍受沙箱约束；外部取消join
审批和准备，表单cancel返回工具错误。后台记忆和基础guardian保持Never。缺少编译器或
typed shell_approval/exec_approval合同不能静默执行。默认trust选择、提权、规则修改、
托管审批约束、Guardian/hooks及网络重试仍未完成；不要把此显式路径称为整个审批完成。

第192批接入模型主动require_escalated及justification：Direct/Code Mode共享实际native
准入和审批，Never/Granular禁用的override先于显式allow拒绝；获准也保留独立deny_read。
缓存区分原始权限意图，普通命令的批准不能用于提权，提权不修改后续命令/后台策略。
justification有值时必须显式sandbox_permissions，反向不要求必填justification。
编译器必须确认model_escalation，未配置execution不能隐式提权。额外权限、prefix_rule/
规则修改、sticky授权及完整默认/托管审批/网络重试仍未完成；以审计中的最终验证为准。

第193批纠正191的表单cancel结论：Codex客户端Cancel经ExecApproval(Abort)中断整个
Turn，不等同内部审批receiver的Abort→工具错误。Runtime验证待处理shell token后
直接触发active run取消，清理并行审批/Code Mode且不再采样；迟到/伪token不能中断新Turn。
Decline仍是Observation错误，MCP取消语义未改。Python stream继续遵循既有约定：
先发TurnCancelled，再抛CancelledError。见审计中本批最终验证，不再引用旧cancel测试
作为一致证据。

第194批接入prefix_rule候选→宿主once/session/rule选择→native文件锁追加→会话overlay及
冷启动规则读取。模型候选不自动授权或落盘；保存失败发Warning但保留当前批准，不发布
规则或附带session缓存；取消join已授权保存后终止模型命令。native按所有解析命令验证
候选并应用原生fallback，缓存使用原executable+native canonical command。已存在的
model_specialty=cyber元数据现在经ToolContext抑制用户Allow前缀/规则建议，保留其他规则。
managed auto_review.ignore_rules、动态父snapshot继承、Guardian/网络重试和额外权限仍
未完成。规则保存不是原子rename/fsync事务；实际原生append合同与验证见审计及native说明。

第195批将实际执行策略接入模型上下文：配置执行权限时复用固定Codex原生渲染器，
未配置compiler的legacy路径明确显示Disabled/Never。默认发送权限说明；顶层
include_permissions_instructions=false切换为原生简略模式（初始静默、仅通知新前缀）。
已保存规则只追加增量，删除规则/压缩丢失基线时重发完整说明，失败保存不误报成功。
与工具执行共享会话规则owner、用户规则过滤及独立托管overlay；实际接入Direct/CodeMode，
包含跨Turn、冷恢复及手动压缩组合验证。渲染使用有归属、可取消的native子进程及单视图缓存；
旧compiler缺typed ack会在模型采样前显式失败，须重建，不会猜测权限或静默省略。
30KB/10000估算token硬边界拒绝超大权限片段，不截断策略；模型自定义权限文案、自动审批者
和额外权限工具仍需另行对齐。全量/安装态阶段证据见审计，不以本段表示A–E整体完成。

第196批接通models.catalog的审批/权限文案：缺失/null回退、空串抑制；文案进入类型化
ModelContextInfo、业务snapshot和白名单checkpoint，并保留内置目录覆盖。权限文字只
替换精确network占位符，其他内容原样保留；legacy Disabled/Never与原生渲染逐字对照。
消费者保持该参考版本的冻结Turn模型语义，Step切换不提前改文案，下个Turn可采用新文案。
覆盖审批说明不取消保存规则的独立增量通知，也不关闭实际Never/沙箱执行检查。native
需要新model_permission_messages确认位；模型文案数据不等于自动reviewer或Guardian实现。

第197批为宿主显式父子Runtime增加execution_policy_handle：与旧不可变快照入口兼容，
但只有配置目录、声明sources和native managed identity一致才共享后续规则更新与锁。
每会话独立保留落盘路径、审批router/session cache、进程和关闭；权限上下文与执行端
读取同一live状态。handle不进入持久化字段，跨event-loop拒绝，basic guardian及独立
memory不自动共享。这不是完整AgentControl/fork实现；本批未更改native协议或二进制。

第198批将保存与live发布分开：共享锁内捕获完整current policy，由native先append再检查
提案是否已有真实Allow覆盖；被覆盖的窄规则仍落盘，但不额外发布或通知。raw用户规则、
当前live前缀和managed约束均参与，不以磁盘重读或cyber模型过滤视图替代。需要重建桥接
以返回独立typed publication确认；旧/非法ack仅警告并保留当前批准，不发新授权或自动重试。
取消继续join写入与发布，匹配结果不改变当前命令的既有审批/沙箱意图。

### Unit

- graph routing 使用纯状态和 fake event；
- model adapter 使用录制的响应片段；
- tools 使用临时目录和短命令；
- context builder 使用样例仓库；
- storage 使用临时 SQLite；
- memory pipeline 使用 fake model，覆盖 lease 并发、空输出、失败退避、污染、引用和文件边界；
- unit test 不访问真实模型和用户 `~/.corki`。

### Integration

- fake model 按脚本依次返回 tool call 和 final answer；
- 验证完整 graph loop、消息顺序和 checkpoint；
- 验证工具失败仍能回到模型；
- 验证取消时 graph 和子进程都结束。

### E2E

- 在伪终端中启动已安装的 `corki`；
- 使用临时 `CORKI_HOME` 和临时 workspace；
- 验证启动、输入、流式输出、工具展示和退出；
- 默认不依赖真实网络或 API key。

每个 bug fix 必须先增加能复现问题的测试；provider 的在线 smoke test 单独标记，不进入默认测试。

## 15. 编码规范

1. Python 3.11+，完整类型注解，优先 dataclass、Enum、Protocol 和 TypedDict。
2. I/O 边界使用 async；不得在 async node 中直接运行阻塞 subprocess 或同步网络请求。
3. 注释解释“为什么”和边界条件，不逐行翻译代码。
4. 公共类和函数必须有 docstring；内部显然的短函数不堆砌注释。
5. 不使用裸 `dict[str, Any]` 穿过包边界；先定义协议类型。
6. 不在核心层 `print`；通过 event 返回 UI。
7. 不捕获异常后静默忽略；在正确边界转换成领域错误或失败事件。
8. 不读取全局 cwd 作为隐藏状态；cwd 必须来自 TurnContext。
9. 不直接在 node 中创建 provider、registry 或数据库；统一在 composition root 注入。
10. 保持用户已有工作树，不执行破坏性 Git 操作。

## 16. 本地开发

```bash
cd /Users/corki/PycharmProjects/corki
source .venv/bin/activate
pip install -e '.[dev]'
```

运行：

```bash
corki
```

检查：

```bash
ruff check src tests examples main.py
ruff format --check src tests examples main.py
pytest
python -m compileall -q src
pip check
python examples/core_loop_demo.py
python examples/builtin_tools_demo.py
python examples/extensions_demo.py
```

提交一个里程碑前还应运行对应 integration/e2e 测试。真实模型测试必须显式启用，不能成为默认
测试的必要条件。

## 17. 下一步

CLI 冷恢复先读取 Thread 的 model/provider/selected effort 元数据，再在 composition
root 解析配置和构造 Runtime。任一显式 model/provider/effort 覆盖跳过整组持久化值；
旧库无记录时使用当前配置，记录中的 effort=None 则清除新配置 effort。provider 身份
与 adapter capability name 分离；从当前命名 profile 解析传输与凭证，不从历史恢复密钥。
Runtime 初始化在 checkpoint 编译成功后、Turn admission 前以 cancellation-joined 写入
完整组；写入失败关闭本次 checkpoint，不能继续采样。该记录不是业务历史 item，不受
压缩替换影响，也不修改 memory source mode。

独立 `update_thread_settings` 只更新未来 Turn 的模型/effort/summary/tier；与 admission
共用生命周期锁，取消等待者也必须等待持久化和内存发布结束。已接纳 Turn 使用独立 graph、
window 和技能预算视图，共享 Thread body-prefix 状态。模型选择与 ModelInfo 快照随首次
TurnRecord 原子落库并进入 checkpoint；恢复校验两份快照和 provider 身份，终态写入保留
原快照。它不是全部 Config 的持久化。Code Mode 已接纳调用保留原 worker，新调用进入
当前 worker。`step_model_switching` 默认关闭；独立 `update_turn_settings` 为同一个存活任务
串行解析并发布稀疏更新，不改变未来 Turn 默认值。capture 节点在异步 prepare 前以同步
checkpoint 持久化；采样、重试与工具使用捕获的视图，工具策略/预算限制仍属于接纳 Turn。
恢复不重新解析已捕获 metadata；尚未被捕获的更新不承诺跨崩溃持久化。当前只覆盖无托管
审批、固定 full-access 的 host 路径；托管审批、reviewer、环境和协作模式更新仍未完成。

当前已有纵向闭环和离线测试，不代表恢复、长任务稳定性与长期记忆已完整对齐。
优先完成 [Harness 源码审计](harness-alignment/audit.md) 中的差异修复与验收；
下列原路线图不覆盖或缩减该目标，也不能作为拒绝必要主循环修改的依据：

1. 增加 thread 列表、真实 `/status` 聚合和可观测性导出；
2. 为已知模型增加精确 tokenizer，未知 provider 继续使用保守估算；
3. 持续维护显式 provider capability profile 和 opt-in 在线 smoke test；
4. 通过现有 ToolExposure、PromptSlot、ToolRegistry 契约接入审批/sandbox、多 Agent、
   语音 realtime，并继续补齐 Code Mode 的已记录差异。

## 18. Skills、MCP、插件与 realtime 开发约定

### 18.1 Skills

启动时将源码根目录 `skills/` 原子同步到 `~/.corki/skills/.system`。发现优先级是当前目录到
项目根的 `.corki/skills`，随后是同层 Codex-compatible `.codex/skills` 与 portable
`.agents/skills`，再到用户 `~/.corki/skills`、`~/.agents/skills`、`~/.codex/skills`、系统
skills 和插件 skills；同名时高优先级胜出，插件 skill 使用 `plugin-name:skill-name` 避免冲突。
元数据目录按Step作为world state更新；Turn开始时显式 `$skill-name` 会读取 `SKILL.md`，
在触发它的用户输入之后追加独立user片段。正文与目录分离：后续Step不重新读取，不因
新Turn未提及或文件移除而撤回旧历史；新Turn再次提及会再次注入，不能按Thread永久去重。
新输入准备时的压缩保护尚未采样的正文；后续mid-turn压缩不自动重注入旧全文，模型可通过
`skill_read`重新读取。与固定Codex的turn_input入口一致，steering只记录新输入，不重复
Turn-start显式正文注入；这不禁止模型按需读取被提及的技能。已选技能读取失败发出临时
WarningEvent并跳过该正文，继续其他技能及采样；CLI字面显示警告，不伪造工具结果，不吞掉
取消或未知基础设施异常。警告不持久化、不在冷恢复时重放；prepare重新执行可能再次报告
读取失败，不提供跨崩溃恰好一次通知。旧selected快照/错误tombstone
仅修正请求投影，不回写旧库。语义匹配也由模型调用 `skill_read` 完成。引用资源必须留在
skill目录内并受
128 KiB 上限约束。`compatibility_home` 只由生产 composition root 注入，测试默认不扫描真实
用户目录。与 Codex 一样，project/user skill root 允许链接到共享 skill checkout，并用真实路径
去重防止 symlink cycle；发行包安装的 `.system` root 不跟随目录 symlink。

host目录由skills/catalog.py单独分配预算：默认配置窗口2%，未知窗口8000字符；
`[skills] max_context_tokens`为正整数覆盖值，最多10000。这里token=ceil(UTF-8 bytes/4)，
按元数据行含换行计费，不是整个请求预算。先保留所有完整name/path行，再round-robin
分配description字符；minimum行仍超预算才逐行省略，可跳过大行保留后面的短行。
absolute和root-alias候选以覆盖数量、描述保留量、成本排序，根表收费且路径展开可逆。
目录省略不改变发现/显式选择/read集合。Corki额外的plugin命名scope仍保留独立身份，
没有据此声称实现Codex的Admin来源或executor/orchestrator共享目录预算。

PromptContribution.warnings只进入ContextSnapshot.section_warnings，不进入模型items；
Window在真正更新/压缩重新注入时返回PreparedHistory.warnings，GraphRunContext按临时
Turn/message去重后发WarningEvent。此通知在窗口事务成功后发出，不做崩溃间恰好一次承诺。
目录更新直接追加新的skills fragment，移除使用host unavailable说明；历史前缀不改写。
隐藏配置、完整选择匹配、来源加载冻结等其余目录语义仍见核心审计的开放项。

隐式可见性来自独立的agents/openai.yaml，不从SKILL.md正文猜测：
policy.allow_implicit_invocation默认true，false仅隐藏自动目录和模型skill_list；
显式选择/skill_read仍允许enabled hidden。optional metadata无效时只记录日志、保留
有效正文并回退默认；sidecar创建/编辑/删除参与技能缓存指纹。
SkillSnapshot分别提供is_enabled/is_visible，disabled_paths不删除原始metadata，也
不触发同名低优先级定义回退。用户配置[[skills.config]]按name或canonical path顺序
覆盖，接入Runtime创建的SkillService；相对路径按该配置文件目录展开，重复selector
保留最后一次的位置。无效selector日志跳过，规则结构无效日志回退空规则。Corki目前
只有一个用户配置文件与直接Settings输入，不等于Codex全部分层配置来源已完成。
这些开关不是文件读权限，也不撤回旧对话正文。全局skills.include_instructions缺省true，
false仅隐藏自动目录，不关闭显式输入/skill_read/按需skill_list；与关闭整个service的
skills.enabled、单技能allow_implicit_invocation不同。开关通过Settings接入Runtime，
暂需重开Runtime应用配置变更，不宣称已有live config事件总线。
目录比较body和开关；首次无正文不发消息，已有状态后hidden/empty各发对应通知，重复
状态不重复发送。静默基线与用户输入原子追加，冷恢复和压缩后仍能正确比较。
MCP dependencies/interface UI、product
restriction来源、结构化UI mentions及hosted connector同名冲突检查仍在开放审计范围内。

frontmatter读取完整UTF-8文档，允许缺省/空name回退父目录，description与short-description
完整保留；目录渲染预算不再混入解析上限。首尾---必须整行trim后匹配，解析失败仅进行
Codex的限定逐行scalar repair，保留block、引号和注释；仍失败保留原始错误。
yaml_fields基于安全语法树读取已知字段，保留标量原文/style/tag并拒绝已知重复字段；
no/off不再被PyYAML的YAML 1.1规则解释成false，quoted false也不作为bool接受。
这不是整个serde_yaml规范等价的声明。
文本召回识别精确无歧义$name及[$name](path)，忽略常见环境变量，链接路径不匹配时
不回退标签名；先path后plain name，各按发现顺序。discovery_path保留逻辑symlink目录，
目录展示和链接选择可使用它，读取身份仍为canonical path；不改变正文注入/恢复生命周期。

系统技能固定为 10 个：Codex 来源的 `imagegen`、`openai-docs`、`plugin-creator`、
`review-agent`、`skill-creator`、`skill-installer`；Hermes 来源的 `arxiv`、
`grounded-citations`、`pdf`、`xlsx`。选择原则不是继续堆叠编码方法论，而是补齐通用 Agent
的高频能力：`arxiv` 按 submitted date 倒序检索最新论文；`grounded-citations` 为通用调研
维护可验证的 URL 与证据账本；`pdf` 负责 PDF 创建、读取、表单和页面操作；`xlsx` 负责
表格创建、读取、公式、图表和 CSV 互转。Office/OCR 的额外能力后续通过用户 skill 或插件
扩展，避免首批内置目录功能重叠。

`openpyxl`、`pypdf`、`pdfplumber`、`reportlab` 属于正式运行依赖，保证 `xlsx` 和 `pdf`
在标准 Corki 安装中即可执行；仅页面栅格化、LibreOffice 重算和 OCR 等体积较大或依赖
系统程序的能力保持可选，并要求 skill 明确降级结果。

### 18.2 MCP

在 `config.toml` 的 `[mcp.servers.<name>]` 配置 `transport = "stdio"` 加 command/args，或
`transport = "http"` 加 URL/headers。Manager 在 graph 编译前初始化各 server、分页调用
`tools/list`，并注册为 `mcp__<server>::<tool>`。每个 server 独立失败；工具结果和异常仍经过
统一 ToolExecutor 的 schema 校验、输出预算和 runtime event。资源侧通过
`list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource`、
`list_mcp_prompts`、`get_mcp_prompt` 聚合，但 JSON-RPC 始终留在 MCP 包内，core 不直接处理。
稳定生命周期与当前 Codex 一样以 MCP `2025-06-18` 为首选版本，并在 HTTP session 后续请求
中使用 initialize 实际协商的版本；实验性的 `2026-07-28` discovery 尚未启用。
启动先在 manager 内暂存 client/tool，全部网络 discovery 结束后才一次性发布到 registry；包括
`CancelledError` 在内的中途退出都会关闭已启动 client、回滚 handler，并允许同一 manager 重试。

运行中可用 `/mcp refresh` 请求重连当前服务器，或通过 Runtime 的 `request_mcp_refresh`
提交完整的新 server settings。请求只设置 pending；prepare 与 MCP 调用准入会在同一 gate
中消费刷新，先暂存后发布，取消恢复 pending。已准入调用保留精确连接，旧连接在调用释放
后关闭，不能重放副作用；尚未准入调用先解析最新 binding，移除的 server/tool 返回错误。
普通 registry 仍然 sealed，只有 composition 时取得的 owner 能原子替换自己名下工具。

第95批接入整个暂存catalog的canonical namespace/leaf命名：ASCII字母数字下划线保留，
其他字符（包括连字符）转下划线；同一raw工具首条保留，sanitized namespace/leaf碰撞双方
加SHA1/12位后缀，按raw identity排序分配namespace+两字节预留+leaf总长<=128的名字。
标准MCP不信任tool._meta的connector信息，namespace说明来自initialize，独立512KiB字节上限。
搜索文本的flat identity严格按Codex helper直接拼接namespace与leaf，不额外插入分隔符；
这与hook式展示名及兼容provider的hash alias不同。空namespace说明在native wire保持空。
`[tools].non_prefixed_mcp_tool_names` 默认false；启用后server列表缺省=全部去前缀，
空列表=不去前缀，非空列表按原始server名选择。Raw server/tool路由不随canonical改名变化。
旧历史/账本不改名，compatible旧发现失效后重新检索；native历史原样保留。工具目录不能覆盖
claim返回的已验证完成结果、结果未知拒绝或call-id冲突错误，也不能猜测旧扁平名的新执行目标。
模型 Step 使用隔离的 schema 快照；源码参考的后台预热、auth/environment 自动失效和完整
MCP surface policy 尚未全部实现，不将显式刷新描述为所有动态路径一致。

initialize 的可选 instructions 必须为字符串或 null；非法类型导致该 server 初始化失败，
不影响其他 server。完整说明作为搜索元数据及 source_description 保存，不提升为 system
指令。默认 tool_search 的来源按名称排序去重，聚合列表先预留完整名称，再分配 512 KiB
UTF-8 说明预算；超长名称整体跳过，截断不切断多字节字符。此上限不是额外 context 配额，
最终 schema 描述照常计入完整请求预算。说明变化更新来源与搜索索引；兼容路径投影中
失效的发现定义被移除，原生路径保留历史 tool_search_output，不按当前 registry 改写它。
raw 历史始终保留旧值；新字段为 None 时不写入 payload，兼容旧历史格式。
普通 MCP 的 callable/raw name 均参与词频，搜索顶层参数名而非递归 schema 说明；不信任
tool._meta 自报的 hosted connector/plugin 身份。

Runtime 的 ToolSearchHandlerCache 已使用不可变 handler 弱身份 / dynamic search-info 值
两种 key；MCPTool 显式声明 immutable_search_metadata，并隔离导出的嵌套 schema。索引在
prepare 发布搜索入口前构建，handler 冻结语料、不持有 registry。相同 MCP schema 换实例
仍重建；动态工具仅执行并发/结果预算变化时复用索引，但返回定义绑定当前执行参数。构建
失败不发布半套缓存；查询失败仍是 Observation，准备失败在模型采样前退出并允许后续重试。
持久化恢复可以重建索引，但不得重复已记录的搜索调用。默认来源 Include 已接入缓存；
兼容加载匹配仅排除 concurrency/output_char_budget：改变这些执行设置不要求重新搜索，
dispatch 使用当前 Step 的设置，历史结果内容保持原样。schema、名称、来源、输入类型和
曝光策略变化仍按兼容路径的身份约束处理；原生历史保留不等于绕过当前 router 的可调用
集合。这两种路径不能再用同一个“注册表变化就清空旧搜索结果”的规则处理。
第94–95批已接入namespace wire与DeferredToolWorldState增量目录/Omit策略；完整动态来源、
hosted provenance和catalog revision仍未对齐。

第96批接入server级enabled_tools/disabled_tools：可选字符串数组按原始remote name精确
匹配，不trim/改大小写，不接受canonical/provider alias作为替代。enabled缺省全允许，
空数组全禁用，disabled优先。每个暂存连接持有冻结集合；先过滤再生成MCPTool并参与全局
命名，过滤掉的碰撞项不影响剩余名称。Manager实际调用准入在remote调用/污染回调之前
再次检查；刷新失败不改变旧发布策略，重试成功应用新策略。已准入调用不追溯取消或重放。
配置经普通TOML与插件MCP声明同一路径加载；资源/prompts不受工具名单影响。这不是OS
权限、交互审批或完整Codex连接复用/动态配置事件机制。

第97批将MCPConnection变为独立策略/调用视图，MCPTransportOwner持有共享物理client和
初始化时未过滤catalog。request_mcp_reconcile经同一prepare/准入gate应用完整配置和
metadata；健康同identity连接不重启、不重列工具，新视图的filter/输出预算/timeout/污染
metadata不覆盖已准入旧调用。retire释放单个视图，最后视图在其调用结束后才关闭transport；
暂存回滚释放复用引用而不关闭当前发布连接。request_mcp_refresh与/mcp refresh仍强制重连。
force请求不会被后来的reconcile抹掉，取消/发布失败恢复已领取的force标记。
HTTP URL/headers或stdio command/args/cwd/env配置参与hashed identity；不打印凭据。
HTTP关闭状态、stdio退出或reader结束阻止复用。第98批将stdio环境改为默认名单与显式
env_vars引用；identity保留引用形式、顺序和非remote引用的父进程值，不再跟踪全量环境。
即使literal覆盖同名值，显式引用值改变仍失效；未引用的默认环境变化不自动重启。
超时按client身份+task上下文绑定，
HTTP request和wait_for都使用当前调用值，不修改共享client.settings；多个资源lease可共存。
配置更新不持久化或自动watch文件，尚未接入OAuth/remote环境/pending启动复用和完整catalog
revision授权路径。原始历史与已记录结果不改写，冷启动按调用方提供的当前设置重新建连接。

第159批普通MCP用户审批：`[mcp] approval_policy`只接受`never`与`on-request`；默认
never对应当前无OS沙箱host，与Codex Never+Disabled一样跳过所有工具模式，包括prompt，
不是“禁止执行”。OnRequest按server.default_tools_approval_mode及tools.<raw>.approval_mode
的auto/prompt/writes/approve决定是否询问；per-tool覆盖server，插件mapping走同一解析器。
Manager在最新binding准入后持有精确connection lease等待可信host，再标记污染和执行；
取消/关闭必须传播并清理等待。普通重连不撤回旧prepared权限，也不得重定向旧调用。
ElicitationRequest.kind区分server与tool_approval，server保留拒绝privileged metadata的
规则。Auto只记住本Runtime的raw server/tool，不按arguments分key；Prompt/Writes不能
记住。CLI额外remember布尔字段是宿主交互适配，原生meta.persist=session也可使用；
不声称复刻原生空form UI。拒绝/取消/host失败是MCP错误Observation，不触发远端或污染。
完整Guardian、managed权限/hook、永久配置amendment、hosted Apps原地catalog revision
写锁及旧request_user_input feature-off分支仍未完成；不可把这些新配置当成全局权限执行。

第160批MCP服务器准入：Runtime.create的独立host参数mcp_requirements在资源分配前
校验并冻结，Manager在每次启动/reconcile/forced refresh创建client前检查。工具过滤、
工具审批、remote metadata或用户server配置不能放宽此约束。全局空表禁用全部服务器，
非空全局表只约束普通server；插件规则使用loader保留的原始声明名与package身份，
任一package显式指定mcp_servers后才启动插件名单约束。发布失败保留旧generation并
恢复pending标记，下一次调用必须先重试准入；已进入审批的调用持有原connection lease。
本批只接通原生legacy精确command/URL身份：command故意不检查args，URL不归一化且
授权其余HTTP设置。typed规则现由mcp_matchers.py实现：executable完全一致、args数量及位置
逐项exact/prefix/regex；URL matcher仍授权完整HTTP配置。typed各层拒绝未知字段，legacy
字符串分支优先并保留忽略siblings语义。mcp_regex.py运行打包的regex-lite0.1.8 WASM，而非
Python re语法；先校验原pattern及完整值wrapper，每次匹配独立Store并关闭，无WASI/import。
Wasm host限制256MiB/1亿fuel，超限配置失败或匹配拒绝，不扩大权限。原生引擎没有这些host
ceiling，因此不声明任意大小输入完全等价；未改变普通legacy身份规则。构建源码/lock在
native/mcp_regex，Rust1.85.1+wasm32-unknown-unknown重建，运行时只需Wasmtime48.0.0和
随wheel携带的二进制/MIT notice。只隔离正则计算，不是工具sandbox。系统文件默认加载与
MCP层合并现见第170批；云端/MDM加载、实时更新、selected plugin和实际OS/reviewer权限
仍是未完成项，不可声称完成整体权限对齐。托管约束不持久化进业务历史。

第169批MCP声明目录：Runtime默认先按原始logical server name保留插件来源（按package名
排序），再加入config声明；不再靠package__前缀隐藏重名。PluginManager.mcp_servers仍是
旧manifest兼容投影，Runtime改用mcp_registrations。配置解析保留enabled=false并校验bool，
只启动最终enabled赢家。MCPCatalog记录声明、同tier冲突、Remove action及已materialize的
disabled name veto；Plugin<SelectedPlugin<Config<Compatibility<Extension，插件较早order胜，
extension较晚order胜，同priority按插入顺序。disabled selected-plugin不产生永久name veto，
其他disabled赢家会；disabled loser不是veto。controller约束在base构建前检查，随后host
overlay仍继承base veto。显式host compatibility/extension不是普通用户配置，不直接按全局
MCP名单过滤；此处对齐HostOnly目录路径，不代表完成远端environment authority或OS sandbox。
Runtime.mcp_catalog返回独立副本，request_mcp_catalog提交新来源；旧request_mcp_reconcile仅
materialize settings并保留已知来源。manager在同次无await发布中更换registry、连接、目录，
失败保留旧view和pending；已准入调用保留原lease，新调用先刷新。旧包前缀不自动alias，历史
不改写；调用者需使用raw server canonical name。selected-root自动发现、云端/MDM加载及更新、
环境选择/attachment权限仍是必需未完成项，不能用目录的source枚举声称这些链路已实现。

第170批托管MCP加载：默认CLI与Runtime.create先读Unix /etc/corki/requirements.toml；
Windows取OS ProgramData known folder/Corki/requirements.toml，查询失败告警并用
C:/ProgramData，绝不从ProgramData环境变量、CORKI_HOME或workspace推导系统authority。
只有NotFound视为缺省；读失败、UTF-8/TOML/结构/最终regex错误在目录、DB、插件、client
分配前失败。CLI在ensure_exists前捕获一次，交给Runtime，不二次读取；reconcile沿用快照。
managed_mcp.load_mcp_requirements的system_path是显式host绝对路径覆盖；layers是宿主提供的
MCPRequirementsLayer(source, contents)，系统层后按低到高优先级合并。每层先验shape，表
递归合并，scalar/array替换，高空表不清空低规则；合并后再编译有效regex。mcp_shapes是
共享wire结构校验，不产生未经编译的可执行policy。旧legacy sibling原文保留参与合并。
runtime.mcp_requirements_snapshot提供不可变policy与分字段、高到低去重source。显式旧
mcp_requirements mapping/typed policy或新snapshot都是完整host authority，替代默认加载；
不是用户配置选项或模型工具参数。直接使用底层Runtime构造器的宿主仍自行负责依赖与authority。
不支持的非MCP要求明确失败，不静默接受未执行权限。实际cloud获取、macOS preferences、
legacy全局权限转换、热更新和远端executor仍缺；不能把host fragments称为已支持云/MDM。

第171批保留MCP environment_id：缺省/null映射local，其余字符串不trim或alias，非字符串
在解析时失败。默认Runtime有隐式local绑定，未知环境由Manager在复用/创建client之前
作为单server启动失败隔离，其他server继续；底层本地stdio/HTTP构造器也在分配前拒绝。
transport identity包含环境名，unknown赢家不回退到同名低优先级local插件；已准入调用
保留原lease，新调用重新查当前绑定。普通非本地配置cwd不expanduser、不补controller默认
目录；插件来源路径规范化另有原生规则，尚未完整对齐，不把来源与environment_id混同。
冷恢复已有发现结果不授予调用权限：native原历史保留，compatible请求投影移除失效schema，
原业务历史不改写。系统MCP文件收紧后，同Thread新Turn和待执行checkpoint都重新准入，
不重复采样、已完成搜索或用户输入。此批是未知绑定拒绝的必要安全阶段，不是永久禁止
remote后宣称完成；实际executor绑定/transport、远端环境变量和attachment owner policy仍需实现。

第172批接入宿主显式MCPRuntimeContext/MCPHTTPEnvironment：create时验证typed context后才
分配Runtime资源；捕获不可变环境列表但保留具体binding/transport身份，不deepcopy载体。
HTTP client通过该实际AsyncBaseTransport发送请求和消费流，404新session继续使用原绑定。
环境owner requirements在catalog所有来源的winner解析前执行，controller policy仍独立。
request_mcp_runtime_context经prepare/call admission发布；同名不同对象重建连接，已准入
调用保留原lease。共享carrier由宿主在所有消费者结束后关闭，MCP仅关闭自己的session/response。
该批仅接入宿主能力；原生exec-server HTTP RPC在第173批接通，CLI executor discovery、
远端stdio和selected-root/attachment状态来源仍缺。

第173批ExecutorHttpTransport.connect显式连接宿主给定WebSocket，按executor独立RPC方言
握手，再通过http/request与bodyDelta转发真实MCP Runtime请求。reader在发送前注册response/
body路由，不等待满body队列；单流256帧、单帧1MiB、共享16MiB，溢出/断连保留失败终态。
早到分片、取消前后迟到response/delta、消费者drop、同连接多请求和共享字节释放独立处理。
executor_wire执行字节/节点/深度限制、重复key拒绝、严格ID/整数、canonical base64及header
边界；不改用MCP数字字符串ID别名规则。MCP初始化传播剩余毫秒deadline，普通请求不重置
远端总timeout，仍由现有MCP active-time控制。WebSocket连接本身不自动重连或重放请求。
bearer_token_env_var按真实executor capability选择远端valueEnvVar，移除已有Authorization，
不发送controller token；旧握手缺metadata时才懒读environment/info。cap=false才走controller
解析；畸形cap拒绝，不隐式降级。HTTP404新session保留原凭证来源。env_http_headers仍按普通
controller声明路径解析。websockets成为显式依赖。Noise/stdio连接、远端进程、透明executor
会话恢复、attachment状态与owner-policy-only刷新仍未完成；测试不冒充live executor验证。

第174批MCPHttpSession在真实HttpMCPClient统一处理POST、common GET、SSE恢复GET和DELETE。
底层每跳禁用自动redirect；外层按原始origin核验Location、10跳/共享deadline、方法/body转换、
proxy/referer规则和response拥有者关闭。helper位于单跳内部，401/403刷新不污染后续跳的
显式Authorization；HTTP helper proxy认证重定向仍拒绝。MCPCatalogSource新增宿主agent_plugin
标记，仅plugin/selected_plugin可携带；winner/materialize保留，连接复用比较该标记。普通插件
仍走legacy；agent plugin的configured/auth请求、discovery和2026-07-28协议请求停止重定向。
SSE GET的redirect/helper拒绝进入原有GET重连退避，不重放已接受POST；外层操作预算仍有效。
这不是完整modern MCP/Agent Plugin来源发现或权限隔离实现；实际测试和未完成项见审计。

第174批回归进一步稳定复现AnyIO TCP race交接与HTTPcore TLS upgrade的取消泄漏：真实socket
已连接但尚未交给pool，client.aclose后仍未关闭。http_connect只给本地默认/代理pool安装
OwnedConnectBackend，并在winning-stream交接与TLS upgrade所有异常路径回收原stream；
不全局patch第三方库，不修改宿主显式carrier。保留Happy Eyeballs地址顺序/竞速、HTTPcore
stream/TLS实现，AnyIO衍生逻辑随包附MIT许可。私有接缝集中在HTTP session构造hooks和
backend安装；依赖范围限定HTTPX0.28、HTTPcore1.0，AnyIO成为显式依赖。
历史第175批在chat/Responses、V2/legacy压缩、memory quota和history/notes真实HTTP消费者中（quota 消费者现已删除；其他专有路径待清理）
分别复现TCP交接与TLS握手取消后的socket泄漏，再将连接拥有者提取到顶层http_client/
http_connect。MCPHttpSession继承共享OwnedHTTPClient，其他消费者仅更换自建默认client；
不会继承MCP redirect/helper/session规则，也不修改外部注入的client/transport/mount。
AnyIO许可随模块移至包根。Runtime自建模型在普通/实时路径取消和关闭时，验证唯一取消
终态、持久化Turn结束及socket/reader回收；这不是所有网络故障或非协作宿主carrier的保证。

第98批local stdio环境构建先取Unix11/Windows23默认名和env_vars，然后加入非空继承CA
路径（基于父进程cwd转绝对路径，不resolve符号链接），最后应用literal env并过滤内部
身份变量。Windows覆盖及所有平台的CA literal覆盖按ASCII大小写不敏感去别名；普通Unix
变量保持大小写敏感。string与{name,source?}引用均经严格解析，未知字段/来源报错。
source=remote配置可解析但本地启动在spawn之前失败；Manager隔离该server，其他server
继续搜索/调用。HTTP显式env_vars连空数组也拒绝，null视为缺省。插件走同一配置和实际
启动路径。不修改os.environ，不代表OS沙箱；真实Windows/远端运行仍未验证/对齐。
CA边缘路径表示与原始配置Option表示的剩余差异见审计。

第99批POSIX stdio使用process_group=0，只保留刚创建的组ID；leader退出后仍清理该组。
MCPProcessGroup一次TERM，成功后独立两秒Timer发送KILL；组不存在不排期，TERM失败
记录警告且不升级。复用既有Darwin精确组成员fallback，不向父进程组发信号。启动交接
与close均有独立task；重复取消仍等待清理后传播CancelledError，清理错误保留在task供
后续close观察，不替换取消控制流。close等直接child最多三秒后kill，并回收reader与
所属subprocess transport的pipe。后台reader只持pipe和pending回复，不反向保活client；
weakref finalizer与显式close共享一次性组终止，真实GC测试验证最后引用释放的清理。
刷新退休仍等已准入调用完成，shutdown则取消调用；不重放历史工具。Windows Job/远端
清理、逃离组的子进程和宿主突然退出仍非已验证对齐范围。

第100批HTTP配置支持URL-only推断、http_headers/env_http_headers/bearer_token_env_var。
新字段严格校验并冻结，headers为旧别名，不能与http_headers同时声明；跨transport字段
连显式空表/空数组也拒绝。client首次start/request快照默认UA、literal/env头及bearer，
复用连接不重新读取父环境。无效header项安全警告并跳过，env空白按Rust White_Space
判定；无效或缺失bearer不回退到literal Authorization，而是隔离该server启动失败。
POST强制Accept/Content-Type、协议版本和session，bearer覆盖Authorization；DELETE
复用同一快照。HTTP identity包含新配置的None/empty区分及排序去重的引用父环境值。
改变未引用变量不重连；已有调用与最终DELETE保持旧认证，冷恢复不重放旧工具结果。
HTTPX LocalProtocolError可能包含认证值，因此转为安全MCPProtocolError且不自动重试。
真实loopback验证UTF-8 header字节；h11外缘空白限制与Codex HeaderValue仍有载体差异，
不将MockTransport成功当完整wire等价。OAuth/helper/remote/完整redirect仍待对齐。

第101批接入http_headers_helper（当前POSIX本地执行）：sh -c使用MCP默认环境与本地cwd，
stdin/stderr丢弃，stdout限64KiB，10秒超时，完成/取消/失败均立即清理自有进程组。
JSON必须是单个字符串表，重复及大小写别名、协议保留头、无效名称/值均安全报错。
每连接共享一次attempt及失败缓存；取消等待者不取消缓存任务，owner关闭/释放才取消并
join进程清理。返回的Headers副本不允许调用方修改缓存。认证拒绝刷新有cohort epoch，
失败/未变化也推进epoch但失败不替换旧凭据；并发拒绝共享一次refresh。仅同源POST
401/403可尝试刷新，Bearer insufficient_scope不刷新，有效helper值未变化不重发，
explicit Authorization遮蔽的变化不算变化；最多一次重发且保留body/id/session，
不重放网络错误或结果未知调用。helper应用前后共享请求deadline，DELETE不认证重试。
HTTP helper禁止跟随重定向，HTTP Proxy-Authorization重定向安全失败。HTTP close任务
独立拥有DELETE/helper/client清理，取消仍join；helper command和cwd参与reconcile身份。
原有第100批carrier空白、完整OAuth/redirect、Windows Job和remote差异仍未关闭。

第102批HTTP非2xx的有效JSON-RPC错误在符合条件时保留code/message，再经过既有ID匹配、
MCPTool错误值与Observation路径；session 404、认证challenge和生命周期瞬态status优先，
不新增自动重试。响应改为逐块收集，在追加前检查现有16MiB解码字节限额；正常完成、
超限、读失败或取消均退出所持HTTP响应。POST 202/204不读取body、不更新session。
HTTPX没有有界aread，适配器使用其同一_content缓存保存已解码正文，避免gzip二次解码；
依赖版本变化需保留gzip与真实carrier回归。该限额不是Codex所有legacy请求的数值合同，
也不限制解码器单次分配。完整SSE/GET及握手/会话恢复仍开放。
第103批响应stream独立拥有一次close task，覆盖HTTPX在EOF自动关闭及提前设置is_closed
的路径；重复取消只取消等待者，仍join真实清理，再传播取消。清理同时失败不能覆盖原始
取消/读错误，只记录异常类型；无原始错误时关闭失败仍失败，不报告成功。真实Runtime
在清理完成前不发布终态，取消后冷恢复不重放工具。该路径不强制终止不协作的自定义
transport，也不声称已经处理HTTP headers交接前的全部依赖内部取消窗口。

第104批tools/call的HTTP401 challenge在helper处理后变为Authentication required错误值，
合并全部WWW-Authenticate值进入私有_meta，不再次重发；读取到challenge headers后不等body。
initialize/list、bare401和403不混为同一结果。ToolResult与ToolCallCompleted新增可选
mcp_result_json/mcp_error：前者是不可变JSON宿主副本，后者是传输失败，两者互斥。
MCP事件结果序列化<=1MiB时保留完整content/structuredContent/isError/_meta，超过时
整段序列化文本按源码头尾预览，移除structured/_meta并保留isError；最终JSON转义可更大。
输入沿用32MB有界JSON校验，事件最终字节另设转义上限。executor验证后经ledger到宿主
完成事件；新字段缺省不写入旧账本，读取旧行保留None，旧记录不回写。ToolResultItem不带
这两个字段，Code Mode/普通日志仍排除顶层_meta；元数据不能改变权限或触发自动登录。
真实Runtime原生/兼容检索、Code Mode、事件、账本与冷恢复验证，不代表OAuth交互或404恢复。

第105批HTTP由独立HttpRecovery持有generation和操作任务。初始化/initialized通知的
瞬态传输及408/429/500/502/503/504最多三次，250ms/1s退避共享启动deadline，每次重建
会话和helper。普通操作仅tools/list做瞬态重试；tools/call/resources/prompts不重放
结果未知失败。只有实际协商session的POST404才按旧generation身份串行恢复、完整握手
后发布并重试原操作一次；第二个404直接回传，未来独立调用可正常继续。客户端内部记录
发送时的session权威，不能由literal同名header或后来变化的session冒充。恢复保留原始
静态认证/cwd快照，工具timeout按原调用视图绑定，恢复本身使用保存的startup timeout。
旧generation等已准入调用退出才清理，不重新列catalog；新握手失败/取消不替换旧会话。
关闭立即拒绝准入、取消并join已拥有操作，禁止晚到发布；response cleanup继续遵守第103批。
注入的HTTP carrier由外层唯一关闭，各会话仅借用，不随一次退休提前关闭。普通默认HTTP
client每代独立池。真实loopback/Runtime检索与Code Mode、账本、冷历史和恢复中取消验证；
完整OAuth/scope、现代协议、SSE/GET、远端和其他Harness缺口继续见审计。

第106批按Cargo.lock校验下载的rmcp3.2.0/sse-stream0.2.5源码继续追踪：请求级SSE
改为逐块解析完整事件，不等服务器EOF；HTTP JSON/非2xx仍用原有有界body路径。CR/LF/
CRLF、UTF-8跨块/BOM、multiline data、仅去冒号后一个空格、重复/未知字段等按锁定
解析器处理；不把未结束的最后事件、control事件或同ID服务器请求当成工具完成。
普通JSON损坏事件跳过，SSE字段/UTF-8错误失败。16MiB改为未完成事件/行的有界状态，
不累计已完成heartbeat/comment；此数值仍是Corki与Codex legacy adapter的已声明差异。
POST成功后的SSE读流失败变成协议失败，不误走第105批send重试；取消/提前返回仍join
response close。原生/兼容/Code Mode的真实Runtime检索、Observation、账本和冷恢复验证。
此前严格整数ID的结论被依赖源码纠正：HTTP/stdio接受Rust i64规则的数字字符串fallback，
不接受bool/float/空白/Unicode数字，原业务tool call id与账本不改写。GET续传、常驻
通知流、服务器请求处理及50ms后台连接复用drain在当批尚未实现；GET续传现见第107批，
不声称完整SSE对齐。

第107批请求级SSE使用独立sse_resume状态管理，普通请求收到完整事件ID后可以GET续读，
无协商session也适用；冻结原始URI/认证/协议/session/RPC身份，只更新已完成事件的cursor。
空ID按锁定rmcp的Some("")处理；未完成事件ID不发布；控制/坏JSON帧也可更新ID/retry。
EOF消耗服务器retry或默认1秒；带cursor的读流/字段错误立即GET，超限永不续传。
GET失败默认无限指数退避，但始终受原调用deadline/取消控制；GET404不能误入POST
会话重建，GET关闭异常也不能成为初始化/tools-list发送重试。旧response先join关闭，
再等待或建立新carrier；独立http_stream模块复用原有重复取消/首错保留的owned close。
initialize按源码expect_initialized分支不启用此wrapper，其完整独立payload验证待继续核齐。
真实Runtime原生/兼容检索及Code Mode续传、超时Observation、账本与冷历史，以及真实
HTTP/1 chunked POST→GET验证；共享通知流、入站服务器请求、动态认证刷新和50ms连接
复用drain仍开放。未新增持久化字段，恢复旧历史不重新连接/重放已完成操作。

第108批加入initialization.py，对默认legacy握手统一验证JSON-RPC envelope和
InitializeResult必填protocolVersion/capabilities/serverInfo，递归验证已知capabilities、
Implementation及Icon字段。未知字段忽略、optional null允许；字符串按锁定rmcp类型规则
允许空值/未知版本，不额外发明版本白名单。无效结果在initialized通知和工具目录发布前
失败；会话404恢复的替代握手同样验证，不能发布无效generation或重发原调用。
HTTP initialize SSE不再套普通响应筛选：忽略event类型，跳过空data/非Response消息，
非空坏JSON立即失败，首个Response交给握手层校验ID/result；不GET续读。
initialized通知接受202/204或有效JSON-RPC JSON，拒绝SSE及未类型化body，关闭仍受owned
response保护。stdio真实LocalStdioTransport的legacy AsyncRw路径则跳过语法坏JSON/BOM，
对typed envelope错误发Invalid Request后继续等；首个response/error交握手层，不静默等过
冲突ID。reader持有初始化future/写管道及锁，不持有client引用，原有GC/进程关闭所有权不变。
成功fixture补齐真实必填字段；实际Runtime验证坏server隔离、健康server检索/调用/
Observation/账本/冷恢复。仍不代表所有普通消息serde、日志/服务器请求分发或现代discovery。

第109批HTTP generation新增共享pending response路由和初始化成功后的公共GET接收任务。
202/204只表示POST发送被接受，RPC等待GET或其他POST中的对应ID；未知/重复/已取消ID
不误配到当前调用。公共流收到结果后继续接收，无cursor也可续GET；请求级流仍要求cursor，
首个Response关闭该请求流并按ID路由，不能一直跳过其他请求的响应。初始GET405视为不支持，
其他初始GET失败隔离，不进入POST重试或会话恢复。请求deadline/取消与公共接收器寿命分开，
后台发送与接收由generation持有，关闭join后才DELETE，新旧generation同数字ID不串结果。
真实Runtime原生/兼容搜索、Code Mode的结果/错误/超时Observation、账本和冷历史均覆盖。
此批仅响应路由，服务端请求/通知处理、完整serde、精确队列backpressure/50ms drain及
会话恢复barrier仍开放；不新增数据库或checkpoint字段，不声称完整协议等价。

第110批InboundService接HTTP JSON、POST SSE、公共GET和stdio的method消息分发。
默认ping返回空对象、roots/list返回空roots、未实现sampling/custom返回MethodNotFound；
精确保留服务端请求ID，不与同数字的出站RPC混淆。HTTP回复走原generation控制POST，不进入
会话恢复；stdio reader仅持有pending、pipe和锁，不持有client强引用。回复任务归transport
所有，关闭先停止/等待回复，再关闭管道或DELETE；完整Codex EOF/shutdown drain仍待核齐。
远端cancelled通知按精确RequestId查找，非法类型不强制转换；MCPRemoteCancelled继承
MCPProtocolError，经真实MCPTool错误值边界进入Observation，不等同asyncio取消整个Turn。
进度/resource/list/log通知按已知字段类型和severity记录日志，不自动重列/发布catalog，
不把私有_meta注入模型。真实stdio及HTTP Runtime的原生/兼容搜索、Code Mode、账本和冷历史
覆盖反向ping与远端取消。elicitation权限/用户输入/暂停timeout、订阅适用性、全部serde和
精确backpressure仍开放；未新增数据库字段、capability声明或自动批准外部请求。
源码复核纠正：Codex rmcp_client.rs初始化成功后显式关闭RMCP response cache及stale-on-error，
不能将上游库默认缓存当成当前Codex路径的待补功能。EOF与主动关闭需分别追踪：库级5s/2s
排空不是Codex所有关闭路径的统一保证，Codex主动关闭stdio先终止进程再drop service。

第111批修复自然stdio EOF的入站回复丢失：停止新请求准入，已排队/发送中的回复最多排空5s，
随后关闭stdin写端，等待子进程3s后必要时kill/reap。legacy I/O读取错误与EOF同类；内部
异常和显式取消不套用自然EOF grace。InboundService拥有唯一close任务；显式host close可
中断grace，重复取消不重复打断reply cleanup，reader待处理RPC在清理完成后收到关闭错误。
EOF收尾仅持有process，不保活client；HTTP SSE EOF仍走重连，不触发此stdio收尾。
stdio排空期间关闭普通出站call/notify准入，锁内再次检查关闭状态；反向回复独立写路径保留。
实际子进程在收到ping回复和stdin EOF后才退出，原生/兼容搜索及Code Mode Runtime验证
此前工具结果仍成功回灌、账本一次写入、冷恢复不重放。完整HTTP关闭/调度和其他A–E仍开放。

第112批普通MCP请求在params._meta注入独立progressToken；初始化和通知不分配token。
计数器归连接generation所有，按锁定rmcp的AtomicU64/signed i64语义编码；不复用JSON-RPC
request ID。生成token覆盖调用方同名meta键，保留其他metadata及arguments并深拷贝输入。
同连接新RPC重试取新token，404换代从新序列开始，SSE GET仅续原请求不消耗token。
错误_meta类型在发送前变为MCPProtocolError；不修改默认超时、重试、model schema或账本参数。
真实HTTP/stdio Runtime验证进度通知、Observation、一次ledger完成与冷恢复，私有通知meta
不进入模型/日志。普通legacy调用没有开启SDK progress idle-timeout reset；elicitation暂停
预算是独立未完成路径，不能将两者混淆。

进程内 prepare 通过 ToolRegistry.snapshot 同时捕获 handler 与 spec；采样 retry 保留同一个
快照，仅从更新后的 durable history 重建请求，不刷新 handler 或重置嵌套执行门。GraphRunContext
的 StepToolState 持有单代绑定，live/普通工具执行使用该快照，不再从最新 registry 按名字
重选对象。checkpoint 仅保存 tool_snapshot_id，旧 checkpoint 缺失该字段也可恢复；冷恢复
只能取得当前实例，并通过保存的 dispatch spec 校验和 ledger 避免误执行/重复副作用。
强制刷新搜索入口也不会改变已准备 Step 的搜索 handler。Code Mode 新 cell 的工具表和
本 Step nested dispatch 共用快照；exec/wait 由受控 owner 发布，下一 Step 更新 exec 中的
直接工具定义。跨 Step 旧 cell 的未来调用仍由当前 worker 分发，不把创建时对象永久绑定
给整个 cell。MCP 的当前连接准入策略保持不变。此快照不是 OS 权限或动态撤权机制。
旧 cell 仅保留原始名称与 payload kind；其未来调用在当前 worker 准入时捕获执行 spec，
并发、schema 校验与结果预算不再使用旧 cell 的定义，已准入且等待 gate 的调用不改绑。
Hidden/ModelOnly 过滤新 cell 的目录，不撤销旧 cell 已持有的名称；Hidden 强制独占。
这与 Codex registry 的保留分发语义一致，实际撤销需移除注册，不应把 exposure 当授权。
嵌套分发异常（包括 FatalToolError）现在先持久化带 dispatch_error 标记的失败结果，再
以字符串拒绝 JS Promise；handler 返回的错误 ToolResult 仍正常解析为工具值，不能混为
一种错误文本。顶层 Fatal 仍终止 Turn，取消不被吞掉；账本/桥接故障不降级成正常工具
结果。旧 ledger 缺少标记时保持原语义，不改写旧结果；新标记只在执行账本使用。
worker 覆盖采样及完整响应后的独立 tools drain；进入下一次 prepare 前暂停新准入并释放
dispatch/notifier 引用。旧 cell 的新调用/notify等待下一worker，已准入调用继续使用原
closure/spec及所属Turn事件生命周期。Event唤醒后再次pause会重新等待；cold checkpoint
从tools节点恢复时可重建worker，无需重采样。完整取消/压缩/重试组合仍见审计，不代表
Code Mode或上下文管理已整体完成。

### 18.3 Plugins

扫描项目 `.corki/plugins`、`~/.corki/plugins` 和 `[plugins].directories`。原生支持
`.codex-plugin/plugin.json`，也支持 `.corki-plugin/plugin.toml`。插件可提供 skills、内联或
`.mcp.json` MCP server，以及 Corki 扩展字段 `entrypoint = "plugin.py:register"`。Python 插件
是显式信任的本地代码，工具强制命名为 `plugin__<plugin>__<tool>`；加载失败必须回滚其已注册
工具、移除临时 Python module 并记录 warning，不得破坏其他插件。每次加载使用独立 module key，
两个嵌入式 Corki runtime 关闭插件时不能互相卸载；`aclose()` 必须幂等。

第176批将普通宿主MCP声明归一化提取为plugins/mcp.py：包装/裸server map统一解析，坏server
与坏可选MCP文件分别隔离，PluginManifest携带诊断供宿主和原有插件上下文发布；有效技能/工具
不因MCP错误被丢弃。缺省cwd留给Runtime，显式相对cwd在共享settings解析前以插件root作
词法拼接，避免错误的进程cwd或shell展开。HTTP helper不再被强制放到插件目录运行。
mcpServers文件路径遵循原生./前缀/禁止父路径约束，无效声明回退默认文件，缺失可选文件
不报错；默认路径先检查is_file，不打开目录/FIFO。普通插件允许文件symlink，不能复用到
Agent Plugin的regular-file和canonical containment检查。type注解只做提示，普通配置解析
仍保持自身严格校验。此批不代表Agent Plugin、executor来源、OAuth或所有来源策略完成。

第177批实现将目录加载的Agent Plugin来源独立处理：manifest_path保留发现时的root/format，
不靠包目录名猜测；root plugin.json按schema优先选择，不支持的版本及symlink/nonfile不降级。
agent_manifest验证核心元数据并固定skills/mcp.json，agent_mcp执行严格字段、HTTP头及可选文件
隔离；agent_paths先解析现存symlink再检查command/cwd，args/env占位符只替换一次。
目录加载使用root/.plugin-data且不主动mkdir。agent_overlay只向既有stdio追加本地env_vars，
移除同名同引用的占位符以允许继承，但不替换literal、不增加服务或HTTP凭据。
typed MCP envelope及legacy overlay拒绝重复字段；根清单和单个server的Value对象保留后值。
来源身份由PluginManager传入Runtime catalog，驱动已有认证重定向策略。普通manifest还支持
.claude-plugin和.cursor-plugin的原生fallback顺序。Agent URL及重定向使用固定url 2.5.8解析器；
legacy覆盖配置保留数字的原生分支选择，hook元数据准入校验不等于执行hook或授予权限。
本批目录来源/MCP加载范围已通过源码6687项和隔离安装6387项全量验证；普通MCP URL方言、
Windows实际执行及完整插件能力仍分别保留审计边界，不代表A–E整体已验收。

第178批正在接入显式本地执行权限。`[execution]` 指定宿主编译器与原生权限对象，
普通/Code Mode 共用不可变配置和 policy cwd；shell/PTY、patch、image 通过实际OS策略执行。
编译失败不回退无约束执行，编译期间取消后会在命令准入前重验。构建和显式配置方法见
`native/sandbox/README.md`。40个新增场景和233项隔离安装相关回归已通过；随后全量回归报告
6727项通过。本次后台权限继续变更后需重新验收，不能复用此结果证明新快照已通过。
当前没有配置时仍是旧宿主执行路径；默认/命名权限、托管约束、审批编排、持久化/恢复、
记忆工作者派生和Linux/Windows后端尚未完成，不以这一增量宣布权限或核心Harness对齐。

第179批已接入记忆后台pass独立父权限捕获，以及Disabled/External/Managed三分支工作者
派生；Managed的实际命令只写私有记忆工作区且禁网，不继承父工作区或默认临时目录写权限。
策略失败先于工作者采样，记录failed_sandbox_policy并释放claim；取消仍是独立控制流。
该增量的托管配置约束、持久化/恢复、配置默认选择及跨平台部分仍未完成，详见权限审计。

第180批接入 `allowed_sandbox_modes` 与独立托管 `permissions.filesystem.deny_read`：
系统文件/宿主fragment按来源冻结，原生codex-config解析每层相对路径并组合拒读并集。
Runtime在Thread创建、MCP启动及模型采样前解析有效权限，模式按实际文件权限分类；
不允许的workspace等配置可回退只读并提示，FullAccess在当前无审批升级能力时不能回退。
工具执行严格重验，记忆工作者使用同一托管快照，不重新读取后来变化的系统配置；
子任务仅继承独立托管拒读，不把所有父用户deny都当成托管规则。其他托管域仍明确拒绝。
该批次不是默认/命名profile、权限持久化/恢复、审批编排或跨平台完成声明；
记忆输入准备与配置构造的完整原生顺序仍有差异。验证终态记录在harness-alignment审计中。

第181批将用户default_permissions与命名permissions表接入真实Runtime，直接编译固定
Codex源码中的命名profile解析模块；[execution]只指定compiler时走unknown-trust只读
builtin。原始execution.profile形式仍兼容，两种配置形式互斥；不支持的proxy配置明确
失败。实际权限、选中profile身份及profile roots在采样前一起发布，受托管模式约束回退时
清空身份/roots，memory子策略不携带父命名身份。Thread模型设置更新和启动重试保留已解析值。
当前这些值仅为运行时快照，不是持久化恢复完成；project trust、完整配置层选择及无
编译器时的原生默认行为仍需继续实现。

第182批接入独立托管命名目录、allowlist和default；用户/托管双向继承，跨来源同名
明确拒绝。名称不允许时先选托管默认再编译；具体模式不允许时回退只读并清空身份。
配置准入保留原始用户目录以供重试；memory的具体子策略携带目录用于校验，但不重新
选择父profile。宿主重新准入可传入当前约束；这不是自动配置重载或持久化恢复。
编译器新增managed_catalog能力确认，旧后端不得静默忽略目录规则。其他proxy和审批
约束仍未接入。验证范围、剩余差异及最终门状态见权限审计，不作为整体Harness完成声明。

第183批把phase2子配置准入提前到DB输入选择、同步和no-change判断之前；配置无效时
即使输入没变也按failed_sandbox_policy失败，而非成功skip。PreparedAgent在同一
claim/heartbeat拥有期内保留临时工作目录和完整child settings，run_agent直接复用；
直接调用run_agent也先建立配置再复制输入。skip和输入处理失败释放准备目录，关闭
未确认的Runtime把清理权留给ConsolidationShutdowns，不能被外层finally提前删除。
这不意味着私有副本已经变成Codex共享memory_root，也不代表完整权限/恢复对齐。

第184批主合并流程改为实际共享memory_root作为工具cwd和权限派生根；临时目录只保存
worker运行资源，清理不删除memory_root。已完成的工具写入即时可见，失败不回滚普通
文件，但不推进基线/DB成功。确认关闭后验证实际文件并清除symlink（不跟随目标）；
owner fence只控制基线/状态提交，不再复制发布文件编辑产物，也不再次删除外部重建的
摘要。文件编辑路径不再额外改写model写出的字节或以输入快照差异拒绝整个任务。
独立run_agent与最终JSON保留明确兼容路径；原生Git基线/完整配置与恢复仍待对齐。

第185批主链使用真实内部Git HEAD/index基线，宿主需提供Git（本机验证2.39.5）；
无需原生权限编译器也走同一基线路径，Git不可用时后台失败，不回退为虚假成功。
准备阶段在child config/输入选择之前建基线；隐藏文件、二进制和执行位纳入，FIFO
等非Git文件类型忽略。启动清链接、完成后清链接并判失败；均不跟随目标。diff不写
对象，每次成功重建无父提交基线并清除旧对象和生成的diff文件，不留副本。v3 JSON
迁移保留旧内容证据，未知旧版本强制正常合并。只重建拥有标记的内部.git或已识别的
原生单提交基线；误配用户项目仓库会拒绝，不能删用户历史。JSON兼容输出也记录当前
共享树，不再拼接采样前输入和完成后输出。Git仓库边界同步作用于项目规则/仓库技能。
正文diff仍使用Python renderer而非Rust similar，同名hunk分组不保证字节一致；全局
配置、恢复及A–E整体仍未完成。回归和打包终态以harness-alignment/audit.md为准。

### 18.4 Realtime

`[realtime].enabled = true` 或会话内 `/realtime on` 会在模型输出期间保持 composer 可用。
新文本进入有界 `RealtimeController`，当前模型 iterator 被关闭，该文本作为同一 Turn 的新
`UserMessageItem` 持久化，然后 LangGraph 回到 prepare 节点重建快照并重新采样。崩溃恢复时
不会恢复已经失去前端连接的 active 标志，避免 checkpoint 永久等待。`Ctrl+C` 通过同一通道
发出停止请求。这里的 realtime 特指文本 steering；音频采集、播放和 Realtime API 会话属于
独立 provider/UI 能力。

## 19. Codex 对标验收矩阵

这张表列出已有实现入口，不是所有核心语义与失败边界一致的证据。
当前对齐结论、适用路径和未关闭差异以 [源码审计](harness-alignment/audit.md) 为准。

| 领域 | Corki 当前契约 | 对应实现与验收 |
| --- | --- | --- |
| Planning & Reasoning | 可见 plan 与 provider reasoning 分离；plan 最多一个 active；reasoning 可流式、持久化和按 capability replay | `planning/`、`update_plan`、两个 provider adapter；core demo 与 model adapter tests |
| Memory | 同 Thread 连续历史与 compaction；跨 Thread 两阶段抽取/合并；lease/watermark；分层 Markdown 渐进召回；结构化引用与 usage 排序 | `memory/`、`prompts/memory/`、`protocol/memory.py`；repository/pipeline/backend/integration tests |
| Tool Use | schema 与 handler 统一注册；每个 step 冻结 advertised specs；exclusive/parallel 调度；结果预算与幂等 ledger | `tools/`、`core/graph.py`；builtin tools demo、tool loop、parallel/recovery tests |
| Context Engineering | base instructions、mode、层级 AGENTS、environment、Git 与 extensions 分层；context 先于当前 user；保留历史并追加替换/失效消息，比较基线限于当前窗口 | `prompting/`、`context/world_state.py`、`prompts/`；Runtime/HTTP/冷恢复与压缩 tests；section专属diff与来源冻结仍见审计 |
| State Management | Thread/Turn/Step 分层；显式 LangGraph edges；业务 SQLite 与 graph checkpoint 分离；resume 不重复采样或副作用 | `core/`、`sessions/`、`storage/`；checkpoint/fault-injection tests |
| Evaluation & Guardrails | step/tool 上限、输出完整性、跨 Turn item、重复 call id、JSON/schema 与空响应验证 | `evaluation/`、provider adapters、`ToolExecutor`；guards/transport/malformed-provider tests |
| Extensions | project/user/system/plugin skills；Codex manifest；stdio/HTTP MCP 的 tools/resources/templates/prompts；文本 steering | `skills/`、`plugins/`、`mcp/`、`realtime/`；extensions demo 与各包 tests |

持续对标时优先验证下面这些“不变量”，不要只比较类名或目录：

1. 一次采样的 advertised / dispatch tools 来自同一个冻结注册视图。原生 deferred 工具定义通过
   搜索输出历史提供，不要求出现在顶层 tools；兼容路径才把已检索工具追加到顶层列表。
   普通 registry 只允许在 composition/startup 阶段变化；运行中 MCP/搜索入口经受控 owner
   原子发布。MCP 调用准入解析最新连接，执行中不重绑；完整动态路径仍见审计的 B05。
2. world-state/context diff 先于它所限定的用户输入；压缩不得吞掉当前输入或制造 history gap。
3. model output、tool call、tool result 都是 canonical history，而不是只能在终端看到的临时文本。
4. model step 和 tool side effect 在 checkpoint 边界上都可幂等恢复；未知副作用不能自动重放。
5. 可选扩展单点失败要降级为 warning/tool error，不能破坏无关的核心工具或整个 CLI。

与 Codex 仍存在的实现差异：Corki 使用支持 Unicode 与图片的 provider-neutral token
estimator，没有 Codex 的逐模型 tokenizer 与远程 compaction；Phase 2 已接入独立多 Step Runtime，
但完整内部线程管理与权限配置尚未对齐；模型/工具 capability plan 比 Codex 的动态 hosted tool selection 简化；Code Mode
已部分接入，shell/MCP 结构化结果及有序媒体/helper 已支持，其余工具 typed return、媒体预处理、V8 host 与全部边界
仍待对齐；hosted web search、语音 Realtime
API、审批/sandbox 和多 Agent 尚未进入本阶段。新增其中任何能力时，应扩展 `ModelPort`、`ContextContributor`、
`ToolRegistry` 或 runtime event，不得在 CLI 或 graph 中建立第二条旁路循环。

### 19.1 可执行验收场景

- `examples/core_loop_demo.py`：证明 plan、项目指令、shell、patch、验证、最终响应与跨 Turn memory。
- `examples/builtin_tools_demo.py`：逐个调用全部默认工具，证明长进程 `write_stdin` 和图片 attachment
  能回到下一次模型请求。
- `examples/extensions_demo.py`：动态创建并召回 project skill、Codex-format plugin，并启动真实
  JSON-RPC stdio MCP server 走完整模型/工具闭环。

examples 不是替代测试的脚本。它们的 stdout 是稳定 JSON，`tests/e2e/test_examples.py` 会逐个
作为独立进程执行；任何 demo 发现的 bug 还必须在更小的 unit/integration 层留下回归测试。
