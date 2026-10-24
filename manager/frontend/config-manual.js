// Read-only guide to settings.json. Keep paths aligned with src/corki/config/.
const field = (path, title, description, recommended, type = '文本', choices = null) =>
  ({path, title, description, recommended, type, choices});
const choice = (...pairs) => pairs;

export const manualSections = [
  {key:'default', title:'默认模型', fields:[
    field('default.provider','默认供应商','新会话默认使用的供应商名称；未指定时按配置顺序使用第一个已开启的供应商。','选择已经配置好连接的供应商'),
    field('default.model','默认模型','新会话默认使用的模型名称，必须属于上面的供应商。','选择该供应商 models 中已配置的模型'),
  ]},
  {key:'providers', title:'供应商与模型', fields:[
    field('providers.[供应商]','供应商配置','以自定义名称组织连接信息和模型目录。','每个供应商使用一个容易辨认的名称','对象'),
    field('providers.[供应商].base_url','接口地址','模型服务的 HTTP(S) 基础地址。','填写供应商提供的官方 API 地址'),
    field('providers.[供应商].api_key','API Key','调用该供应商接口时使用的密钥。','填写对应供应商的密钥；不要分享配置文件'),
    field('providers.[供应商].enabled','供应商启用状态','关闭后保留配置和模型，但不再供新任务使用。','true（默认）','布尔值'),
    field('providers.[供应商].api_mode','接口协议','Corki 向供应商发送请求时使用的 API 协议。','按供应商接口文档选择','枚举',choice(['chat_completions','OpenAI Chat Completions 兼容接口'],['responses','OpenAI Responses 接口'])),
    field('providers.[供应商].name','适配器名称','指定使用哪种供应商适配行为，与供应商配置名称可以不同。','一般留空；仅私有部署需要指定'),
    field('providers.[供应商].aliases','别名','保留旧名称供已有会话恢复时识别。','重命名供应商时才添加','文本数组'),
    field('providers.[供应商].reasoning_effort','默认推理等级','该供应商请求的默认推理等级；具体可用值取决于模型。','按模型文档选择；不确定时留空'),
    field('providers.[供应商].reasoning_summary','推理摘要','控制支持该能力的模型返回推理摘要的详细程度。','auto','枚举',choice(['auto','由服务端决定'],['concise','简短摘要'],['detailed','详细摘要'],['none','不请求摘要'])),
    field('providers.[供应商].thinking','思考模式','向支持该字段的适配器开启或关闭思考模式。','按供应商文档设置；不确定时留空','布尔值'),
    field('providers.[供应商].service_tier','服务等级','为支持服务等级的供应商指定请求档位。','一般留空；有对应套餐时再设置'),
    field('providers.[供应商].max_retries','流重试次数','连接或流处理失败后的重试次数。','5（默认）','整数'),
    field('providers.[供应商].request_max_retries','请求重试次数','模型请求失败后的重试次数。','4（默认）','整数'),
    field('providers.[供应商].models.[模型]','模型配置','模型名称及能力元数据，可配置多个。','使用接口实际支持的模型 ID','对象'),
    field('providers.[供应商].models.[模型].description','模型描述','在模型管理页面展示的简短介绍。','用一句话描述模型适用场景'),
    field('providers.[供应商].models.[模型].context_window','上下文窗口','模型可处理的上下文 token 容量。','使用供应商公布的准确数值','正整数'),
    field('providers.[供应商].models.[模型].max_context_window','最大上下文窗口','模型理论可用的最大窗口，供窗口策略参考。','仅在有可靠模型资料时设置','正整数'),
    field('providers.[供应商].models.[模型].default_reasoning_level','默认推理等级','该模型未单独选择推理等级时使用的档位。','选择 supported_reasoning_levels 中列出的档位'),
    field('providers.[供应商].models.[模型].supported_reasoning_levels','可选推理等级','列出模型支持的推理档位；每项为包含 effort 的对象。','只列出服务端确实支持的档位','对象数组'),
    field('providers.[供应商].models.[模型].tool_mode','模型工具模式','覆盖全局工具模式，让该模型使用指定的工具调用方式。','通常留空，继承 tools.mode','枚举',choice(['direct','直接向模型提供工具'],['code_mode','可使用代码编排工具'],['code_mode_only','仅通过代码编排工具调用'])),
    field('providers.[供应商].models.[模型].effective_context_window_percent','有效窗口比例','用于留出上下文余量的窗口百分比。','95（默认）','整数'),
  ]},
  {key:'agent', title:'Agent 与上下文', fields:[
    field('agent.model','模型名称','旧式单供应商配置的模型选择；有 default 时优先使用 default。','新配置使用 default.provider + default.model'),
    field('agent.context_window_tokens','上下文容量','没有模型元数据时使用的上下文窗口容量。','优先在模型配置中填写准确窗口','正整数'),
    field('agent.auto_compact_tokens','自动压缩阈值','到达阈值时开始压缩上下文；留空则根据模型窗口计算。','留空，使用自动计算','正整数或 null'),
    field('agent.auto_compact_token_limit_scope','压缩计数范围','确定自动压缩阈值按哪些 token 计数。','total','枚举',choice(['total','计算完整上下文'],['body_after_prefix','只计算固定前缀之后的内容'])),
    field('agent.effective_context_window_percent','有效窗口比例','Agent 使用上下文窗口时保留的余量比例。','95（默认）','整数'),
    field('agent.include_environment_context','环境信息','是否向 Agent 提供工作目录和环境上下文。','true（默认）','布尔值'),
    field('agent.personality','回应风格','设定 Agent 的语言风格。','不设置，使用默认风格','枚举',choice(['none','不附加风格'],['friendly','友好'],['pragmatic','务实'])),
    field('plan_mode_reasoning_effort','规划模式推理等级','进入规划模式时使用的推理等级。','不设置，沿用模型选择'),
    field('instructions','基础指令','额外提供给 Agent 的基础文字指令。','仅放稳定且通用的规则'),
    field('compact_prompt','压缩提示词','覆盖自动压缩时使用的提示文字。','通常不设置'),
  ]},
  {key:'goals', title:'持续目标', fields:[
    field('features.goals','Goal 功能','启用 /goal 命令及目标工具。目标按会话保存，并在空闲时继续执行。','true（默认）','布尔值'),
    field('goals.max_goal_token_budget','目标预算上限','限制每个目标的 token 预算；设置后也作为新目标的默认预算，缓存输入不计入用量。','按任务成本设置；不设置则不限预算','正整数或 null'),
  ]},
  {key:'execution', title:'权限与执行', fields:[
    field('default_permissions','默认权限','选择内置权限或 permissions 中定义的命名权限。','按工作区信任状态由 Corki 自动选择','枚举',choice([':read-only','只读'],[':workspace','工作区可写'],[':danger-full-access','完整本机访问'],['自定义名称','引用 permissions 中的配置'])),
    field('approval_policy','审批策略','工具需要确认时的处理方式。','on-request','枚举',choice(['on-request','需要确认时询问'],['never','不发起审批请求'],['granular','按审批类型分别配置的对象'])),
    field('permissions.[名称]','自定义权限','定义可由 default_permissions 选择的命名权限配置。','只在内置权限不够用时定义','对象'),
    field('execution.compiler','权限编译器','自定义执行权限策略所用的编译器路径。','通常不设置，使用随安装提供的编译器'),
    field('execution.profile','执行权限档案','直接指定执行权限配置；不能与命名权限同时使用。','通常不设置，使用 default_permissions','对象'),
    field('shell_environment_policy.inherit','Shell 环境继承','控制命令执行时从父进程继承多少环境变量。','all（默认）','枚举',choice(['all','继承所有变量'],['core','只继承基础变量'],['none','不继承变量'])),
    field('shell_environment_policy.filters','环境变量过滤','按变量名模式标记允许或排除传给 Shell 的变量。','按需设置，避免传递无关变量','对象',choice(['include','匹配变量保留'],['exclude','匹配变量排除'])),
    field('shell_environment_policy.set','附加环境变量','为执行的命令设置固定环境变量。','只添加确实需要的变量','对象'),
  ]},
  {key:'tools', title:'工具', fields:[
    field('tools.mode','工具模式','控制工具直接调用还是通过代码模式编排；模型配置可覆盖它。','direct（默认）','枚举',choice(['direct','直接调用工具'],['code_mode','按需使用代码编排'],['code_mode_only','只通过代码编排调用'])),
    field('tools.command_timeout_seconds','命令超时','单次命令运行允许的最长秒数。','留空，使用工具默认超时','正数或 null'),
    field('tools.command_yield_seconds','命令首轮等待','命令执行后，首轮等待结果的秒数。','10（默认）','正数'),
    field('tools.allow_login_shell','登录 Shell','执行命令时是否允许加载 Shell 登录环境。','true（默认）','布尔值'),
    field('tools.background_terminal_max_timeout','后台终端超时上限','后台终端任务允许的最长超时时间，单位毫秒。','300000（默认）','非负整数'),
    field('tools.output_token_limit','工具输出上限','限制工具返回内容的 token 数量。','不设置，沿用运行时策略','非负整数'),
    field('tools.search_mode','工具搜索模式','决定工具搜索能力如何暴露给模型。','compatible（默认）','枚举',choice(['disabled','关闭工具搜索'],['compatible','兼容模式'],['native','原生模式'])),
    field('tools.freeform_mode','自由格式工具模式','决定自由格式工具调用的协议形式。','compatible（默认）','枚举',choice(['compatible','兼容格式'],['native','原生格式'])),
    field('tools.namespace_mode','工具命名空间模式','决定工具命名空间采用兼容还是原生形式。','compatible（默认）','枚举',choice(['compatible','兼容格式'],['native','原生格式'])),
    field('tools.code_mode_disable_fallback','代码模式降级','代码模式不可用时是否禁止回退到直接工具调用。','false（默认）','布尔值'),
  ]},
  {key:'skills', title:'Skills 与插件', fields:[
    field('skills.enabled','Skills 总开关','控制是否启用 Skills 加载。','true（默认）','布尔值'),
    field('skills.include_instructions','技能说明','控制是否把 Skills 的说明提供给 Agent。','true（默认）','布尔值'),
    field('skills.max_context_tokens','技能上下文额度','限制 Skills 说明使用的 token 数量。','不设置，沿用运行时默认','正整数'),
    field('skills.config','技能启停规则','按技能名称或 SKILL.md 路径配置禁用规则。','仅为需要关闭的技能添加规则','对象数组'),
    field('skills.config.[条目].path','技能文件路径','以 SKILL.md 文件路径指定一个技能。','优先使用实际 SKILL.md 路径'),
    field('skills.config.[条目].name','技能名称','按技能名称指定规则；与 path 二选一。','路径稳定时优先使用 path'),
    field('skills.config.[条目].enabled','是否启用','false 禁用所指定技能；缺省不覆盖状态。','禁用时写 false；启用时删除规则','布尔值'),
    field('plugins.directories','插件目录','额外加载的插件目录路径。','按需添加；没有插件时留空','文本数组'),
    field('plugins.disabled','禁用插件','按名称列出不加载的插件。','没有禁用需求时留空','文本数组'),
    field('features.plugins','插件总开关','控制插件系统是否加载。','true（默认）','布尔值'),
  ]},
  {key:'mcp', title:'MCP 服务', fields:[
    field('mcp.approval_policy','MCP 审批','MCP 相关操作的审批策略，与本地执行审批独立。','never（默认）','枚举',choice(['never','不请求审批'],['on-request','需要时请求审批'])),
    field('mcp.servers.[服务名]','MCP 服务','以名称定义一个可连接的 MCP 服务。','一个服务对应一个对象','对象'),
    field('mcp.servers.[服务名].transport','传输方式','指定通过本地标准输入输出，或 HTTP 连接服务。','按服务部署方式选择','枚举',choice(['stdio','启动本地进程，通过 stdin/stdout 通信'],['http','连接 HTTP MCP 地址'])),
    field('mcp.servers.[服务名].command','启动命令','stdio 服务的可执行命令；此模式下必填。','填写已安装的服务启动命令'),
    field('mcp.servers.[服务名].args','命令参数','启动 stdio 服务时传给命令的参数。','按服务文档填写','文本数组'),
    field('mcp.servers.[服务名].env','进程环境变量','为 stdio 服务进程提供环境变量。','只填写服务实际需要的变量','对象'),
    field('mcp.servers.[服务名].url','服务地址','HTTP 模式下 MCP 服务的 HTTP(S) 地址；此模式下必填。','填写服务公开的 MCP 地址'),
    field('mcp.servers.[服务名].headers','请求头','HTTP 模式下发送给 MCP 服务的请求头。','仅在服务要求时配置','对象'),
    field('mcp.servers.[服务名].timeout_seconds','调用超时','MCP 调用等待的最长秒数。','30（默认）','正数'),
    field('mcp.servers.[服务名].enabled','启用状态','是否在启动时注册该 MCP 服务。','true（默认）','布尔值'),
    field('mcp.servers.[服务名].required','启动必需','服务不可用时是否阻止正常启动。','false（默认）','布尔值'),
    field('mcp.servers.[服务名].enabled_tools','允许工具','只向 Agent 暴露这些工具名称。','需要限制工具时再填写','文本数组'),
    field('mcp.servers.[服务名].disabled_tools','禁用工具','从 MCP 服务提供的工具中排除这些名称。','不需要限制时留空','文本数组'),
  ]},
  {key:'memories', title:'记忆', fields:[
    field('memories.enabled','记忆总开关','是否开启长期记忆能力。','false（默认）','布尔值'),
    field('memories.generate','记忆生成','是否从符合条件的会话生成记忆。','true（启用记忆后使用默认值）','布尔值'),
    field('memories.use','记忆使用','是否在后续任务中使用已生成的记忆。','true（启用记忆后使用默认值）','布尔值'),
    field('memories.background_enabled','后台处理','是否在后台执行记忆生成任务。','true（默认）','布尔值'),
    field('memories.max_unused_days','未使用保留天数','控制长期未使用记忆的清理期限。','30（默认）','整数'),
    field('memories.summary_token_limit','摘要 token 上限','限制记忆摘要生成的长度。','2500（默认）','正整数'),
  ]},
  {key:'sessions', title:'会话清理', fields:[
    field('sessions.auto_cleanup','自动清理会话','开启后，在管理页面打开时每天最多执行一次后台清理；永久删除超过 3 个月没有更新且未被占用的会话。','false（默认，按需开启）','布尔值'),
  ]},
  {key:'other', title:'界面与其他', fields:[
    field('features.self_evolution','自进化','保存 Corki 的全局自进化设置。','false（默认）','布尔值'),
    field('features.fast_mode','快速模式','对支持服务等级的模型优先使用快速档位。','true（默认）','布尔值'),
    field('features.code_mode.enabled','代码模式','启用代码编排能力；最终调用方式由 tools.mode 决定。','按模型和工具能力启用','布尔值'),
    field('features.token_budget','Token 预算','启用上下文预算提示，可用布尔值或详细对象配置。','false（默认）','布尔值或对象'),
    field('realtime.enabled','实时输入','允许 CLI 的实时文本输入。','true（默认）','布尔值'),
    field('tui.notifications','桌面通知','控制终端事件通知；也可指定事件名数组。','true（默认）','布尔值或文本数组'),
    field('tui.notification_method','通知方式','指定终端通知发送方式。','auto（默认）','枚举',choice(['auto','自动选择'],['osc9','使用 OSC 9'],['bel','使用终端响铃'])),
    field('tui.notification_condition','通知条件','指定何时发送终端通知。','unfocused（默认）','枚举',choice(['always','始终发送'],['unfocused','终端不在前台时发送'])),
    field('project_doc_max_bytes','项目说明大小','允许读取的项目说明文件最大字节数。','32768（默认）','非负整数'),
    field('project_doc_fallback_filenames','备用说明文件','未找到标准项目说明时额外查找的文件名。','不设置','文本数组'),
    field('project_root_markers','项目根标记','用于定位项目根目录的文件或目录名。','[".git"]（默认）','文本数组'),
  ]},
];

