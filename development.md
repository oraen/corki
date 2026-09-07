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

- 命令审批、危险操作判断和用户确认；
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
- freeform 原始输入契约：Responses custom call/output，兼容 JSON input wrapper，
  搜索加载后的定义类型、真实工具调度及持久化回放；不等于 Code Mode 执行环境；
- 独立的 thinking/reasoning 流式事件、UI 展示、SQLite 持久化和工具回合原样回传；
- `exec_command`、`write_stdin`、`apply_patch`、`update_plan`、`view_image`；
- 类型化 assistant/tool/plan/turn runtime events，CLI 实时消费 token delta；
- provider-neutral `ConversationItem` 协议和逐 item SQLite 存储；
- context/world-state 变更与本轮 user item 按确定顺序原子写入，当前输入永不参与 pre-step compaction；
- 每次模型采样冻结 advertised / dispatch 两份工具计划；兼容 provider 只加载已检索的 deferred
  定义，原生 Responses 则通过 tool_search_output 历史提供定义；
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
续接受 `max_steps` 限制。finalize 在检查本次请求未见的已持久化输入后，原子检查队列并关闭
接收；存在新输入时返回 prepare。关闭后的 steer 抛出专门错误，CLI 保留为下一 Turn 输入。
输入 dequeue 不等于已记录；稳定 item id + 写入后 ack 支持失败/取消收尾的幂等补写。
普通和 realtime 模式的 cancel_active 现在都取消 Runtime 持有的 TurnRun。
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
排序，上游HashSet同分顺序不确定；分词normalization/stemming/stopwords与u32 hash身份仍待
下一阶段对齐，不得由评分测试通过推断完整排名一致。

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
上下文；若预计超阈值，只压缩此前已经存在且未受保护的历史。Turn 开始前压缩的模型可见顺序是
`保留的旧 user -> compaction -> 当前 context -> 当前 user input`；工具回合中的 mid-turn 压缩会把
当前 context 放在最近一条保留 user 之前，并把 compaction summary 放在最后，与 Codex 的注入语义
一致。存储仍保持 append-only，因此 checkpoint marker 会先物理落库，再通过 marker 中的 replacement
长度和 summary index 重建上述模型顺序。当前用户消息必须以原文保留，不能先交给摘要模型改写。
没有触发压缩时，context diff 与 pending user item 在同一次 repository append 中提交，保证崩溃
恢复仍保持顺序。

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

项目指令遵循 Codex 的层级策略：从项目根到当前 cwd 逐级查找 `AGENTS.override.md`/`AGENTS.md`，
同目录优先 override，越接近当前目录的指令越具体。不越过 Git/project root，并按 root 到 cwd
的顺序分配总字节预算；无效 UTF-8 使用 replacement character 保留其余有效指令。

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

后续同一 Thread 只追加发生变化的上下文，但不从模型历史删除旧版本。AGENTS 更新/删除
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
窗口。工具执行先 claim ledger：完成后可复用；若进程死于外部副作用之后、结果提交之前，状态
记为 `interrupted` 并返回“结果未知”，不会擅自重复副作用。

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
connection counter 持久化，崩溃后不会重新从 5s 开始。关闭开关或显式 provider_name 为
bedrock/amazon_bedrock 时有界；没有原生 Bedrock adapter，不以名称分支冒充平台支持。
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
- `[memories]` 默认关闭；`enabled` 是总 feature gate，`generate` 和 `use` 分离，
  `dedicated_tools` 单独控制 scoped memory tools；
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
路由，再对 Markdown 做关键词搜索和按需读取。Corki 当前 Phase 2 直接使用隔离的 `ModelPort`
请求；Codex 使用内部 consolidation sub-agent。等 Corki 开启多 Agent 后，可替换 orchestration，
但不能据此认定 repository、artifact 或 read path 契约已经一致；完整差异以源码审计为准。

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
启动quota、远程模型目录和完整Phase 2策略仍是开放对齐项。

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
失败retry_at独立于status检查，不因重复启动变pending而失效；run_once不再根据文件/
stage1是否存在而无条件enqueue。真实提取输入变化或显式enqueue才推进单调水位，并在
非running时清除退避；保留已有finished_at/error，仍遵守成功冷却。Phase2失败计数会
递减到0但不禁止后续到期领取，与Stage1的3次硬上限不同。finished_at新增列不回填
旧updated_at，未知历史成功时间允许正常工作区检查。兼容参数enqueue(force=...)保留
调用形状，但force不再作为跳过空库或绕过冷却的开关。空库缺少产物也会进行一次隔离
模型合并初始化；失败仍不影响主Turn。完整原生subagent/git diff内容协议仍未声称完成。

## 14. 测试策略

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
`tools/list`，并注册为 `mcp__<server>__<tool>`。每个 server 独立失败；工具结果和异常仍经过
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
namespace wire、DeferredToolWorldState 的增量来源/Omit 仍待对齐。

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
estimator，没有 Codex 的逐模型 tokenizer 与远程 compaction；Phase 2 目前用隔离模型请求而非
内部 sub-agent；模型/工具 capability plan 比 Codex 的动态 hosted tool selection 简化；Code Mode
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
