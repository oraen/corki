// Evaluated only inside a fresh QuickJS context with no module loader or I/O globals.
(function (configuration) {
    const emitNative = globalThis.__corki_emit;
    delete globalThis.__corki_emit;
    for (const name of ["console", "Atomics", "SharedArrayBuffer", "WebAssembly"]) {
        delete globalThis[name];
    }
    const parse = JSON.parse, stringify = JSON.stringify;
    const emit = value => emitNative(stringify(value));
    const pending = new Map(), timers = new Map();
    const values = new Map(Object.entries(parse(configuration).stored));
    const writes = Object.create(null);
    let sequence = 0, exited = false, done = false;
    const exitSentinel = "corki:cell-exit";
    const render = value => {
        if (value === null || typeof value !== "object") return String(value);
        const result = stringify(value);
        return result === undefined ? String(value) : result;
    };
    const finish = error => {
        if (done) return;
        done = true;
        emit({type: "complete", error: exited && error === exitSentinel ? null : error == null ? null : String(error), writes});
    };
    globalThis.text = value => { emit({type: "text", text: render(value)}); };
    const detailValue = value => {
        if (value == null) return undefined;
        if (typeof value !== "string") throw new TypeError("image detail must be a string when provided");
        return value;
    };
    const media = (value, kind) => {
        const key = kind + "_url";
        let url, detail;
        if (typeof value === "string") url = value;
        else if (value !== null && typeof value === "object" && !Array.isArray(value)) {
            const supplied = value[key];
            if (supplied !== undefined) {
                url = supplied;
                if (typeof url !== "string") throw new TypeError(kind + " expects a string URL");
                if (kind === "image") detail = detailValue(value.detail);
            } else {
                const block = parse(stringify(value));
                if (!block || block.type !== kind) throw new TypeError(kind + " only accepts MCP " + kind + " blocks");
                if (typeof block.data !== "string" || !block.data) throw new TypeError(kind + " expected MCP data");
                const candidate = Object.hasOwn(block, "mimeType") ? block.mimeType : block.mime_type;
                const mime = typeof candidate === "string" && candidate ? candidate : "application/octet-stream";
                url = /^data:/i.test(block.data) ? block.data : "data:" + mime + ";base64," + block.data;
                const hint = block._meta?.["codex/imageDetail"];
                if (kind === "image" && ["auto", "low", "high", "original"].includes(hint)) detail = hint;
            }
        } else throw new TypeError(kind + " expects a data URL, URL object or MCP block");
        if (!url || !/^data:/i.test(url)) throw new TypeError(kind + " requires a data URL; remote URLs are not supported");
        return {type: kind, data_url: url, ...(kind === "image" ? {detail} : {})};
    };
    const outputImage = (value, override) => {
        override = detailValue(override);
        const item = media(value, "image");
        item.detail = (override ?? item.detail ?? "high").toLowerCase();
        if (!["auto", "low", "high", "original"].includes(item.detail)) throw new TypeError("image detail must be one of: auto, low, high, original");
        return item;
    };
    globalThis.image = (value, detail) => { emit({type: "content", item: outputImage(value, detail)}); };
    globalThis.audio = value => { emit({type: "content", item: media(value, "audio")}); };
    globalThis.generatedImage = value => {
        if (value === null || typeof value !== "object") throw new TypeError("generatedImage expects an image generation result object");
        const hint = value.output_hint;
        if (hint !== undefined && typeof hint !== "string") throw new TypeError("generatedImage output_hint must be a string when provided");
        const item = outputImage(value);
        emit({type: "content", item});
        if (hint !== undefined) emit({type: "text", text: hint});
    };
    globalThis.notify = value => {
        const text = render(value);
        if (!text.trim()) throw new TypeError("notify expects non-empty text");
        emit({type: "notify", text});
    };
    globalThis.store = (key, value) => {
        key = String(key);
        const serialized = stringify(value);
        if (serialized === undefined) throw new TypeError("Only serializable values can be stored");
        const copied = parse(serialized);
        values.set(key, copied);
        writes[key] = copied;
    };
    globalThis.load = key => values.has(String(key)) ? parse(stringify(values.get(String(key)))) : undefined;
    globalThis.yield_control = () => { emit({type: "yield"}); };
    globalThis.exit = () => { exited = true; throw exitSentinel; };
    globalThis.setTimeout = (callback, delay = 0) => {
        if (typeof callback !== "function") throw new TypeError("setTimeout requires a callback");
        delay = Number(delay);
        delay = !Number.isFinite(delay) || delay <= 0 ? 0 : Math.min(Math.trunc(delay), 2**64 - 1);
        const id = String(++sequence);
        timers.set(id, callback);
        emit({type: "timer", id, delay});
        return Number(id);
    };
    globalThis.clearTimeout = id => {
        id = Number(id);
        if (!Number.isFinite(id) || id <= 0) return;
        id = Math.trunc(id);
        timers.delete(String(id));
        emit({type: "clear_timer", id: String(id)});
    };
    const definitions = parse(configuration).tools;
    globalThis.ALL_TOOLS = definitions.map(({name, description}) => ({name, description}));
    globalThis.tools = Object.create(null);
    for (const tool of definitions) {
        tools[tool.name] = input => {
            if (tool.kind === "freeform" ? typeof input !== "string" : input !== undefined && (input === null || typeof input !== "object" || Array.isArray(input))) {
                throw new TypeError(tool.kind === "freeform" ? "tool expects a string input" : "tool expects a JSON object for arguments");
            }
            const id = String(++sequence);
            return new Promise((resolve, reject) => {
                pending.set(id, {resolve, reject});
                emit({type: "tool", id, name: tool.name, input: input === undefined ? {} : input});
            });
        };
    }
    // This callback is retained only by Python, not published in the JS global object.
    return (command, promise) => {
        if (command === "main") {
            Promise.resolve(promise).then(() => finish(null), finish);
            return;
        }
        const value = parse(command);
        if (value.type === "timer") {
            const callback = timers.get(value.id);
            timers.delete(value.id);
            if (callback) { try { callback(); } catch (error) { finish(error); } }
        } else if (value.type === "response") {
            const resolver = pending.get(value.id);
            pending.delete(value.id);
            if (resolver) {
                if (value.error !== undefined) resolver.reject(value.error);
                else resolver.resolve(value.result);
            }
        }
    };
})
