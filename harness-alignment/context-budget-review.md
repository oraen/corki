# C3 请求预算复核

## A6/C8/E6 媒体预算取消所有权复核

原生 tasks/mod.rs::abort_all_tasks 先 handle_task_abort，再清 pending approvals，
明确要求被中断任务先观察到取消。Corki 媒体 prepare_items 经 joined_work 持有 CPU
线程任务，shield 后在取消时继续 join；本轮补实际 Runtime 接入证据，不只测 helper。

test_budget_projection_owns_cpu_work_until_runtime_finishes 门控真实 ImagePreparation
for_model 投影线程，验证 cancel/repeat_cancel/runtime.close：工作线程未释放前
消费任务不完成、关闭不完成、模型未关闭且没有采样；释放后 CancelledError 传播、
只有一个 TurnCancelled，无 TurnFailed/TurnCompleted 冒充取消。非关闭路径还验证
旧图片历史未改写及同 Runtime 新 Turn 成功。关闭路径不宣称已验证冷 Runtime 续跑。

新增取消三组合与媒体预算、CPU join 单位及 provider 生命周期共 54 通过（c47bec，
2.89 秒）；ruff/1110 文件格式/compileall/77 包/diff 通过（e9f0c8）。本轮无生产
修改；并非任意媒体编解码器硬卡死均可终止的保证，完整 A–F 仍需继续收敛。

## 媒体预算修复结果

window 的本地请求估算统一调用媒体 for_model=True 投影；usage 锚点后的新增内容
估算也使用同一投影，锚点身份和原始数据库记录不改写。覆盖模型切换估算、prepare、
摘要后固定部分/最终请求、手动压缩与预算状态；不通过提前改写历史实现“对齐”。
第一处本地估算修复后 477 通过（87a2ef）；追加 usage 锚点场景又复现 1 失败 3 通过
（b0c60e），据此修复增量 usage 估算，扩大 context/媒体/普通预算/压缩/降窗/
TokenBudget 共 553 通过（c76549，31.08 秒）。随后图片/音频 × 支持/不支持 ×
有/无 usage 锚点 8 组合通过（640f6b），均为实际 Runtime/SQLite，原历史前缀不变。
音频 fixture 仅用于 data URL 和预算路径，不能证明真实 codec 解码或供应商音频计费。
本地启发式仍不声称精确 tokenizer，附件所有格式与整个 A–F 不由这 8 组关闭。

## 媒体预算差异（修复前）

Codex history.normalize_history 经 normalize.strip_images_when_unsupported / audio
把不支持的媒体替换为文本。Corki graph._call_model_owned 也投影为替代文本，但
window.prepare 预算使用 for_model=False 的归档媒体；16 张不支持图片仍计约 3 万
token，实际请求仅有短替代文本，错误触发压缩。真实 Runtime 对照 1 失败 1 通过
（42dd8b）。方案：本地请求估算统一先做模型媒体投影，保持归档及恢复内容不变；
模型支持图片时仍计算图片成本。覆盖自动/手动压缩重预算与状态查询的同类调用点。

## 修复结果（覆盖下方修复前状态）

window.prepare 的压缩准入增加本地估算触及完整硬窗口条件，复用原摘要与原子历史
替换流程，不修改供应商 usage 计数、不新增协议字段。仅本地估算超过自动阈值但
未达硬窗口时，仍保留原来的 usage 决策，而非一律取更大的粗估算。

有效修复前运行 4 失败、12 通过（2ce3e0）：低 usage + 大正文在热/冷两种状态下
都返回 context_window 失败。更早首次运行另有测试夹具误传 usage=None，已改为
合法 ModelUsage(total_tokens=None)；那部分失败不是生产问题，不计作修复证据。
修复后完整 context 单位目录、新 16 组合与普通 wire 压缩、搜索降窗、TokenBudget
通知/重置/挂起恢复联合 472 通过（d1ba52，13.61 秒），无跳过。
保留私有元数据不计成本、压缩原始记录不覆盖的断言。媒体完整组合仍未由本批关闭。

