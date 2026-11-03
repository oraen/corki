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
