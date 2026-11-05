# 套餐适配网关

独立于 Corki 主包，将本机已有的 Codex / Qoder 登录接入 OpenAI 兼容接口。

## 目录

```text
adaptor/
  src/
    common.py           公共限制、TLS 和错误类型
    gateway.py          HTTP 服务与供应商路由
    control.py          网关生命周期管理
    windows_service.py  Windows 服务适配
    openai/             Codex 凭据、Responses 转发与协议转换
    qoder/              Qoder 凭据、模型与流处理
  bin/                  安装、启动和停止脚本
  tests/
  pyproject.toml
  uv.lock
```

所有 Python 源码直接位于 `src/`，安装后使用包名 `corki_adaptor`。
外层没有供应商源码目录或旧 Python 兼容包。

## 安装与运行

```sh
./adaptor/bin/install.sh
./adaptor/bin/start.sh
./adaptor/bin/stop.sh
```

Windows PowerShell 使用 `adaptor/bin/install.ps1`、`start.ps1` 和 `stop.ps1`。
公共依赖、锁文件和虚拟环境均位于 `adaptor/`。不要求启动 Corki 或管理页面。

```sh
uv sync --project adaptor --frozen
adaptor/.venv/bin/python -m corki_adaptor.control status --home ~/.corki
adaptor/.venv/bin/python -m pytest adaptor/tests
```

HTTP 地址继续使用 `/openai`、`/qoder` 前缀；现有客户端配置不需要迁移。
凭据仍在运行时读取，不复制到源码、安装包或配置示例。

具体接入示例见 [使用指南](QUICKSTART.md)。

## 管理页默认模型

控制接口 `check --provider openai|qoder` 在检查登录信息后返回该适配器的模型目录。
仅在管理页首次创建对应适配供应商时初始化模型。之后重新启用或重启网关不会补齐、删除或重写模型，用户删除的模型也不会恢复。

- OpenAI：仅默认加入 6.1、6 系列，目前为 `gpt-6.1-sol`、`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`。
- Qoder：仅加入当前转发链路已验证可用的 `ultimate`、`Qwen`、`Kimi-K2.8-Preview`、`GLM-5.3`、`DeepSeek-V4-Pro`。

目录是代码维护的默认值，不保证每个账号套餐都能调用全部模型。
模型窗口默认使用 `min(512K, 模型最大窗口)`，已配置的窗口不被覆盖。
