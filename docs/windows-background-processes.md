# Windows background processes

Background operations must not allocate a visible Windows console. Opening the
CLI from the Manager is an explicit interactive-terminal action and remains the
only intentional `CREATE_NEW_CONSOLE` path.

Use `corki.platforms.processes.background_process_options()` for synchronous
background commands and independent workers. Use its `create_subprocess_exec()`
for owned asynchronous tools: it hides the console while retaining suspended
startup, Windows Job containment, cancellation and descendant cleanup.

`CREATE_NO_WINDOW` must not be combined with `DETACHED_PROCESS`: Windows ignores
the hidden flag in that combination, and a virtual-environment redirector can
give the underlying Python process a visible console. Redirected, console-free
Windows workers can outlive their launching process without detaching. POSIX
workers continue to use `start_new_session=True`.

| Runtime path | Console handling |
| --- | --- |
| Manager → channel control | Shared synchronous background options |
| Channel control → gateway / package worker | Shared synchronous background options |
| Connector installer → Node / native batch helper → npm | Shared synchronous background options, inherited by descendants |
| Channel connectors, MCP stdio and header helpers | Owned asynchronous process helper |
| Agent command tools, execution helpers, terminal adapters, custom tools and hooks | Owned asynchronous process helper |
| Project Git probes and memory Git baselines | Shared synchronous background options |
| Code-mode isolated worker | Shared synchronous background options; existing worker cleanup retained |
| Scheduled task worker | Shared synchronous background options; independent lifetime retained |
| Directory picker | Shared synchronous background options; the requested GUI dialog remains visible |
| Subscription adaptor (independently installed) | Its existing standalone `CREATE_NO_WINDOW` paths |
| Electron bridge and Manager launcher | `windowsHide: true`; the Windows Manager launcher does not detach |
| Manager → interactive CLI | Explicit new console, requested by the user |

The native batch helper inherits the console-free state. The native interactive
terminal backend uses ConPTY instead of opening a separate console window.
macOS-only AppleScript/keychain commands and developer build commands are not
Windows background runtime paths.

`tests/integration/test_windows_background.py` exercises real Windows children
and venv grandchildren, observes transient visible console windows, installs and
uninstalls a connector through the real Manager control boundary, runs real npm
with an offline local package, and checks scheduler and code-mode workers. It
uses temporary data, without changing real channel credentials or connections.

Run it with the development dependencies and optional code-mode engine installed:

```powershell
uv sync --frozen --extra dev --extra code-mode
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/integration/test_windows_background.py
```
