# Corki Desktop

Independent Electron desktop host. Application and protocol adapters live here;
Corki's runtime remains the authority for tools, model calls, permissions and
canonical conversation history. See [ARCHITECTURE.md](ARCHITECTURE.md) and the specification mapping in
[ACCEPTANCE.md](ACCEPTANCE.md).

## Development

Install the repository's Python dependencies in its development virtual
environment, then run in this directory:

```sh
npm ci --registry=https://registry.npmjs.org
npm start
```

If the package manager disables lifecycle scripts, allow Electron's installer
and esbuild's installer in your package manager policy before building.

The development host detects the repository virtual environment. Override with
`CORKI_PYTHON` pointing to an interpreter with Corki installed. No Manager server
or conversion gateway is started by this application.

## Checks

```sh
npm run typecheck
npm run build
npm test
```

The Node protocol tests use the repository's development Python (`.venv/bin/python`
on macOS, `.venv/Scripts/python.exe` on Windows), or `CORKI_TEST_PYTHON`. From the repository
root, run `.venv/bin/python -m pytest app/tests/test_bridge.py app/tests/test_protocol.py app/tests/test_content.py app/tests/test_live.py`.
Tests use disposable homes, never real user configuration or conversations.

For Windows PowerShell, use the virtual environment interpreter directly; shell
activation, WSL and Git Bash are not required. Install the Corki Python package
and its matching native execution bundle before starting an Agent. Merely
building Electron does not install that bundle. A missing compiler is reported
as a configuration error; it must not be bypassed with an unrestricted executor.

