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
2. **Thread、Turn、Step 分层。** Thread 是长期对话；Turn 是一次用户任务；Step 是 Turn
   中的一次模型采样。三者不能混成一个状态对象。
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

当前边界也必须说清楚：审批/sandbox、多 Agent、Code Mode，以及 Codex 的语音/音频 realtime
会话尚未实现。当前 realtime 是 coding harness 的双向文本 steering，不应表述为语音能力。

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
非 realtime 主动取消、活跃 runtime.aclose 与更多异常关闭路径仍见对齐审计的未关闭项。

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

流式传输必须看到协议终止标记，否则整个 step 失败。只允许在尚未向 UI 发送正文或 reasoning
delta 前重试；一旦用户看到了部分输出，自动重试可能造成重复，因此必须失败退出。
一旦收到权威终止事件，应立即结束读取，不能依赖 HTTP peer 主动断开；所有 provider payload
在进入 canonical item 前必须完成结构、usage 非负整数、item/call id 唯一性和响应大小校验。

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
5. `view_image`：读取 PNG/JPEG/GIF/WebP，通过 attachment 进入多模态消息。

虽然暂不做安全审批，但必须做到：

- 使用参数数组或明确的 shell 语义，不能暗中改变执行方式；
- 每个命令有超时；
- 捕获 stdout/stderr 和 exit code；
- 输出有模型预算；
- 取消 Turn 时终止对应子进程；
- 工具异常转成 `ToolResult`，不能让整个 CLI 崩溃；
- 所有文件路径相对 Turn 的 cwd 解析；
- tool call id 写入 durable ledger；完成结果复用，崩溃时结果未知的调用绝不自动重放；
- `PARALLEL` 只用于显式声明可并行的工具，shell、patch 和 plan 默认串行；
- `tool_search` 查询 deferred 元数据，完整定义保存在工具结果及执行账本；兼容路径从活动历史
  重建 loaded schemas，原生路径保持 schemas 在搜索输出历史中；压缩后重新检索，禁止复用失效定义；
- `exec_command(tty=true)` 使用真实 PTY，取消和 runtime 关闭时回收进程组。
- handler 必须返回当前 call 对应的 `ToolResult`；身份不匹配或返回类型错误统一转成失败结果。

后续工具若声明 `PARALLEL`，必须用重叠执行与取消测试证明互不冲突。

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

后续同一 Thread 只追加发生变化的上下文；同 key 仅最新值对模型可见，删除来源写 tombstone。
Plan 模式以 `modes/plan.md` 替换 default；`tasks/compact.md` 只用于上下文压缩。

### 10.2 Prompt 扩展位

`PromptAssembler` 不识别具体业务能力，只接收各模块提供的 `PromptContribution`。Contribution
包含稳定 key、role、slot、slot 内顺序、模板名、变量以及是否需要独立消息。当前预留顺序为：

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
  warning 并保留上一版发布文件，不能拖垮交互 Turn；
- `LangGraphRuntime`：管理创建、完成、失败和取消，不写 SQL。

模型 step 的 usage、metadata 和 items 在一个事务内提交，并以
`(thread_id, turn_id, step_index)` 幂等读取，消除业务提交与 graph checkpoint 之间的重复采样
窗口。工具执行先 claim ledger：完成后可复用；若进程死于外部副作用之后、结果提交之前，状态
记为 `interrupted` 并返回“结果未知”，不会擅自重复副作用。

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
- retry 分类、Retry-After、流完整性和 response 硬上限；
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

### M7：Codex 风格跨 Thread 长期记忆（已完成）

- Phase 1 对已完成且空闲的 Thread 做有界抽取，过滤无价值内容并脱敏常见 secret；
- Phase 2 使用全局 lease、watermark、usage/recency 输入选择，生成 `MEMORY.md`、
  `memory_summary.md`、rollout summaries 和可复用 memory skills；
- 普通 Turn 只注入 compact routing index，随后通过 shell 或可选 dedicated tools 渐进读取；
- 记忆引用从流式正文中隐藏，作为 canonical assistant provenance 持久化并回写 usage；
- external MCP 污染策略、过期 lease 接管、失败退避、旧 artifact 保留和 symlink 边界均有测试。

与当前 Codex 一致，这不是 embedding/vector RAG。它用模型做语义抽取和归并，用小型 summary
路由，再对 Markdown 做关键词搜索和按需读取。Corki 当前 Phase 2 直接使用隔离的 `ModelPort`
请求；Codex 使用内部 consolidation sub-agent。等 Corki 开启多 Agent 后，可替换 orchestration，
但 repository、artifact 和 read path 契约不需要变化。

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

当前纵向核心闭环、恢复、长任务稳定性与跨 Thread 长期记忆已经完成。后续工作不应重新改写主循环：

1. 增加 thread 列表、真实 `/status` 聚合和可观测性导出；
2. 为已知模型增加精确 tokenizer，未知 provider 继续使用保守估算；
3. 持续维护显式 provider capability profile 和 opt-in 在线 smoke test；
4. 通过现有 ToolExposure、PromptSlot、ToolRegistry 契约接入审批/sandbox、多 Agent、
   Code Mode 和语音 realtime。

## 18. Skills、MCP、插件与 realtime 开发约定

### 18.1 Skills