// A map key is shown as [provider]/[model]/[server]; [0] represents one array item.
const normalize = path => path.replace('[供应商]','[provider]').replace('[模型]','[model]')
  .replace('[服务名]','[server]').replace('[名称]','[name]').replace('[条目]','[0]');
const extraFields = [
  field('provider','旧式供应商','兼容旧配置的单个供应商对象；新配置建议使用 providers 和 default。','新配置使用 providers','对象'),
  field('provider.id','供应商标识','旧式单供应商配置的标识。','使用有意义的名称'),
  field('provider.name','适配器名称','旧式单供应商配置的适配器名称。','仅在需要特殊适配行为时设置'),
  field('provider.base_url','接口地址','旧式单供应商配置的 HTTP(S) 基础地址。','使用官方 API 地址'),
  field('provider.api_key','API Key','旧式单供应商配置的接口密钥。','填写对应供应商的密钥'),
  field('provider.api_mode','接口协议','旧式单供应商配置的模型请求协议。','按接口文档选择','枚举',choice(['chat_completions','Chat Completions 兼容接口'],['responses','Responses 接口'])),
  field('models.catalog.[model]','旧式模型元数据','兼容旧配置的模型能力目录；新配置建议放在供应商 models 下。','新配置使用 providers.[provider].models','对象'),
  field('models.catalog.[model].context_window','上下文窗口','旧式模型目录中的上下文 token 容量。','使用供应商公布的容量','正整数'),
  field('models.context_window_override','上下文窗口覆盖','强制覆盖所选模型的上下文窗口。','通常不设置','正整数'),
  field('runtime.event_queue_size','事件队列容量','内部事件队列的最大长度；0 表示不设上限。','0（默认）','非负整数'),
  field('agents.interrupt_message','中断消息','控制 Agent 中断时是否插入通知消息。','true（默认）','布尔值'),
  field('orchestrator.mcp.enabled','编排器 MCP','是否启用编排器提供的 MCP 能力。','true（默认）','布尔值'),
  field('include_permissions_instructions','权限说明','是否向 Agent 提供当前执行权限说明。','true（默认）','布尔值'),
  field('features.personality','风格功能','是否启用 personality 风格指令。','true（默认）','布尔值'),
  field('features.hooks','Hooks 功能','是否加载并执行 Hooks。','true（默认）','布尔值'),
  field('features.skill_mcp_dependency_install','技能 MCP 依赖','技能声明 MCP 依赖时是否允许按需安装。','true（默认）','布尔值'),
  field('features.write_stdin_approval','标准输入审批','控制向运行中命令写入标准输入时的审批行为。','false（默认）','布尔值'),
  field('realtime.max_inputs_per_turn','实时输入上限','单个回合最多接收的实时输入数量。','16（默认）','正整数'),
  field('mcp_oauth_credentials_store','MCP OAuth 凭据','OAuth 凭据的存储位置。','auto（默认）','枚举',choice(['auto','自动选择存储方式'],['file','写入本地文件'],['keyring','使用系统钥匙串'])),
  field('mcp_optional_startup_grace_ms','MCP 启动宽限','非必需 MCP 服务启动时等待的宽限时间，单位毫秒。','1000（默认）','非负整数'),
  field('providers.[provider].aliases.[0]','供应商别名','别名数组中的一个旧供应商名称。','重命名后需要兼容旧会话时填写'),
  field('providers.[provider].models.[model].supported_reasoning_levels.[0]','推理档位条目','一个受支持的推理档位描述。','只列实际支持的档位','对象'),
  field('providers.[provider].models.[model].supported_reasoning_levels.[0].effort','推理档位','该条目的档位名称，具体取值由模型决定。','例如 medium'),
  field('skills.config.[0]','技能规则条目','一条技能启停规则，按 path 或 name 定位。','只记录需要禁用的技能','对象'),
  field('plugins.directories.[0]','插件目录路径','一个要扫描的插件目录。','填写本机目录'),
  field('plugins.disabled.[0]','禁用的插件','一个插件名称。','按需填写'),
  field('mcp.servers.[server].args.[0]','启动参数','stdio 命令的一个参数。','按服务文档填写'),
  field('mcp.servers.[server].env.[variable]','进程变量','为 stdio 服务设置的一个环境变量。','只填服务需要的变量'),
  field('mcp.servers.[server].headers.[header]','HTTP 请求头','HTTP MCP 服务使用的一个请求头。','按服务要求填写'),
  field('mcp.servers.[server].enabled_tools.[0]','允许的工具','允许暴露的一个工具名称。','按需填写'),
  field('mcp.servers.[server].disabled_tools.[0]','禁用的工具','从服务中排除的一个工具名称。','按需填写'),
  field('shell_environment_policy.filters.[pattern]','变量过滤规则','一个变量名模式及其包含或排除动作。','按需填写','枚举',choice(['include','保留匹配的变量'],['exclude','排除匹配的变量'])),
  field('shell_environment_policy.set.[variable]','环境变量值','设置一个传给命令的环境变量。','按需填写'),
  field('permissions.[name].extends','继承权限','基于一个内置权限档案定义命名权限。','例如 :read-only'),
  field('execution.profile.type','权限档案类型','自定义执行档案的类型，由权限编译器校验。','例如 read-only'),
  field('features.code_mode','代码模式设置','启用代码模式及配置其工具范围。','无需定制时省略','对象'),
  field('features.token_budget.enabled','预算开关','在详细预算对象中启用或关闭 Token 预算。','false（默认）','布尔值'),
  field('features.token_budget.reminder_threshold_tokens','提醒阈值','剩余上下文 token 低于阈值时发出提醒。','启用预算时按模型窗口设置','正整数'),
  field('features.token_budget.use_history_notes_extension','历史笔记','压缩时是否使用历史笔记扩展。','false（默认）','布尔值'),
  field('project_doc_fallback_filenames.[0]','备用文件名','一个备用项目说明文件名。','按需填写'),
  field('project_root_markers.[0]','根目录标记','一个用于识别项目根目录的名称。','.git'),
  field('tui.notifications.[0]','通知事件','一个需要显示终端通知的事件名称。','按需填写'),
];