The Windows Electron development launcher also requires the
[Microsoft Visual C++ v14 Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
for Electron's native ZIP installer. Install it before `npm start`. On a
clean system without `VCRUNTIME140.dll`, that installer reports “Cannot find
native binding” even when its Windows binding is present; deleting the lockfile
does not supply the missing DLL. This prerequisite applies to installing the
Electron development launcher.

```powershell
# From the repository root, after installing the Python/core prerequisites:
$env:CORKI_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
& $env:CORKI_PYTHON -m pytest app/tests/test_protocol.py app/tests/test_live.py
Set-Location app
npm.cmd ci --registry=https://registry.npmjs.org
npm.cmd run typecheck
npm.cmd start
```

Use Node 22.12 or newer, as required by the pinned Electron/packager dependencies.
The current isolated Windows test environment uses Node 24 and Python 3.12.
Selected Windows 11 x64 end-to-end Agent and packaged application checks have
passed with an isolated fake model, including streaming, drafts, stop, reload,
history recovery, single-instance startup, window recovery and owned-child
cleanup on quit. Windows 10 and the full Windows test matrix remain
unverified; the commands above are development entry points rather than a
release guarantee.

Current Windows acceptance is limited to the CLI. Desktop platform compatibility
is reviewed in source and build configuration; further Windows desktop runtime
and UI acceptance is reserved for manual testing on a Windows computer. The latest
independent-runtime desktop smoke stopped while waiting for the initial working
directory, before submitting a model request. Its cause is still unconfirmed, so
earlier desktop results do not certify this installation path. Windows 10 is
outside the current acceptance scope.

After building, run `node tests/electron-smoke.mjs` here for the native macOS
end-to-end test with a local fake model. To test the packaged executable:

```sh
CORKI_TEST_PACKAGED=out/Corki-darwin-arm64/Corki.app/Contents/MacOS/Corki node tests/electron-smoke.mjs
```

`node tests/electron-interactions.mjs` exercises real shell/patch approvals,
sandbox-denied patch retry, and local stdio MCP form and URL confirmations. `node tests/electron-lifecycle.mjs` checks single-instance behavior,
full restart/draft recovery and window bounds. Set `CORKI_TEST_RECOVERY=1` for the
native smoke's renderer/backend crash cases. Set `CORKI_TEST_PYTHON` to test a
separately installed interpreter instead of the development environment.

`node tests/electron-attachments.mjs` covers actual attachment import, original
image delivery, draft/history recovery and Unicode text copying.
`node tests/electron-security.mjs` checks IPC boundaries and listener disposal.
`node tests/electron-layout.mjs` checks desktop sizes, 125% zoom, standard edit
shortcuts, sidebar persistence and the New Chat shortcut.
`node tests/electron-models.mjs` checks per-turn model selection, config removal,
session restoration and unchanged global defaults.
`node tests/electron-threads.mjs` verifies directory selection, actual execution
cwd, rename and stop-before-delete without deleting workspace files.
`node tests/electron-edge.mjs` checks invalid configuration, recovery, empty
model replies and explicit model failures using a local fixture server.
`node tests/electron-exit.mjs` checks quit confirmation and owned execution-child
cleanup without affecting an unrelated process. Browser-focused checks are
`node tests/scroll.mjs`, `node tests/interaction-ui.mjs` and
`node tests/markdown-ui.mjs`.

After packaging, `node tests/package-check.mjs` verifies the payload allowlist,
icon, startup without shell activation and that bridge imports do not modify the
application bundle.

`node tests/ui-smoke.mjs` performs a focused UI/selection check using Playwright
Chromium. Install the test browser with `npx playwright install chromium` inside
`app/`, or set `CORKI_TEST_BROWSER` to an existing Chromium/Chrome executable.
The same browser selection applies to the Markdown, scroll, interaction and
transcript UI scripts on Windows and macOS. Test profiles and screenshots stay in system temporary
directories, outside the repository.

## Packaging

`npm run package` creates an unsigned application for the current host platform
under `out/`. Packaging uses an allowlist staging directory; only the built UI,
Electron entry points, local resources and the Python bridge are included.
Windows icons are generated locally as 16/32/48/256px bitmap ICO frames using
the build-time `png-to-ico` dependency. The package check reads the executable's
actual Windows shell icon and compares it with the bundled Corki artwork.
Set `CORKI_TEST_PACKAGE_BUNDLE` to a copied application directory (`Corki.app`
on macOS) when running `node tests/package-check.mjs` against an installation.
The first version uses a separately installed Corki Python environment. It does
not silently include the developer's virtual environment. On first packaged
launch, use “选择 Python 环境” to select its interpreter; the absolute interpreter
path is saved in the user's desktop preferences, not in the application bundle.
Finder launches therefore do not require shell activation or repository cwd.
A macOS example for installing a locally built Corki wheel separately:

```sh
# From the repository root:
uv build --wheel --out-dir /tmp/corki-wheels
python3 -m venv ~/.corki/app/python
# Substitute the wheel filename produced above:
~/.corki/app/python/bin/python -m pip install /tmp/corki-wheels/corki-VERSION-PLATFORM.whl
```

Select `~/.corki/app/python/bin/python` in the application's interpreter chooser.
This installs the core independently; the Electron package contains no Python
virtual environment or user configuration. This flow has been tested with a
non-editable wheel in a disposable environment outside the checkout.

On Windows x64, first build and install the native bundles described in
[the permission backend](../native/sandbox/README.md) and
[file search](../native/file_search/README.md). Build the core wheel directly
with `uv build --wheel`; the source archive intentionally excludes generated
native assets. Install the resulting `win_amd64.whl` into a separate environment:

```powershell
$corkiPythonRoot = Join-Path $env:USERPROFILE '.corki\app\python'
python -m venv $corkiPythonRoot
# Substitute the actual wheel path produced by uv build --wheel:
& (Join-Path $corkiPythonRoot 'Scripts\python.exe') -m pip install 'C:\path\to\corki-VERSION-py3-none-win_amd64.whl'
```

In `app/`, run `npm.cmd run package`. Copy the entire
`out/Corki-win32-x64` directory to `%LOCALAPPDATA%\Programs\Corki`, then open
`Corki.exe`; copying the executable alone omits required Electron resources.
The manager's desktop quick launch recognizes this installation directory.
For another directory, configure `CORKI_DESKTOP_EXECUTABLE` with the full
`Corki.exe` path. A shortcut should target that executable and be named `Corki`.
Select the independent environment's `Scripts\python.exe` on first launch.
The application stores that choice outside the installation, so subsequent
Explorer launches do not require an activated terminal. Fully quit Corki before
replacing an existing application directory; keep user data separate.
These are the Windows installation steps to exercise in final acceptance;
the remaining unverified scope is listed below.
Code signing, publishing and auto-update are not part of this local build.

## Data and lifecycle

- Existing configuration: `~/.corki/settings.json` (read only from this host).
- Existing Agent history: `~/.corki/sessions/corki.db`.
- Desktop-only drafts, titles, submission receipts and interrupted reply text: `~/.corki/app/desktop.db`.
- Referenced upload blobs: `~/.corki/app/attachments/`. Unreferenced blobs older
  than one day are collected; core history retains the original message content.
- Window/Python selection: `~/.corki/app/window.json`.
- Electron profile: `~/.corki/app/electron/`.
- `CORKI_HOME` isolates all of the above for tests.

Archive removes a conversation from Recents while retaining canonical history.
Delete permanently removes its canonical rows, transcript, per-turn checkpoints,
goal state, thread notes and desktop drafts/title/receipts. Running tasks must
stop first; another process's writer lease blocks both operations. A minimal
deleted-thread ID tombstone prevents stale desktop submissions from recreating
the conversation. Confirmation can be suppressed for 24 hours across restarts.
Workspace files, independent fork copies and shared consolidated memories are
not deleted. Unreferenced upload staging blobs follow the one-day cleanup above.
SQLite reuses freed pages; deletion does not promise immediate database file
shrinkage or forensic erasure. Ancillary cleanup precedes canonical deletion,
so failed/interrupted cleanup can be retried; it is not a cross-file crash-atomic
transaction.
Stopped replies retain their received text and an explicit stopped marker, including
after reopening the application. Incomplete text is a desktop display supplement,
not a fabricated completed model-history item; deleting the conversation removes it.
Closing a macOS window leaves execution with the host. Quitting asks before
stopping running tasks. A lost response is reconciled through a stable request
ID; unknown outcomes are never automatically replayed.

The host retains up to eight idle Runtime instances where possible. Active turns,
pending approvals and background terminals prevent retirement. Retired sessions
reopen through canonical history; transient Runtime/session approvals and MCP
connections are recreated rather than copied into a second permission system.

## Validated scope and limits

The macOS arm64 desktop implementation and core acceptance flows are verified.
Python adapter/protocol/content tests, Node protocol/state tests, native Electron
Agent execution and independent packaged execution all passed. See
[ACCEPTANCE.md](ACCEPTANCE.md) for the requirement mapping and
[VALIDATION.md](VALIDATION.md) for specific evidence.

- The package is unsigned. Windows has targeted native test coverage, but full
  Agent and packaged acceptance is not complete. Linux has not been validated.
- Packaged execution requires a separately installed Corki Python interpreter;
  select it on first launch. It does not include a Python distribution.
- Model connectivity depends on the user's configured provider. Tests used local
  deterministic models, not private prompts or paid production endpoints.
- Image/text attachments are supported with a 1 MB per-file and 1.2 MB import
  batch limit, up to eight attachments; text files must be UTF-8. PDFs, OCR,
  upload services and remote image loading are not part of this version.
- Permissions inherit Corki settings. Configuration management remains in the
  existing manager. Choosing a model here changes the session, not global defaults.
- Stop invokes core cancellation; an interrupted goal follows the core's pause
  behavior. Input during execution follows the steering/queue rules documented
  below; it does not silently start a second Agent loop.
- Archive retains history; Delete removes session data without deleting project files.
- Unknown crash outcomes require reconciliation; the app does not guarantee
  exactly-once side effects or automatically rerun tasks after a backend crash.

## Reference design and core changes

The narrow sidebar, New Chat layout, bottom composer, isolated preload,
composition handling, subscription cleanup and scroll anchoring follow patterns
observed in the locally installed reference application. This is an independent
implementation; no proprietary bundle, account logic or cloud integration was
copied. Corki's local orange visual identity and existing resources are retained.

Desktop host code, tests, resources and build configuration are inside app/.
The general hard-delete store is in src/corki/storage/thread_delete.py: the
existing archive interface retains data and cannot implement physical deletion.
This store has no Electron/UI dependencies and uses existing writer leases.
A separately installed Corki runtime must include this module (upgrade/reinstall
the current project package when updating the desktop host).
Other pre-existing changes in the repository are not part of this delivery.

The desktop accepts `--corki-directory /absolute/workspace` on first launch and
through its single-instance activation. The manager's desktop quick-launch mode
uses this entry point. The path initializes a new-chat draft, never changes the
working directory of an existing Agent session, and never submits a prompt.

### 运行中追加消息

桌面端复用 Runtime 的 realtime/steer 接口，不启动第二个 Agent 循环。运行期间可以继续输入并按 Enter 提交，统一补充当前任务，不展示追加方式选择，也不读取旧版保存的排队偏好。Shift+Enter 换行，中文输入法确认不会发送。

参考本机 Codex 桌面包的 turn coordinator、`hasFinalAnswer` 和 queued-message-list：模型已经输出非空 `final_answer` 时，普通追加自动排队；队列的“立即补充”可以显式绕过此条件。模型未提供 phase 时，不猜测文字是否属于最终回答，按补充当前任务处理。不同模型/推理设置的消息始终等待下一轮，不修改当前执行。

队列支持编辑、移除、排序和手动发送。停止、失败会暂停队列；取消前已接受但未写入核心历史的补充输入也退回暂停队列。重启后保留消息但不自动执行；发送结果未知的消息必须先核对，不能直接重试。队列存于 `app/desktop.db`，核心消息仍只有 Corki 原有会话库这一份事实来源。归档或删除会话会清除其未发送队列；排队附件不会被草稿附件清理器误删。

验证命令：

```sh
# 仓库根目录
.venv/bin/python -m pytest app/tests/test_followups.py tests/integration/test_realtime_steering.py
# app 目录
npm test
node tests/electron-followups.mjs
```

Electron 测试使用独立临时 CORKI_HOME 和本机假模型，不读取真实用户配置。提取的第三方安装包、测试截图和运行日志只放系统临时目录。

### 设置与管理服务

桌面右上角「设置」先检测 `127.0.0.1:17002` 的 Corki 管理页面，已运行时直接打开；未运行时使用桌面配置的同一个 Python 环境启动管理服务，显示启动弹窗，待服务就绪后再打开浏览器。启动失败支持重试；端口被其他服务占用时明确报错。

打包资源包含管理服务入口、静态页面与快速配置模板，不依赖源码目录。管理服务沿用当前 `CORKI_HOME`，默认工作目录为该目录下的 `main/default`，启动日志位于 `CORKI_HOME/manager/service.log`。服务启动后独立运行，关闭桌面窗口不会关闭管理服务。Python 环境仍须安装匹配版本的 Corki；缺少依赖时会提示启动失败。


### 模型连接与默认项

桌面窗口重新获得焦点、发送消息、切换已有会话时不重新读取配置。配置目录在启动、新会话、主动刷新或选择工作目录时加载；已有运行时使用已加载的连接信息。默认供应商/模型缺失或不可用时，按配置顺序使用第一个已启用供应商的第一个模型，不改写全局默认项。

桌面调用实际 HTTP 请求连续失败 10 次，或连续 10 秒没有收到响应行时，显示不占布局空间的“模型调用失败，重试中”提示栏；连续失败 20 次或连续 20 秒无响应时，以普通模型回复显示“模型调用失败”并结束该轮（底层仍记录为失败）。收到响应行会重置无响应计时，成功完成一份模型响应会清零失败计数。工具运行、审批与用户补充信息等待不计入时间。已生成的回复保留，失败状态和部分回复可以从历史恢复。此策略位于桌面适配层，CLI 的重试策略不变。