启动时将源码根目录 `skills/` 原子同步到 `~/.corki/skills/.system`。发现优先级是当前目录到
项目根的 `.corki/skills`，随后是同层 Codex-compatible `.codex/skills` 与 portable
`.agents/skills`，再到用户 `~/.corki/skills`、`~/.agents/skills`、`~/.codex/skills`、系统
skills 和插件 skills；同名时高优先级胜出，插件 skill 使用 `plugin-name:skill-name` 避免冲突。
每轮只向模型提供有上限的元数据目录；显式 `$skill-name` 会把完整 `SKILL.md` 作为独立 user
context 注入，语义匹配则由模型调用 `skill_read` 完成。引用资源必须留在 skill 目录内并受
128 KiB 上限约束。`compatibility_home` 只由生产 composition root 注入，测试默认不扫描真实
用户目录。与 Codex 一样，project/user skill root 允许链接到共享 skill checkout，并用真实路径
去重防止 symlink cycle；发行包安装的 `.system` root 不跟随目录 symlink。

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

这张表是持续维护的工程基线。这里的“已对齐”表示核心语义和失败边界相同，不表示逐行翻译
Rust，也不包含本阶段明确排除的审批/sandbox 与多 Agent。

| 领域 | Corki 当前契约 | 对应实现与验收 |
| --- | --- | --- |
| Planning & Reasoning | 可见 plan 与 provider reasoning 分离；plan 最多一个 active；reasoning 可流式、持久化和按 capability replay | `planning/`、`update_plan`、两个 provider adapter；core demo 与 model adapter tests |
| Memory | 同 Thread 连续历史与 compaction；跨 Thread 两阶段抽取/合并；lease/watermark；分层 Markdown 渐进召回；结构化引用与 usage 排序 | `memory/`、`prompts/memory/`、`protocol/memory.py`；repository/pipeline/backend/integration tests |
| Tool Use | schema 与 handler 统一注册；每个 step 冻结 advertised specs；exclusive/parallel 调度；结果预算与幂等 ledger | `tools/`、`core/graph.py`；builtin tools demo、tool loop、parallel/recovery tests |
| Context Engineering | base instructions、mode、层级 AGENTS、environment、Git 与 extensions 分层；context 先于当前 user；同 key 只暴露最新值 | `prompting/`、`context/`、`prompts/`；mock turn 与 context recall tests |
| State Management | Thread/Turn/Step 分层；显式 LangGraph edges；业务 SQLite 与 graph checkpoint 分离；resume 不重复采样或副作用 | `core/`、`sessions/`、`storage/`；checkpoint/fault-injection tests |
| Evaluation & Guardrails | step/tool 上限、输出完整性、跨 Turn item、重复 call id、JSON/schema 与空响应验证 | `evaluation/`、provider adapters、`ToolExecutor`；guards/transport/malformed-provider tests |
| Extensions | project/user/system/plugin skills；Codex manifest；stdio/HTTP MCP 的 tools/resources/templates/prompts；文本 steering | `skills/`、`plugins/`、`mcp/`、`realtime/`；extensions demo 与各包 tests |

持续对标时优先验证下面这些“不变量”，不要只比较类名或目录：

1. 一次采样的 advertised / dispatch tools 来自同一个冻结注册视图。原生 deferred 工具定义通过
   搜索输出历史提供，不要求出现在顶层 tools；兼容路径才把已检索工具追加到顶层列表。
   registry 仍只允许在 composition/startup 阶段变化，运行中的动态 MCP 更新尚待对齐。
2. world-state/context diff 先于它所限定的用户输入；压缩不得吞掉当前输入或制造 history gap。
3. model output、tool call、tool result 都是 canonical history，而不是只能在终端看到的临时文本。
4. model step 和 tool side effect 在 checkpoint 边界上都可幂等恢复；未知副作用不能自动重放。
5. 可选扩展单点失败要降级为 warning/tool error，不能破坏无关的核心工具或整个 CLI。

与 Codex 仍有意保留的实现差异：Corki 使用支持 Unicode 与图片的 provider-neutral token
estimator，没有 Codex 的逐模型 tokenizer 与远程 compaction；Phase 2 目前用隔离模型请求而非
内部 sub-agent；模型/工具 capability plan 比 Codex 的动态 hosted tool selection 简化；Code Mode、
hosted web search、语音 Realtime API、审批/sandbox 和多 Agent 尚未进入本阶段。新增其中任何能力时，应扩展 `ModelPort`、`ContextContributor`、
`ToolRegistry` 或 runtime event，不得在 CLI 或 graph 中建立第二条旁路循环。

### 19.1 可执行验收场景

- `examples/core_loop_demo.py`：证明 plan、项目指令、shell、patch、验证、最终响应与跨 Turn memory。
- `examples/builtin_tools_demo.py`：逐个调用全部默认工具，证明长进程 `write_stdin` 和图片 attachment
  能回到下一次模型请求。
- `examples/extensions_demo.py`：动态创建并召回 project skill、Codex-format plugin，并启动真实
  JSON-RPC stdio MCP server 走完整模型/工具闭环。

examples 不是替代测试的脚本。它们的 stdout 是稳定 JSON，`tests/e2e/test_examples.py` 会逐个
作为独立进程执行；任何 demo 发现的 bug 还必须在更小的 unit/integration 层留下回归测试。