const groupInfo = {
  default:['默认模型','定义新会话使用的默认供应商和模型。'],
  provider:['旧式供应商','兼容单供应商配置；新配置建议使用 providers。'],
  providers:['供应商列表','按名称配置供应商连接及其模型。'],
  agent:['Agent 设置','控制 Agent 的模型回退、上下文和压缩行为。'],
  agents:['Agent 行为','控制 Agent 消息等行为。'],
  runtime:['运行时','控制事件队列等内部运行参数。'],
  orchestrator:['编排器','控制编排器提供的能力。'],
  permissions:['命名权限','定义可供 default_permissions 引用的权限档案。'],
  execution:['执行策略','直接指定执行权限编译器和权限档案。'],
  shell_environment_policy:['Shell 环境','控制命令执行时继承、过滤或补充环境变量。'],
  tools:['工具设置','控制本地工具调用、超时和协议模式。'],
  skills:['Skills 设置','控制技能加载、说明注入和单项启停。'],
  plugins:['插件设置','配置插件目录和禁用列表。'],
  goals:['持续目标','设置会话目标的预算上限。'],
  features:['功能开关','控制可选运行时能力。'],
  mcp:['MCP 设置','配置 MCP 审批及服务连接。'],
  memories:['记忆设置','控制长期记忆的生成、使用和保留。'],
  realtime:['实时输入','控制 CLI 的实时文本输入。'],
  tui:['终端界面','配置终端通知等交互行为。'],
};
const nestedInfo = {
  'providers.[provider].models':['模型目录','按模型 ID 管理能力元数据。'],
  'models':['旧式模型设置','兼容旧式模型目录与窗口覆盖配置。'],
  'models.catalog':['旧式模型目录','按模型 ID 保存旧式能力元数据。'],
  'mcp.servers':['MCP 服务列表','按服务名配置本地或 HTTP MCP 服务。'],
  'skills.config':['技能规则','逐项指定技能启停规则。'],
  'features.token_budget':['Token 预算','预算提示的开关和细项设置；也可直接使用布尔值。'],
};
const defaults = {
  'features.self_evolution':'false','sessions.auto_cleanup':'false',
  'features.goals':'true','goals.max_goal_token_budget':'null',
  'provider.api_mode':'"chat_completions"',
  'runtime.event_queue_size':'0','agents.interrupt_message':'true',
  'orchestrator.mcp.enabled':'true','include_permissions_instructions':'true',
  'features.personality':'true','features.hooks':'true',
  'features.skill_mcp_dependency_install':'true','features.write_stdin_approval':'false',
  'realtime.max_inputs_per_turn':'16',
  'mcp_oauth_credentials_store':'"auto"',
  'mcp_optional_startup_grace_ms':'1000',
  'default.provider':'未指定时使用第一个已开启的供应商',
  'default.model':'无；配置 default 时必须填写',
  'providers.[provider].enabled':'true',
  'providers':'{}','providers.[provider].aliases':'[]',
  'providers.[provider].api_mode':'"chat_completions"',
  'providers.[provider].max_retries':'5','providers.[provider].request_max_retries':'4',
  'providers.[provider].models.[model].effective_context_window_percent':'95',
  'agent.model':'"gpt-5"（未设置 default 时）',
  'agent.context_window_tokens':'65536（可能被模型元数据覆盖）',
  'agent.auto_compact_tokens':'null（根据模型窗口计算）',
  'agent.auto_compact_token_limit_scope':'"total"',
  'agent.effective_context_window_percent':'95',
  'agent.include_environment_context':'true',
  'default_permissions':'按工作区信任状态自动选择',
  'approval_policy':'按工作区信任状态自动选择',
  'shell_environment_policy.inherit':'"all"',
  'tools.mode':'"direct"','tools.command_timeout_seconds':'null',
  'tools.command_yield_seconds':'10','tools.allow_login_shell':'true',
  'tools.background_terminal_max_timeout':'300000',
  'tools.output_token_limit':'null','tools.search_mode':'"compatible"',
  'tools.freeform_mode':'"compatible"','tools.namespace_mode':'"compatible"',
  'tools.code_mode_disable_fallback':'false',
  'skills.enabled':'true','skills.include_instructions':'true',
  'skills.config':'[]','plugins.directories':'[]','plugins.disabled':'[]',
  'features.plugins':'true','features.fast_mode':'true',
  'features.code_mode.enabled':'false','features.token_budget':'false',
  'features.token_budget.enabled':'false',
  'features.token_budget.use_history_notes_extension':'false',
  'mcp.approval_policy':'"never"','mcp.servers':'{}',
  'mcp.servers.[server].transport':'未设置时从 command / url 推断',
  'mcp.servers.[server].command':'无；stdio 服务必须填写',
  'mcp.servers.[server].url':'无；HTTP 服务必须填写',
  'mcp.servers.[server].timeout_seconds':'30',
  'mcp.servers.[server].enabled':'true','mcp.servers.[server].required':'false',
  'memories.enabled':'false','memories.generate':'true','memories.use':'true',
  'memories.background_enabled':'true','memories.max_unused_days':'30',
  'memories.summary_token_limit':'2500',
  'realtime.enabled':'true','tui.notifications':'true',
  'tui.notification_method':'"auto"','tui.notification_condition':'"unfocused"',
  'project_doc_max_bytes':'32768','project_doc_fallback_filenames':'[]',
  'project_root_markers':'[".git"]',
  'skills.config.[0].path':'无；与 name 二选一',
  'skills.config.[0].name':'无；与 path 二选一',
  'execution.profile.type':'无；配置 profile 时必须填写',
  'providers.[provider].models.[model].supported_reasoning_levels.[0].effort':'无；数组条目必须填写',
};
const required = {
  'default.provider':'否',
  'default.model':'配置 default 时必填',
  'execution.profile.type':'配置 execution.profile 时必填',
  'skills.config.[0].path':'与 name 二选一',
  'skills.config.[0].name':'与 path 二选一',
  'mcp.servers.[server].command':'stdio 服务必填',
  'mcp.servers.[server].url':'HTTP 服务必填',
  'providers.[provider].models.[model].supported_reasoning_levels.[0].effort':'数组条目必填',
};
const samples = {
  'goals.max_goal_token_budget':200000,
  provider:{id:'demo',base_url:'https://api.example.com/v1',api_key:'YOUR_API_KEY',api_mode:'chat_completions'},
  'models.catalog':{'demo-model':{context_window:262144}},
  'models.catalog.[model]':{context_window:262144},
  default:{provider:'demo',model:'demo-model'},
  providers:{demo:{base_url:'https://api.example.com/v1',api_key:'YOUR_API_KEY',api_mode:'chat_completions',models:{'demo-model':{context_window:262144}}}},
  'providers.[provider]':{base_url:'https://api.example.com/v1',api_key:'YOUR_API_KEY',api_mode:'chat_completions',models:{'demo-model':{context_window:262144}}},
  'providers.[provider].models':{'demo-model':{context_window:262144}},
  'providers.[provider].models.[model]':{context_window:262144},
  'providers.[provider].models.[model].supported_reasoning_levels':[{effort:'medium'}],
  'providers.[provider].models.[model].supported_reasoning_levels.[0]':{effort:'medium'},
  'providers.[provider].models.[model].supported_reasoning_levels.[0].effort':'medium',
  'providers.[provider].aliases':['old-demo'],
  'skills.config':[{path:'/path/to/SKILL.md',enabled:false}],
  'skills.config.[0]':{path:'/path/to/SKILL.md',enabled:false},
  'mcp.servers':{filesystem:{transport:'stdio',command:'node',args:['server.js']}},
  'mcp.servers.[server]':{transport:'stdio',command:'node',args:['server.js']},
  'mcp.servers.[server].env':{EXAMPLE_FLAG:'1'},
  'mcp.servers.[server].headers':{Accept:'application/json'},
  'shell_environment_policy.filters':{PATH:'include'},
  'shell_environment_policy.set':{EXAMPLE_FLAG:'1'},
  'features.token_budget':{enabled:true,reminder_threshold_tokens:10000},
  'execution.profile':{type:'read-only'},
  'permissions':{review:{extends:':read-only'}},
  'project_root_markers':['.git'],
};
const sampleByKey = {
  provider:'demo',model:'demo-model',base_url:'https://api.example.com/v1',
  api_key:'YOUR_API_KEY',api_mode:'chat_completions',name:'demo',
  description:'适合日常任务',context_window:262144,max_context_window:262144,
  default_reasoning_level:'medium',reasoning_effort:'medium',
  reasoning_summary:'auto',service_tier:'priority',
  auto_compact_tokens:180000,context_window_tokens:262144,
  default_permissions:':workspace',approval_policy:'on-request',
  command:'node',url:'https://example.com/mcp',
  path:'/path/to/SKILL.md',inherit:'all',mode:'direct',
  extends:':read-only',type:'read-only',
  transport:'stdio',notification_method:'auto',notification_condition:'unfocused',
  mcp_oauth_credentials_store:'auto',
  filters:{PATH:'include'},set:{EXAMPLE_FLAG:'1'},
};
const placeholderKeys = {'[provider]':'demo','[model]':'demo-model','[server]':'filesystem',
  '[name]':'review','[variable]':'EXAMPLE_FLAG','[header]':'Accept','[pattern]':'PATH'};
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export const manualFields = [...manualSections.flatMap(section => section.fields), ...extraFields]
  .map(item => ({...item,path:normalize(item.path)}));
