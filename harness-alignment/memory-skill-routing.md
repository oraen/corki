# 记忆技能读取入口修复

## 现象与定位

`weather-lookup-wttr-jq`实际位于用户memory的skills目录。只读复验确认：
普通SkillService未发现它，单独扫描memory目录可正常解析，无重名/文件损坏证据。
此前把这概括成“漏接扫描目录”不够准确：对照Codex后，应保留两个不同的读取入口。

## Codex源码依据

参考本地 `<codex-source>/codex-rs/`：

- `ext/skills/src/host_roots.rs::roots_from_layer_stack` 所在根构建逻辑：
  项目配置目录、用户skills、.agents/skills、系统及插件根，不自动加入memories/skills。
- `ext/memories/templates/memories/read_path.md`：summary → MEMORY.md → 相关
  skills/或rollout文件，按精确文件路径渐进读取，不把目录名直接当普通技能身份。
- `ext/memories/src/tools/read.rs::ReadTool::handle_call`：memory相对路径、
  line_offset/max_lines和固定token预算；`metrics.rs::scope_from_path`明确识别skills路径。
- `ext/memories/src/prompts_tests.rs`：记忆指令带实际memory根与summary。

## Corki修复

- `prompts/memory/read_path.md`明确memory skill不是普通catalog项，优先使用
  已提供的memories::read按相对路径读；未启用专用工具则读实际memory根下文件。
  必须读完整入口文件，分页续读，支持文件相对入口目录解析；同名普通技能不能替代。
- `skills/tools.py`补工具描述与失败恢复指引：skill_list仅查普通目录；memory引用
  改走精确路径，不凭missing-or-ambiguous断言文件不存在，也不猜memory根。
- 不自动注册/迁移/安装记忆技能，不改变普通技能优先级、禁用策略、扫描范围或memory开关。
  不修改用户生成的技能文件；该文件已有的错误调用示例不作为正确接口规范。

## 验证

新测试覆盖：默认memory技能不进入catalog/显式mention；失败提示不泄露文件内容
或猜测根；真实runtime中自定义中文memory根、普通同名技能存在/不存在两种情况，
错误入口后读取MEMORY.md再通过memories::read拿到正确技能全文。
模型为脚本替身，验证工具及上下文链路，不宣称能保证任何供应商模型永不选错工具。
未访问网络或真实用户剪贴板/密钥，未改写用户memory。

最终相关回归203 passed（4 workers，6.56秒）：全部技能单元、memory文本契约、
新增路由runtime、memory检索、读取策略及技能可见性。读取策略测试覆盖专用工具
启用/关闭、memory关闭/不使用/空summary等边界及chat/responses/lite本地模拟线协议。
初跑6个既有模板hash快照因新增指引失败，审阅后更新模板hash并增加新路由断言，
保留所有旧策略与开关断言；复跑全部通过。ruff check/format及diff检查通过。
