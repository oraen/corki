# 插件资源包

入口：主页 → 工具箱 → 插件管理。

这是管理端的可选资源打包功能，不是 Agent 运行时扩展，不连接插件市场，不执行插件安装脚本。
插件没有启停状态；包内的资源是独立副本。创建备份不会改变现有 Skills、MCP 或 Hooks。

## 使用

- 创建插件：填写名称，可选描述；勾选当前 Skills、MCP、Hooks，或者上传文件、文件夹、ZIP。
- 本地文件中的 `SKILL.md` 会识别为 Skill；`mcp.json` / `.mcp.json` 中的服务会识别为 MCP；`hooks.json` 中的处理器会识别为 Hook。其他文件作为本地资源保留。
- 导入插件：上传单个插件 ZIP 或目录；先查看新增和同名跳过的清单，再确认导入。
- “导入后开启”默认不勾选，只决定本次新增资源的状态。同名资源的内容和状态都不改变。
- 所有资源均已存在时禁止重复导入。纯本地文件资源包也可以保存、导出、在另一环境导入一次。
- 导出：每个插件可单独导出 ZIP；勾选后可批量导出。批量 ZIP 内是按插件名标识的独立 ZIP，恢复时解压并逐个导入。
- 删除：永久删除插件备份，确认后无法恢复；不删除已经导入的资源。

新导入的 Skills 放在 Corki 扩展目录；MCP 和 Hooks 合并到原配置中，保留其他字段与已有开关。
如果用户已关闭整个 Skills / Hooks 功能，“导入后开启”不会擅自开启它们的总开关。

## 数据位置

- 备份：`$CORKI_HOME/manager/resource-packages/<id>/`，包含 `package.zip` 和列表元数据 `record.json`。
- 已导入、被组件使用的本地资源：`$CORKI_HOME/manager/imported-resources/<id>/`。
- 未设置 `CORKI_HOME` 时为 `~/.corki`。

这些路径不在 Agent 插件自动发现目录中。导出的包不会引用原插件备份目录；删除备份不影响已经导入的资源。
选择现有 Skill 类型 Hook 时，其引用的 Skill 目录和文件也会被复制入包。
MCP 的运行程序和第三方环境仍须由用户准备；资源打包不复制 Python 环境、Node 环境或整个工作目录。

## 清单格式

`plugin.json` 位于 ZIP / 目录根目录，也可位于唯一的外层目录下。

```json
{
  "format": "corki-resource-package",
  "version": 1,
  "name": "example-tools",
  "description": "资源备份示例",
  "skills": [{ "name": "example-skill", "path": "skills/example-skill" }],
  "mcp": [],
  "hooks": []
}
```

Skill 的路径需包含 `SKILL.md` 和配套文件，清单名称须与 Skill 的 name 一致。
MCP 项为 `{ "name": "服务名", "config": { ...原 MCP 配置 } }`。
Hook 项为 `{ "name": "名称", "event": "SessionStart", "matcher": "*", "handler": { ...原处理器 } }`。
资源路径遵循现有导入边界校验，不支持符号链接、路径穿越或重名文件。

MCP 配置和 Hook 处理器可使用 `${PACKAGE_ROOT}/resources/...` 引用包内资源；导入时转换为独立的实际资源目录。
Skill 类型 Hook 的 `path` 必须指向包内 Skill，例如 `${PACKAGE_ROOT}/resources/hook-skill/SKILL.md`。
带空格的命令路径应按所用 shell 正确引用。

该格式是 Corki 的资源备份格式，不等同于 Codex 的运行时插件格式。