const root = {children:new Map(),path:''};
const nodes = new Map();
for(const field of manualFields){
  let parent=root;
  for(const part of field.path.split('.')){
    const path=parent.path?`${parent.path}.${part}`:part;
    if(!parent.children.has(part))parent.children.set(part,{key:part,path,children:new Map(),field:null});
    parent=parent.children.get(part);
    nodes.set(path,parent);
  }
  parent.field=field;
}

function info(path){
  const node=nodes.get(path);
  const defined=node.field;
  const [title,description]=groupInfo[path]||nestedInfo[path]||[];
  const rawType=defined?.type || (node.children.size?'对象':'文本');
  const type=rawType==='枚举'?'字符串 · 枚举':rawType==='文本'?'字符串':rawType==='文本数组'?'数组 · 字符串':rawType==='对象数组'?'数组 · 对象':rawType==='布尔值'?'布尔值':rawType;
  return {path,title:defined?.title||title||node.key,description:defined?.description||description||'此对象包含下列可配置字段。',
    type,choices:defined?.choices,required:required[path]||'否',defaultValue:defaults[path]||'未设置（可省略）'};
}

function sample(path){
  if(Object.hasOwn(samples,path))return samples[path];
  const node=nodes.get(path), item=info(path), key=node.key;
  if(item.choices?.length)return item.choices[0][0];
  if(Object.hasOwn(sampleByKey,key))return sampleByKey[key];
  if(node.children.size){
    const child=[...node.children.values()][0];
    const childValue=sample(child.path);
    return child.key==='[0]'?[childValue]:{[placeholderKeys[child.key]||child.key]:childValue};
  }
  if(item.type.startsWith('数组'))return ['example'];
  if(item.type.includes('对象'))return {};
  if(item.type.includes('布尔'))return true;
  if(item.type.includes('整数')||item.type.includes('正数'))return 30;
  return 'example';
}