## 新发现：本地硬上限与压缩准入不一致（修复前记录）

触发：普通供应商返回较低 usage，但下一请求本地估算已超过可用硬窗口。
window.prepare 用 usage 决定是否压缩，随后却以 max(本地估算, usage) 判定硬拒绝。
结果是有可压缩历史也不尝试摘要，直接 TurnFailed。影响 C3/C4/E4，优先修复。
方案：本地估算触及同一个硬窗口也允许进入现有压缩路径；保留 usage 的自动阈值与
body-after-prefix 语义，不把所有粗估算强制覆盖供应商计数。验收覆盖热/冷、正文/
reasoning、低 usage/缺 usage、大正文/私有元数据，压缩后原记录不变。

原生调用链补核：session/turn.rs 采样后调用 context_window_token_status；
session/context_window.rs 无论 total/body_after_prefix 都保留独立完整窗口硬上限。
protocol/openai_models.rs::usable_context_window 明确以 effective percent 预留余量，
auto_compact_token_limit 取配置与原始窗口 90% 较小值。codex-api/src/common.rs 的
ResponsesApiRequest 没有 max_output_tokens 字段。因此“缺少独立精确输出预留配置”
不应被当作已证实的 Codex 对齐缺口；当前对齐基准是配置比例余量与独立硬上限，
不是新增原生主采样链也未提供的输出参数。下方上一轮待核验说法由本结论补充。

参考仓库 HEAD ddf04ad26789d040f9ef6a96736f76602e35a6cc，本次核对工作树干净。
Codex core/src/context_manager/history.rs 的 estimate_token_count 经
estimate_token_count_with_base_instructions 累加基础指令与每条历史成本；
estimate_item_token_count 使用模型可见序列化字节启发式，并对图片、音频负载替换估算。
源码明确它不是精确 tokenizer 计数。专属加密协议分支不是 Corki 普通传输的实现要求。

Corki context/window.py::prepare 在媒体准备、world-state 更新和输入绑定后，
用 tool_resolver 为候选历史重新选定工具，再调用 estimate_request_tokens。
该函数累加 instructions、普通工具定义（两种传输取较大估算）和合并后的消息成本。
discovered_tools 本身是内部状态，不在 Observation 中重复收费；加载后作为请求工具计入。
压缩后重新解析工具并重新估算，而不是沿用压缩前已加载 schema 的成本。

ContextLimits 默认可用窗口为原始窗口的 95%，自动阈值不超过原始窗口的 90%。
这是比例余量，不是已经实现了独立、精确的输出 token 预留配置；不能用它关闭 C3 的
全部输出预留要求。普通协议使用统一估算，不按 provider 名称切换官方 tokenizer。

## 本轮直接证据

扩展 tests/integration/test_ordinary_context_budget.py 为 8 组合：
assistant/reasoning × 热运行/冷恢复 × 小正文/大正文。私有归档元数据始终很大；
小正文只有两次普通采样、无压缩，大正文有一次额外摘要请求、随后继续普通采样。
摘要请求不带工具，压缩后最终请求移除旧大正文，原始持久模型结果保持完整。
这是实际 Runtime/SQLite/ScriptedModel 对照，不是仅调用 token helper 的单测。

8 通过（d0c639，1.69 秒）。此前预算、工具加载后压缩、消息合并、普通 wire 压缩及
模型降窗七文件 73 通过（56f373，3.00 秒）；两次独立运行不冒称一次组合运行。
静态 ruff、1109 文件格式、compileall、77 包依赖、diff 通过（1a83d7）。

## 待继续

本轮未改生产预算算法，不能证明所有附件成本、输出上限与供应商实际计费一致。
下一步需继续追踪原生余量/输出限制调用链，区分应修复缺口与普通兼容估算限制；
补媒体准备后成本的主循环组合验证。C3 仍部分实证，完整 A–F 未完成。
