# Corki

Corki 是一个可在本地运行的 AI Agent，提供命令行、桌面应用和管理页面。
它可以结合代码、文件和工具完成开发、问题排查及日常任务，并支持通过消息渠道远程使用。

## 主要功能

- **Agent 对话**：流式回复、工具调用、执行审批、任务停止与历史恢复。
- **模型接入**：多个供应商与模型配置，支持 OpenAI 兼容接口。
- **能力扩展**：Skills、MCP、自定义工具和 Hooks。
- **知识与经验**：知识库、记忆及可选的自进化。
- **自动执行**：持续目标（Goal）、子 Agent 和定时任务。
- **消息接入**：钉钉、飞书、微信、Telegram、WhatsApp 及自定义连接器。
- **本地管理**：模型、技能、任务、会话、配置、日志和数据库管理。

## 安装

以下命令在项目根目录执行，适用于 macOS 源码开发环境。需要 Python 3.11+；
桌面应用还需要 Node.js 22.12+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

运行 Agent 前，还需按[原生执行后端说明](native/sandbox/README.md)完成安装。
供应商和模型可在管理页面的“快速配置”中设置。

## 启动

### 命令行

在需要工作的目录中执行：

```bash
corki
```

当前目录即工作目录。使用 `corki resume` 恢复会话。

### 管理页面

在项目根目录执行，然后打开 <http://127.0.0.1:17002/>：

```bash
./manager/bin/start.sh
```

### 桌面应用

在项目根目录执行：

```bash
cd app
npm ci
npm start
```

已打包的版本可直接打开 Corki 应用。

## 配置与更多说明

默认配置为 `~/.corki/settings.json`，用户数据存放在 `~/.corki/`；
可通过 `CORKI_HOME` 指定其他数据目录。

- [桌面应用](app/README.md)
- [管理页面与 Windows 启动方式](manager/README.md)
- [消息转发网关](gateway/README.md)
- [可选的套餐适配网关](adaptor/README.md)