export function manualExample(path){
  if(path==='default.provider'||path==='default.model')return JSON.stringify({default:samples.default},null,2);
  if(path==='skills.config.[0].path')return JSON.stringify({skills:{config:[{path:'/path/to/SKILL.md',enabled:false}]}},null,2);
  if(path==='skills.config.[0].name')return JSON.stringify({skills:{config:[{name:'skill-creator',enabled:false}]}},null,2);
  const parts=path.split('.');
  let value=sample(path);
  for(let index=parts.length-1;index>=0;index--){
    const part=parts[index];
    value=part==='[0]'?[value]:{[placeholderKeys[part]||part]:value};
  }
  return JSON.stringify(value,null,2);
}

function syntax(text){
  const token=/"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|\b(?:true|false|null|-?\d+(?:\.\d+)?)\b/g;
  let result='',last=0,match;
  while((match=token.exec(text))){
    result+=escape(text.slice(last,match.index));
    const value=match[0],kind=value[0]==='"'?(text.slice(token.lastIndex).match(/^\s*:/)?'key':'string'):/^(true|false|null)$/.test(value)?'boolean':'number';
    result+=`<span class="manual-json-${kind}">${escape(value)}</span>`;
    last=token.lastIndex;
  }
  return result+escape(text.slice(last));
}

