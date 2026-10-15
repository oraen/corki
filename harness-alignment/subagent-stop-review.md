# SubagentStop：类型化来源、独立授权、真实执行与冷恢复

## 基准与修复前差异

参考Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc：
core/src/hook_runtime.rs::run_turn_stop_hooks将ThreadSpawn分流到SubagentStop；
subagent_hook_context使用子thread id及agent_role（缺省default）。
hooks/src/events/stop.rs选择agent_type匹配的handler，dispatcher执行并聚合Stop/Block；
engine/discovery.rs::hook_hash将event_name、有效matcher和规范化handler共同纳入信任。
config key标签为subagent_stop，不是显示标签subagent-stop。

Corki已有ThreadSpawnSource，但Runtime→GraphRunContext只携带非root布尔值；
StopHooks只读取Stop并跳过全部非root。新增test_subagent_stop的真实Runtime场景，
修复前6失败/6通过（5b3988），六个已批准匹配的child都只采样一次且未运行Hook。
负例覆盖不匹配、Stop指纹不能授权SubagentStop、matcher改动、内部记忆、synthetic
来源和custom前缀不能伪装为ThreadSpawn。不把预设模型当成真实模型选择质量证据。

## 已实施链路

- Runtime._execute_run将完整SessionSource传给GraphRunContext，原布尔字段保持兼容。
- StopHooks.prepare原子保存Stop/SubagentStop两份发现结果；run按类型化来源选事件，
  匹配角色后再捕获整个批次。内部memory/synthetic不运行当前用户/项目/插件Stop。
- command_identity新增event_name/matcher；原Stop规范JSON与指纹保持不变，Stop
  继续忽略matcher。SubagentStop的matcher是信任身份，不能复用Stop或其他matcher批准。
- subagent_stop批次/执行key、HookRunSummary事件名、反馈content_kind各自独立。
  原Stop执行key保留，旧快照缺matcher字段时用None，未知结果仍在副作用前拒绝恢复。
- child stdin带真实session_id、agent_id、agent_type、stop_hook_active和事件名；
  Block反馈进入下一采样，后续检查active为true；命令不是模型可调用工具。

## 匹配引擎及边界

原生events/common.rs::matches_matcher对None/空/*匹配全部；仅ASCII字母数字、
下划线和竖线时按精确备选值匹配，其余走regex::Regex未锚定搜索。
因此不能拿MCP的regex-lite整值匹配或Python re直接替代。
本批在已有无import WASM中加入独立hook_validate/hook_matches导出，锁定
regex1.12.3，MCP仍用regex-lite0.1.8原导出。没有服务、鉴权、文件或网络能力。
共享现有256MiB/100M fuel上限，超限校验失败、运行时不匹配，不回退宽松匹配。
新单位验证Unicode类别、ASCII精确匹配、备选、未锚定搜索、非法反向引用/前瞻和
fuel失败，同时证明MCP整值与ASCII语义不被改变。

用既有隔离Rust1.85.1工具链离线生成lock后成功构建（068737）；最初shell没有cargo，
改用已有/private/tmp/corki-mcp-regex.Yy8S4c环境，未安装新工具链或修改参考仓库。
新WASM SHA256：dc596e2f2ce14c02efb3262f9237d310b531e7b42be38852c47512ee182de15b。
依赖许可证及第三方说明已补齐；本轮未以安装态wheel验证冒充源码态运行。

## 验证与未关闭项

首次实现后新child/原Stop/恢复/控制/身份71通过（4efa86）；扩大至MCP matcher、
原Stop来源/JSON/插件及evaluation181通过（81e51c，15.59秒）。
test_stop_hook_recovery将原12组合保留并增加SubagentStop：unknown/completed/feedback
三个窗口×用户/旧key迁移/插件/插件迁移，共24通过（585d28）。未知执行不重放，
已提交结果复用，反馈去重，插件移动的身份变化拒绝恢复，且原历史前缀保持。
后加session_id断言曾误写成all(async_generator)，6失败/237通过（b28cad）；已改为
先await读取session id再普通all比较。这是新增测试语法问题，不作为生产反例。

最终扩大回归（正确设置当前sandbox compiler及null keyring）：evaluation、MCP
matcher单位/实际Runtime、所有Stop/child来源与恢复、Hook输出、session来源及
memory_session_source联合**298 passed，32.35秒**（834d59，session74160退出0）。
其中不包含新跑的完整CLI PTY或完整A–F全量；先前全量基线发生在本批生产修改前。

后续异步实现已接入，见async-stop-review.md；本节此前同步证据不冒充异步验收。
仍未完成：Hook授权UI、完整来源/管理配置与刷新身份组合
仍需后续处理。Corki目前是SQLite业务历史，没有可交给Hook读取的原生JSONL transcript，
child的transcript_path和agent_transcript_path返回None，不把数据库文件或子历史路径
伪装成父transcript；文件式转录读取能力未由本批证明。没有据此宣布全部Hook或A–F完成。
