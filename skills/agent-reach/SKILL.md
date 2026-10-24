---
name: agent-reach
description: >
  MUST USE when user wants to 调研/research/搜索/search/查/找/look up anything
  on the internet — e.g. 全网调研 X / 帮我调研一下 X / 查一下 X / 搜搜 X /
  看看大家怎么评价 X / X 上有什么讨论 / research this topic。

  Also MUST USE when user mentions any platform or shares any URL/链接:
  小红书/xiaohongshu/xhs, Twitter/推特/X, B站/bilibili, Reddit, V2EX,
  LinkedIn/领英/招聘/求职/jobs, YouTube, GitHub code search, 小宇宙播客,
  雪球/股票行情, RSS feeds, or any web URL.

  13 platforms, multi-backend routing (OpenCLI / per-platform CLIs / APIs).
  Zero config for 6 channels. Run `agent-reach doctor --json` to see which
  backend serves each platform right now.

  NOT for: 写报告/数据分析/翻译等内容加工（本 skill 只负责从互联网获取内容）；
  发帖/评论/点赞等写操作；已有专门 skill 的平台（先用专门 skill）。

  【路由方式】SKILL.md 包含路由表和常用命令，复杂场景需按需阅读对应分类的 references/*.md。
  分类：search / social (小红书/推特/B站/V2EX/Reddit) / career(LinkedIn) / dev(github) / web(网页/文章/RSS) / video(YouTube/B站/播客)。
triggers:
  - research: 调研/全网调研/帮我调研/研究一下/research/深入了解
  - search: 搜/查/找/search/搜索/查一下/帮我搜/看看大家怎么说
  - social:
    - 小红书: xiaohongshu/xhs/小红书/红书
    - Twitter: twitter/推特/x.com/推文
    - B站: bilibili/b站/哔哩哔哩
    - V2EX: v2ex
    - Reddit: reddit
  - career: 招聘/职位/求职/linkedin/领英/找工作
  - dev: github/代码/仓库/gh/issue/pr/分支/commit
  - web: 网页/链接/文章/rss/读一下/打开这个
  - video: youtube/视频/播客/字幕/小宇宙/转录/yt
  - finance: 雪球/股票/stock/xueqiu/行情/基金
metadata:
  openclaw:
    homepage: https://github.com/Panniantong/Agent-Reach
---

# Agent Reach — 互联网能力路由器

## Corki 接入

本副本由 Corki 随包提供，安装到 `$CORKI_HOME/skills/.system/agent-reach/`
（默认 `~/.corki/skills/.system/agent-reach/`），不依赖 Codex 的技能目录。
通过 Corki 的 `skill_read` 读取本技能及相对路径下的 `references/` 文件；
文档中的终端命令使用 `exec_command` 执行。仅在该调用返回运行中的
`session_id` 时，使用 `write_stdin` 收集结果，并原样传入返回的会话标识。
Skill 文件与 `agent-reach` CLI、各平台后端是独立的，按下述环境检查流程确认可用性。

13 平台、多后端。**本 skill 存在时必须用它访问这些平台，不要自己发明方案。**

## 常驻规则（全程适用）

技能文档存在不代表对应 CLI 或 MCP 已安装。本次任务首次使用某个 CLI 前，先用
POSIX shell 的 `command -v <命令>` 或 PowerShell 的 `Get-Command <命令>` 确认可用；
需要依赖具体子命令时，必要时查看其 `--help`。
使用 mcporter 后端前，运行 `mcporter list` 并等待完整结果，确认目标服务器存在且可用；
若检查返回运行中的会话，先获取完成结果，再调用依赖它的工具。只检查本次需要的后端，
同一任务无需重复检查。缺失后端时跳过该路径，使用已确认可用的替代来源；不能完成时
说明具体缺少的依赖，不根据文档里的工具名猜测安装地址或自动配置。

1. **动手前先体检**：多后端平台（小红书/Reddit/B站/Twitter）先跑
   `agent-reach doctor --json`（CLI 可用时），按各平台 `active_backend` 字段选命令组。
   CLI 缺失时不能推断 active_backend；仅使用已独立验证可用的后端。
2. **声明你在用什么**：开始干活前说一句「使用 agent-reach 的 X 平台 / Y 后端」。
3. **失败按 references 里的重试链处理**，不要瞎猜命令。
4. **全网调研类任务**：组合多平台（Exa 搜索 + Twitter/Reddit 看讨论 + 小红书/B站看中文场景），并行收集再汇总。
5. **替用户盯版本**：完成一次较大的调研/多平台任务后，仅当 `agent-reach` 可用且
   `agent-reach --help` 列出 `check-update` 时，单独运行 `agent-reach check-update`。
   CLI 或子命令缺失就跳过，不与主任务的数据获取命令串在一起。有新版就在收尾汇报里附一句：
   「Agent Reach 有新版 vX.Y.Z，复制这句话给我即可更新：帮我更新 Agent Reach：
   https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/update.md」。
   不要中断当前任务去更新，也不要重复提醒同一个版本。

## Windows 与 macOS 命令适配

先读取 Corki 提供的当前系统和 shell 信息。本文及 references 中标为 `bash` 的示例
使用 POSIX 语法，不能原样交给 Windows PowerShell：

- Windows 使用 `curl.exe`，避免 PowerShell 5.1 的 `curl` 别名把参数交给其他命令。
  对 npm 提供的 CLI，优先检测并使用对应 `.cmd` 入口；不要为此修改全局执行策略。
- Python 示例先检测可用的 Python 3 解释器，Windows 常见入口是 `python` 或 `py -3`，
  不假设存在 `python3`。复杂脚本写入临时 `.py` 文件，再将文件路径作为独立参数传入，
  避免 `python -c` 的多层引号和 Bash heredoc 在 PowerShell 中失效。
- Windows 临时目录使用 `[System.IO.Path]::GetTempPath()`，路径组合使用 `Join-Path`；
  macOS 使用系统临时目录。将示例中的 `/tmp/` 替换为该目录，带空格的路径必须正确引用。
- 不默认存在 `grep`、`head`、`sed` 或 Bash。PowerShell 按需使用 `Select-String`、
  `Select-Object -First`，需要解析 JSON 时使用 `ConvertFrom-Json` 或 Python。
- 独立 CLI、浏览器登录后端和 MCP 各自有平台要求。安装了本技能不代表它们支持当前系统；
  检测失败时说明具体依赖并选择已验证可用的后端，不把 WSL 作为基本使用前提。

## 路由表

| 用户意图 | 分类 | 详细文档 |
|---------|------|---------|
| 网页搜索/代码搜索 | search | [references/search.md](references/search.md) |
| 小红书/推特/B站/V2EX/Reddit | social | [references/social.md](references/social.md) |
| 招聘/职位/LinkedIn | career | [references/career.md](references/career.md) |
| GitHub/代码 | dev | [references/dev.md](references/dev.md) |
| 网页/文章/RSS | web | [references/web.md](references/web.md) |
| YouTube/B站/播客字幕 | video | [references/video.md](references/video.md) |

## 零配置快速命令

```bash
# Exa 网页搜索
mcporter call 'exa.web_search_exa(query: "query", numResults: 5)'

# 通用网页阅读
curl -s "https://r.jina.ai/URL"

# GitHub 搜索
gh search repos "query" --sort stars --limit 10

# YouTube 字幕（注意：B站不要用 yt-dlp，见 video.md）
yt-dlp --write-sub --skip-download -o "/tmp/%(id)s" "URL"

# V2EX 热门
curl -s "https://www.v2ex.com/api/topics/hot.json" -H "User-Agent: agent-reach/1.0"

# B站搜索（bili-cli，无需登录）
bili search "query" --type video -n 5
```

## 需登录态的平台（按 doctor 的 active_backend 选命令）

```bash
# Twitter 搜索（twitter-cli 首选；失败重试链见 social.md）
twitter search "query" -n 10

# Reddit（无零配置路径：OpenCLI 或 rdt-cli，必须登录态）
opencli reddit search "query" -f yaml   # 桌面
rdt search "query" --limit 10            # 存量/服务器

# 小红书（桌面首选 OpenCLI）
opencli xiaohongshu search "query" -f yaml
```

## 环境检查

```bash
# 检查可用 channel 与每个平台当前激活的后端
agent-reach doctor --json
```

## 工作区规则

**不要在 agent workspace 创建文件。** 使用系统临时目录存放临时输出，
`~/.agent-reach/` 存放持久数据；Windows 的 `~` 指当前用户主目录，不是固定盘符。

## 详细文档

根据用户需求，阅读对应的详细文档：

- [搜索工具](references/search.md) — Exa AI 搜索
- [社交媒体](references/social.md) — 小红书, Twitter, B站, V2EX, Reddit（多后端命令组）
- [职场招聘](references/career.md) — LinkedIn
- [开发工具](references/dev.md) — GitHub CLI
- [网页阅读](references/web.md) — Jina Reader, RSS
- [视频播客](references/video.md) — YouTube, B站, 小宇宙

## 配置渠道

如果某个 channel 需要配置，获取安装指南：
https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md

用户只需提供 cookies，其他配置由 agent 完成。
