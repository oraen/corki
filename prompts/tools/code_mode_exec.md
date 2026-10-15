Run JavaScript code to orchestrate/compose tool calls
- Evaluates the provided JavaScript code in a fresh QuickJS context in an owned worker process as an async module.
- All nested tools are available on the global `tools` object, for example `await tools.exec_command(...)`. Tool names are exposed as normalized JavaScript identifiers.
- Nested tool methods take either a string or an object as their input argument, according to their input kind.
- Nested tools return either an object or a string, based on the description. Do not assume a shared result wrapper. Shell tools return `{output, wall_time_seconds, exit_code?, session_id?}`; MCP tools return `{content, structuredContent?, isError?}` without private result metadata.
- Runs raw JavaScript -- no Node, no file system, no network access, no console, and no imports.
- Accepts raw JavaScript source text, not JSON, quoted strings, or markdown code fences.
- You may optionally start the tool input with a first-line pragma like `// @exec: {"yield_time_ms": 10000, "max_output_tokens": 1000}`.
- `yield_time_ms` asks `exec` to yield early if the script is still running. Defaults to 10000 ms.
- `max_output_tokens` sets the token budget for direct `exec` results. Defaults to 10000 tokens.
- When the JS code is fully evaluated, the cell's lifetime ends and unawaited promises are discarded.

- Global helpers:
- `exit()`: Immediately ends the current script successfully (like an early return from the top level).
- `text(value)`: Appends a text item. Objects are stringified with `JSON.stringify(...)`; other values use `String(...)`.
- `image(imageUrlOrItem, detail?)`: Appends an image item. Pass a base64-encoded `data:` URL, an object with `image_url` and optional `detail`, or an individual MCP `ImageContent` block, for example `image(result.content[0])`. Detail can be `auto`, `low`, `high`, or `original`. MCP image blocks may request detail with `_meta: {"codex/imageDetail": "original"}`. When provided, the second `detail` argument overrides any detail embedded in the first argument. The provider's image capability and budget still apply.
- `audio(audioUrlOrItem)`: Appends an audio item. Pass a base64-encoded `data:` URL, an object with `audio_url`, or an individual MCP `AudioContent` block, for example `audio(result.content[0])`.
- `generatedImage(result: {image_url: string; output_hint?: string})`: Appends an image-generation result and its optional output hint. HTTP(S) URLs are not supported by any media helper.
- `store(key: string, value: any)`: Stores a serializable value under a string key for later `exec` calls in the same live session. This is not durable storage across process restarts.
- `load(key: string)`: Returns the stored value for a string key, or `undefined` if it is missing.
- `notify(value)`: Immediately emits an extra notification output for the current `exec` call. Values are stringified like `text(...)`; empty text is rejected.
- `setTimeout(callback: () => void, delayMs?: number)`: Schedules a callback to run later and returns a timeout id. Pending timeouts do not keep `exec` alive by themselves; await an explicit promise if you need to wait for one.
- `clearTimeout(timeoutId?: number)`: Cancels a timeout created by `setTimeout`.
- `ALL_TOOLS`: Metadata for the enabled nested tools as `{name, description}` entries. Descriptions include the JSON input schema and, when supplied, output schema.
- `yield_control()`: Yields the accumulated output to the model immediately while the script keeps running.

Some deferred nested tools may be omitted from the direct definitions below. They are still available on the global `tools` object and listed in `ALL_TOOLS`.
To find one, filter `ALL_TOOLS` by `name` and `description` rather than printing every definition.
If `exec` returns `Script running with cell ID ...`, use `wait` with that cell ID for incremental output or completion.