function tree(node, selected){
  const chosen=node.path===selected;
  const label=`<span>${escape(node.key)}</span>`;
  const selectedClass=chosen?' selected':'';
  if(!node.children.size)return `<button type="button" class="manual-field${selectedClass}" data-manual-field="${escape(node.path)}" aria-current="${chosen}">${label}</button>`;
  const open=selected===node.path||selected.startsWith(node.path+'.');
  return `<details class="manual-node" ${open?'open':''}><summary class="manual-field${selectedClass}" data-manual-field="${escape(node.path)}" aria-current="${chosen}">${label}<span class="manual-chevron"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></span></summary><div class="manual-children">${[...node.children.values()].map(child=>tree(child,selected)).join('')}</div></details>`;
}

export function manualDetail(path){
  const item=info(nodes.has(path)?path:manualFields[0].path);
  const choices=item.choices?.length?`<section class="manual-detail-block"><h3>可选值</h3><div class="manual-options">${item.choices.map(([value,meaning])=>`<div><code>${escape(value)}</code><span>${escape(meaning)}</span></div>`).join('')}</div></section>`:'';
  return `<div class="manual-detail-head"><span class="manual-detail-type">${escape(item.type)}</span><h2>${escape(item.title)}</h2><code>${escape(item.path)}</code></div><section class="manual-detail-block"><h3>中文含义</h3><p>${escape(item.description)}</p></section><div class="manual-facts"><section><h3>配置类型</h3><strong>${escape(item.type)}</strong></section><section><h3>是否必须</h3><strong>${escape(item.required)}</strong></section><section><h3>默认值</h3><strong>${escape(item.defaultValue)}</strong></section></div>${choices}<section class="manual-detail-block"><h3>JSON 示例片段</h3><pre class="manual-example"><code>${syntax(manualExample(item.path))}</code></pre></section>`;
}

export function manualMarkup(selected){
  return `<div class="manual-layout"><nav class="panel manual-tree" aria-label="配置字段树"><div class="manual-tree-heading"><strong>配置字段</strong><span>${nodes.size} 项</span></div><div class="manual-tree-nodes">${[...root.children.values()].map(node=>tree(node,selected)).join('')}</div></nav><article class="panel manual-detail" aria-live="polite">${manualDetail(selected)}</article></div>`;
}
