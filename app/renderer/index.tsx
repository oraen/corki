import { displayMessages } from "./pending-inputs";
import { TranscriptView } from "./transcript-view";
import { transcriptEntries } from "./transcript";
import React, {
  useCallback,
  useMemo,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import type { Catalog, HostEvent, Attachment } from "../shared/protocol";
import { assertThreadManagement } from "../shared/protocol";
import {
  emptyConversation,
  conversationFromSnapshot,
  freshDraft,
  reduceEvent,
  mergeSnapshot,
  mergeOlderMessages,
  awaitingFirstReply,
  type Conversation,
  type Draft,
} from "./state";
import { Icon, Modal, Interaction } from "./components";
import { SnapshotRefresh } from "./refresh";
import { EventBuffer } from "./event-buffer";
import { modelIcon } from "./model-icons";
import { HistorySection, WorkspaceHistory } from "./sidebar-history";
import { WindowControls } from "./window-controls";
import { SessionSettingsDialog, resolveSessionDraft } from "./session-settings";
import "./style.css";
import "highlight.js/styles/github.css";
const api = window.corki;
function App() {
  const [threads, setThreads] = useState<any[]>([]),
    [nextThreads, setNextThreads] = useState<number | null>(null);
  const [directories, setDirectories] = useState<
    { path: string; name: string }[]
  >([]);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [selected, setSelected] = useState("new"),
    [conversations, setConversations] = useState<Record<string, Conversation>>(
      {},
    );
  const [drafts, setDrafts] = useState<Record<string, Draft>>({}),
    [catalog, setCatalog] = useState<Catalog>({ valid: false, providers: [] });
  const [ready, setReady] = useState(false),
    [offline, setOffline] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState<Record<string, boolean>>({});
  const [collapsed, setCollapsed] = useState(false),
    [modal, setModal] = useState<
      "directory" | "rename" | "delete" | "archive" | null
    >(null),
    [target, setTarget] = useState<any>(null),
    [field, setField] = useState(""),
    [modalError, setModalError] = useState(""),
    [recent, setRecent] = useState<string[]>([]);
  const [skipDeleteReminder, setSkipDeleteReminder] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [settingsThread, setSettingsThread] = useState<string | null>(null);
  const [settingsTitle, setSettingsTitle] = useState("新会话");
  const threadTitles = useRef(new Map<string, string>());
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [managerState, setManagerState] = useState<"closed" | "starting" | "failed">("closed");
  const [managerError, setManagerError] = useState("");
  const [openingManager, setOpeningManager] = useState(false);
  const openingManagerRef = useRef(false);
  async function openManager() {
    if (openingManagerRef.current) return;
    openingManagerRef.current = true;
    setOpeningManager(true);
    setManagerError("");
    try {
      await api.openManager(() => setManagerState("starting"));
      setManagerState("closed");
    } catch (error) {
      setManagerError(error instanceof Error ? error.message : "请稍后重试");
      setManagerState("failed");
    } finally {
      openingManagerRef.current = false;
      setOpeningManager(false);
    }
  }
  const removingRef = useRef(false);
  async function removeThread(thread: any, archive = false, skip = false) {
    if (removingRef.current) return;
    removingRef.current = true;
    setRemoving(true);
    try {
      assertThreadManagement(
        await api.request("initialize", { protocolVersion: 1 }),
      );
      await saveChains.current.get(thread.id)?.catch(() => {});
      await api.request(archive ? "threads.archive" : "threads.delete", {
        threadId: thread.id,
      });
      snapshotLoads.current.delete(thread.id);
      setConversations((previous) => {
        const next = { ...previous };
        delete next[thread.id];
        return next;
      });
      const nextDrafts = { ...draftRef.current };
      delete nextDrafts[thread.id];
      draftRef.current = nextDrafts;
      setDrafts(nextDrafts);
      setModal(null);
      if (selectedRef.current === thread.id) await newChat();
      await list();
      if (skip && !archive) {
        await api.request("state.write", {
          key: "deleteConfirmationUntil",
          value: Date.now() + 86400000,
        });
      }
    } catch (e) {
      if (modal) reportModal(e);
      else report(e);
    } finally {
      removingRef.current = false;
      setRemoving(false);
    }
  }
  async function requestRemoval(thread: any, archive = false) {
    if (removingRef.current) return;
    try {
      assertThreadManagement(
        await api.request("initialize", { protocolVersion: 1 }),
      );
    } catch (e) {
      report(e);
      return;
    }
    setTarget(thread);
    setSkipDeleteReminder(false);
    if (thread.running) {
      setModal(archive ? "archive" : "delete");
      return;
    }
    if (archive) {
      await removeThread(thread, true);
      return;
    }
    try {
      const until = await api.request("state.read", {
        key: "deleteConfirmationUntil",
      });
      if (typeof until === "number" && until > Date.now())
        await removeThread(thread);
      else setModal("delete");
    } catch (e) {
      report(e);
    }
  }
  const [attachmentPreview, setAttachmentPreview] = useState<{
    name: string;
    type: string;
    data: string;
  } | null>(null);
  async function previewAttachment(attachment: Attachment) {
    try {
      const data =
        attachment.type === "text" && attachment.assetId
          ? (await api.request("assets.read", { assetId: attachment.assetId }))
              .text
          : attachment.data;
      setAttachmentPreview({
        name: attachment.name,
        type: attachment.type,
        data,
      });
    } catch (error) {
      report(error);
    }
  }
  const [atBottom, setAtBottom] = useState(true),
    [loadingOlder, setLoadingOlder] = useState<string | null>(null);
  const [historyReached, setHistoryReached] = useState<string | null>(null);
  const [historyScrollable, setHistoryScrollable] = useState(false);
  const olderRequest = useRef<string | null>(null);
  const selectedRef = useRef(selected),
    draftRef = useRef(drafts),
    catalogRef = useRef(catalog),
    conversationsRef = useRef(conversations);
  selectedRef.current = selected;
  draftRef.current = drafts;
  catalogRef.current = catalog;
  conversationsRef.current = conversations;
  const scroll = useRef<HTMLDivElement>(null),
    historyAnchor = useRef<{
      threadId: string;
      element: Element;
      top: number;
    } | null>(null),
    input = useRef<HTMLTextAreaElement>(null),
    follow = useRef(true),
    epoch = useRef(""),
    sequence = useRef(0),
    loading = useRef(0),
    composing = useRef(false),
    admitting = useRef(new Set<string>());
  const report = useCallback(
    (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    [],
  );
  const reportModal = (e: unknown) =>
    setModalError(e instanceof Error ? e.message : String(e));
  useEffect(() => setModalError(""), [modal]);
  useEffect(() => {
    const closeMenus = (event: Event) => {
      document
        .querySelectorAll<HTMLDetailsElement>(".thread-menu[open]")
        .forEach((menu) => {
          if (event instanceof KeyboardEvent) {
            if (event.key !== "Escape") return;
            menu.open = false;
            menu.querySelector<HTMLElement>("summary")?.focus();
          } else if (
            event.target instanceof Node &&
            !menu.contains(event.target)
          ) {
            menu.open = false;
          }
        });
    };
    document.addEventListener("pointerdown", closeMenus);
    document.addEventListener("focusin", closeMenus);
    document.addEventListener("keydown", closeMenus);
    return () => {
      document.removeEventListener("pointerdown", closeMenus);
      document.removeEventListener("focusin", closeMenus);
      document.removeEventListener("keydown", closeMenus);
    };
  }, []);
  const hiddenSubmissionText = useRef(new Set<string>());
  const conversation = conversations[selected] || emptyConversation(),
    draft = drafts[selected] || freshDraft();
  // Hide the submitted text in the same render as its optimistic message.
  // Keep the durable draft until admission succeeds so failures lose no input.
  const composerContent = busy[selected] && draft.request &&
    hiddenSubmissionText.current.has(draft.request.requestId) ? "" : draft.content;
  const entries = useMemo(
    () => transcriptEntries(displayMessages(conversation.messages, conversation.followUps, conversation.running)),
    [conversation.messages, conversation.followUps, conversation.running],
  );
  const thinking =
    !offline &&
    !draft.uncertain &&
    awaitingFirstReply(conversation, !!busy[selected]);
  const model = catalog.providers
    .find((p) => p.id === draft.provider)
    ?.models.find((m) => m.id === draft.model);
  const received = useRef(new EventBuffer());
  const snapshotSequences = useRef(
    new Map<string, { instance: string; sequence: number }>(),
  );
  const snapshotLoads = useRef(new Map<string, symbol>());
  const importingAttachments = useRef(new Map<string, number>());
  const [importingAttachmentCounts, setImportingAttachmentCounts] = useState<Record<string, number>>({});
  const saveChains = useRef(new Map<string, Promise<unknown>>());
  function persistDraft(key: string, value: Draft | null) {
    const promise = (saveChains.current.get(key) || Promise.resolve())
      .catch(() => {})
      .then(() => api.request("state.write", { key: "draft:" + key, value }));
    saveChains.current.set(key, promise);
    void promise.catch(report);
    return promise;
  }
  function editDraft(change: Partial<Draft>, key = selectedRef.current) {
    const value = { ...(draftRef.current[key] || freshDraft()), ...change };
    if (change.content !== undefined && value.request)
      hiddenSubmissionText.current.delete(value.request.requestId);
    draftRef.current = { ...draftRef.current, [key]: value };
    setDrafts(draftRef.current);
    void persistDraft(key, value);
  }
  async function list(offset = 0) {
    const data = await api.request("threads.list", { offset, limit: 40 });
    setThreads((rows) => (offset ? [...rows, ...data.threads] : data.threads));
    setNextThreads(data.next);
    setDirectories(data.directories || []);
    if (!offset) setHistoryRevision((value) => value + 1);
  }
  async function configuration(directory = "") {
    const result = await api.request<Catalog>("config.read", { directory });
    setCatalog(result);
    catalogRef.current = result;
    return result;
  }
  async function openSessionSettings(id = selectedRef.current, title?: string) {
    setSettingsLoading(true);
    try {
      if (selectedRef.current !== id) await select(id, true);
      const result = await configuration(draftRef.current[id]?.directory || "");
      if (selectedRef.current === id) {
        const value = resolveSessionDraft(draftRef.current[id] || freshDraft(), result);
        draftRef.current = {...draftRef.current, [id]: value};setDrafts(draftRef.current);
        setSettingsTitle(title || threadTitles.current.get(id) || threads.find(thread => thread.id === id)?.title || "新会话");
        setSettingsThread(id);
      }
    } catch (error) {report(error);}
    finally {setSettingsLoading(false);}
  }
  const refreshes = useRef(new SnapshotRefresh<any>());
  function loadThread(id: string) {
    return refreshes.current.run(
      `${epoch.current}:${id}`,
      sequence.current,
      () => readThreadSnapshot(id),
      epoch.current,
    );
  }
  async function readThreadSnapshot(id: string) {
    const ticket = Symbol(id);
    snapshotLoads.current.set(id, ticket);
    let data: any, following: HostEvent[] | null;
    while (true) {
      data = await api
        .request("threads.read", { threadId: id })
        .catch((error) => {
          if (snapshotLoads.current.get(id) === ticket)
            snapshotLoads.current.delete(id);
          throw error;
        });
      if (snapshotLoads.current.get(id) !== ticket) return data;
      following = received.current.after(data.instance, data.sequence);
      if (following !== null) break;
      // The local cache no longer spans the snapshot boundary. Fetch a new
      // snapshot instead of silently applying only the surviving event suffix.
    }
    snapshotLoads.current.delete(id);

    let view = conversationFromSnapshot(data);
    if (!data.running && data.receipt?.state === "outcome-unknown") {
      view.status = "执行结果待确认";
      // Keep already visible, uncommitted output while the same window reconnects.
      // Never reconstruct model history or automatically replay the interrupted turn.
      for (const message of conversationsRef.current[id]?.messages || []) {
        if (
          message.streaming &&
          message.turnId === data.receipt.turn_id &&
          !view.messages.some((existing) => existing.id === message.id)
        )
          view.messages.push({ ...message, streaming: false });
      }
      view.messages.push({
        id: `unknown:${data.receipt.id}`,
        role: "error",
        content:
          "上次执行意外断开，部分操作可能已完成。任务未自动重跑，请确认结果后再继续。",
      });
    }
    for (const event of following)
      if (
        event.threadId === id &&
        event.instance === data.instance &&
        event.sequence > data.sequence
      )
        view = reduceEvent(view, event);
    snapshotSequences.current.set(id, {
      instance: data.instance,
      sequence: following.length
        ? following[following.length - 1].sequence
        : data.sequence,
    });
    if (data.title && !threadTitles.current.has(id)) threadTitles.current.set(id, data.title);
    setConversations((all) => ({ ...all, [id]: mergeSnapshot(all[id], view) }));
    return data;
  }
  async function select(id: string, reloadConfiguration = id === "new") {
    const resetModel = id === "new";
    const generation = ++loading.current;
    setHistoryReached(null);
    setHistoryScrollable(false);
    setSelected(id);
    selectedRef.current = id;
    setError("");
    follow.current = true;
    setAtBottom(true);
    try {
      // Persist selection before asynchronous history/config loads: closing the
      // window immediately after a click must not restore the previous page.
      await api.request("state.write", {
        key: "ui",
        value: { selected: id, collapsed },
      });
      const data = id === "new" ? null : await loadThread(id);
      let stored =
        draftRef.current[id] ||
        (await api.request("state.read", { key: "draft:" + id })) ||
        freshDraft();
      const directory = data?.directory || stored.directory || "";
      const result = reloadConfiguration
        ? await api.request<Catalog>("config.read", { directory })
        : catalogRef.current;
      if (generation !== loading.current) return;
      if (reloadConfiguration) {
        setCatalog(result);
        catalogRef.current = result;
      }
      // Typing can start before the directory/configuration request returns.
      const latestDraft = draftRef.current[id];
      if (latestDraft) stored = { ...stored, ...latestDraft };
      if ((resetModel && !stored.request && !stored.sessionSettings?.provider && !stored.sessionSettings?.model) || !stored.provider || !result.providers.some(
        provider => provider.id === stored.provider && provider.models.some(model => model.id === stored.model)
      )) {
        const historical = data?.model;
        const historyAvailable = historical && result.providers.some(
          provider => provider.id === historical.provider && provider.models.some(model => model.id === historical.model)
        );
        const choice = !resetModel && !stored.provider && historyAvailable ? historical : result.default;
        const previous = stored.provider ? { provider: stored.provider, model: stored.model } : historical;
        const unavailable = previous && !result.providers.some(
          provider => provider.id === previous.provider && provider.models.some(model => model.id === previous.model)
        );
        stored = {
          ...stored,
          modelNotice: !resetModel && unavailable && choice
            ? `此会话原使用的模型已不可用，已切换至 ${choice.provider} / ${choice.model}。`
            : resetModel ? undefined : stored.modelNotice,
          directory,
          provider: choice?.provider || "",
          model: choice?.model || "",
          reasoning: choice?.reasoning_effort ?? choice?.reasoning ?? null,
        };
      }
      stored = resolveSessionDraft(stored, result);
      draftRef.current = { ...draftRef.current, [id]: stored };
      setDrafts(draftRef.current);
      if (resetModel || stored.modelNotice) void persistDraft(id, stored);
      if (stored.request) {
        const known = await api.request("request.read", {
          requestId: stored.request.requestId,
        });
        if (known && !["outcome-unknown", "preparing"].includes(known.state)) {
          const acceptedId = known.thread_id || id;
          const submitted = stored.request;
          const unchanged =
            stored.content === submitted.content &&
            JSON.stringify(stored.attachments) ===
              JSON.stringify(submitted.attachments);
          const value = {
            ...stored,
            ...(unchanged ? { content: "", attachments: [] } : {}),
            request: undefined,
            uncertain: false,
          };
          draftRef.current = { ...draftRef.current, [acceptedId]: value };
          setDrafts(draftRef.current);
          await persistDraft(acceptedId, value);
          if (acceptedId !== id) {
            await persistDraft(id, null);
            delete draftRef.current[id];
            await list();
            await select(acceptedId);
            return;
          }
        } else if (!known) {
          // No receipt and no in-flight admission: nothing was accepted by this host.
          editDraft({ request: undefined, uncertain: false });
        } else editDraft({ uncertain: true });
      }
    } catch (e) {
      report(e);
    }
  }
  async function acceptLaunchDirectory() {
    const directory = await api.takeLaunchDirectory?.();
    if (!directory) return;
    await select("new");
    editDraft({ directory });
    await configuration(directory);
  }
  async function bootstrap() {
    setOffline("");
    setError("");
    try {
      const init = await api.request("initialize", { protocolVersion: 1 });
      assertThreadManagement(init);
      if (epoch.current !== init.instance) received.current.clear();
      epoch.current = init.instance;
      sequence.current = init.sequence;
      const [ui, dirs] = await Promise.all([
        api.request("state.read", { key: "ui" }),
        api.request("state.read", { key: "recentDirectories" }),
      ]);
      setCollapsed(!!ui?.collapsed);
      setRecent(dirs || []);
      await list();
      await select(ui?.selected || "new", true);
      await acceptLaunchDirectory();
      setReady(true);
    } catch (e) {
      setOffline(e instanceof Error ? e.message : String(e));
      setReady(true);
    }
  }
  useEffect(() => {
    let disposed = false;
    let queued: HostEvent[] = [],
      frame = 0,
      timer: ReturnType<typeof setTimeout> | undefined;
    const flush = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      clearTimeout(timer);
      timer = undefined;
      const batch = queued;
      queued = [];
      if (!batch.length || disposed) return;
      setConversations((all) => {
        const next = { ...all };
        let changed = false;
        for (const event of batch) {
          const id = event.threadId;
          if (!id || event.instance !== epoch.current) continue;
          const watermark = snapshotSequences.current.get(id);
          if (
            watermark?.instance === event.instance &&
            event.sequence <= watermark.sequence
          )
            continue;
          const previous = next[id] || emptyConversation();
          const updated = reduceEvent(previous, event);
          if (updated !== previous) {
            next[id] = updated;
            changed = true;
          }
        }
        return changed ? next : all;
      });
    };
    const unsubscribe = api.subscribe((event) => {
      if (disposed) return;
      if (event.type === "desktop.launch") {
        void acceptLaunchDirectory().catch(report);
        return;
      }
      if (event.type === "host.offline") {
        setOffline(event.data.message);
        return;
      }
      if (epoch.current && event.instance !== epoch.current) {
        epoch.current = event.instance;
        received.current.clear();
        sequence.current = 0;
        snapshotSequences.current.clear();
      }
      if (event.sequence <= sequence.current) return;
      received.current.push(event);
      const gap = sequence.current && event.sequence > sequence.current + 1;
      sequence.current = event.sequence;
      if (gap) {
        const id = selectedRef.current;
        if (id !== "new") void loadThread(id).catch(report);
      }
      const id = event.threadId;
      if (event.type === "TurnStarted")
        setThreads((rows) =>
          rows.map((row) =>
            row.id === id && !row.running ? { ...row, running: true } : row,
          ),
        );
      queued.push(event);
      if (
        ![
          "AssistantTextDelta",
          "AssistantReasoningDelta",
          "ToolOutputDelta",
        ].includes(event.type) ||
        queued.length >= 128
      )
        flush();
      else if (!frame) {
        frame = requestAnimationFrame(flush);
        timer = setTimeout(flush, 50);
      }

      if (["run.settled", "threads.changed"].includes(event.type)) {
        void list().catch(report);
        if (event.type === "run.settled" && id === selectedRef.current)
          void loadThread(id!).catch(report);
      }
    });
    void bootstrap();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      queued = [];
      unsubscribe();
    };
  }, []);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === "n" &&
        !e.shiftKey
      ) {
        e.preventDefault();
        void newChat();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  // Restore the composer's height before measuring the available history area.
  // Otherwise its expansion can turn the initial bottom scroll into an apparent
  // upward read before ResizeObserver gets a chance to follow the new size.
  useLayoutEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height =
        Math.min(input.current.scrollHeight, 180) + "px";
    }
  }, [composerContent, selected, ready]);
  useLayoutEffect(() => {
    const node = scroll.current;
    const anchor = historyAnchor.current;
    if (anchor) {
      historyAnchor.current = null;
      if (node && anchor.threadId === selected && anchor.element.isConnected) {
        node.scrollTop +=
          anchor.element.getBoundingClientRect().top - anchor.top;
        return;
      }
    }
    if (node && follow.current) node.scrollTop = node.scrollHeight;
  }, [conversation.messages, conversation.pending, conversation.followUps, selected, ready]);
  useEffect(() => {
    const node = scroll.current;
    if (!node) return;
    const observer = new ResizeObserver(() => {
      setHistoryScrollable(node.scrollHeight > node.clientHeight + 2);
      if (follow.current) node.scrollTop = node.scrollHeight;
    });
    observer.observe(node);
    if (node.firstElementChild) observer.observe(node.firstElementChild);
    return () => observer.disconnect();
  }, [selected, ready]);
  async function newChat() {
    await select("new");
    input.current?.focus();
  }
  async function finishAdmission(
    key: string,
    threadId: string,
    submitted: Draft,
  ) {
    // Install the accepted input/live snapshot before clearing the visible draft.
    // The runtime may not have persisted UserMessageItem at TurnStarted yet.
    await loadThread(threadId);
    const latest = draftRef.current[key] || submitted;
    const unchanged =
      latest.content === submitted.content &&
      JSON.stringify(latest.attachments) ===
        JSON.stringify(submitted.attachments);
    const cleared = {
      ...latest,
      // The snapshot describes the admitted turn. It must not overwrite the
      // session's separately saved choice for the next turn (including steers).
      ...(unchanged ? { content: "", attachments: [] } : {}),
      request: undefined,
      uncertain: false,
    };
    // Apply the accepted transition before awaiting persistence. Subsequent edits
    // are queued after these writes and cannot be overwritten by a late response.
    draftRef.current = {
      ...draftRef.current,
      [threadId]: cleared,
      ...(key === "new" ? { new: freshDraft() } : {}),
    };
    setDrafts(draftRef.current);
    if (key === "new")
      setConversations((all) => {
        const next = { ...all };
        delete next.new;
        return next;
      });
    const saves: Promise<unknown>[] = [persistDraft(threadId, cleared)];
    if (key === "new") saves.push(persistDraft("new", null));
    if (selectedRef.current === key) {
      setSelected(threadId);
      selectedRef.current = threadId;
      follow.current = true;
      saves.push(
        api.request("state.write", {
          key: "ui",
          value: { selected: threadId, collapsed },
        }),
      );
    }
    await Promise.all(saves);
    await list();
  }
  async function send() {
    const key = selectedRef.current,
      d = draftRef.current[key] || freshDraft();
    if (
      admitting.current.has(key) ||
      !!importingAttachments.current.get(key) ||
      d.uncertain ||
      (!d.content.trim() && !d.attachments.length)
    )
      return;
    if (
      !catalogRef.current.valid ||
      !catalogRef.current.providers
        .find((p) => p.id === d.provider)
        ?.models.some((m) => m.id === d.model)
    ) {
      setError("请先选择有效的供应商和模型");
      return;
    }
    admitting.current.add(key);
    setBusy((all) => ({ ...all, [key]: true }));
    setError("");
    const threadId = key === "new" ? crypto.randomUUID() : key;
    const isFollowUp = !!conversationsRef.current[key]?.running || !!conversationsRef.current[key]?.followUps.messages.length;
    const request = {
      followUpMode: "steer",
      requestId: crypto.randomUUID(),
      threadId,
      content: d.content,
      directory: d.directory,
      provider: d.provider,
      model: d.model,
      reasoning: d.reasoning,
      ...(key === "new" && d.sessionTitle ? {title: d.sessionTitle} : {}),
      ...(d.sessionSettings !== undefined ? {sessionSettings: d.sessionSettings} : {}),
      attachments: d.attachments,
    };
    const saved = { ...d, modelNotice: undefined, request };
    hiddenSubmissionText.current.add(request.requestId);
    let acknowledged = false;
    draftRef.current = { ...draftRef.current, [key]: saved };
    setDrafts(draftRef.current);
    follow.current = true;
    if (!isFollowUp) setConversations((all) => ({
      ...all,
      [key]: {
        ...(all[key] || emptyConversation()),
        messages: [
          ...(all[key]?.messages || []),
          {
            id: `submitted:${request.requestId}`,
            requestId: request.requestId,
            role: "user",
            content: d.content || d.attachments.map((a) => a.name).join("、"),
          },
        ],
        status: "正在提交",
      },
    }));
    try {
      await persistDraft(key, saved);
      const accepted = await api.request("turn.submit", request);
      if (
        ![
          "accepted",
          "queued",
          "running",
          "waiting",
          "completed",
          "failed",
          "cancelled",
        ].includes(accepted.state)
      )
        throw new Error("提交结果未知，请核对会话");
      acknowledged = true;
      await finishAdmission(key, threadId, d);
    } catch (e) {
      if (acknowledged) {
        report(e);
        return;
      }
      const known = await api
        .request("request.read", { requestId: request.requestId })
        .catch(() => null);
      if (known && !["outcome-unknown", "preparing"].includes(known.state)) {
        await finishAdmission(key, threadId, d).catch(report);
      } else {
        const message = e instanceof Error ? e.message : String(e);
        const uncertain = {
          ...(draftRef.current[key] || saved),
          request: message.includes("提交被拒绝：") ? undefined : request,
          uncertain: !message.includes("提交被拒绝："),
        };
        if (!uncertain.uncertain)
          setConversations((all) => ({
            ...all,
            [key]: {
              ...(all[key] || emptyConversation()),
              messages: (all[key]?.messages || []).filter(
                (m) => m.requestId !== request.requestId,
              ),
              status: "提交失败",
            },
          }));
        draftRef.current = { ...draftRef.current, [key]: uncertain };
        setDrafts(draftRef.current);
        await persistDraft(key, uncertain).catch(() => {});
        report(e);
      }
    } finally {
      hiddenSubmissionText.current.delete(request.requestId);
      admitting.current.delete(key);
      setBusy((all) => ({ ...all, [key]: false }));
    }
  }
  async function stop() {
    try {
      await api.request("turn.stop", { threadId: selected });
      setConversations((all) => ({
        ...all,
        [selected]: { ...all[selected], status: "正在停止" },
      }));
    } catch (e) {
      report(e);
    }
  }
  async function older() {
    const id = selectedRef.current;
    const snapshot = conversationsRef.current[id];
    const node = scroll.current;
    if (!node || node.scrollHeight <= node.clientHeight + 2 || !snapshot?.loaded || id === "new")
      return;
    setHistoryReached(id);
    if (
      olderRequest.current ||
      snapshot.next === null
    )
      return;
    olderRequest.current = id;
    setLoadingOlder(id);
    try {
      const page = await api.request("threads.read", {
        threadId: id,
        before: snapshot.next,
      });
      if (!conversationsRef.current[id]) return;
      // Measure at commit time: the reader may have scrolled or received output
      // while this page was loading. Preserve an existing DOM anchor, not the
      // old scrollHeight (which also includes unrelated output at the bottom).
      const node = scroll.current;
      if (node && id === selectedRef.current) {
        const top = node.getBoundingClientRect().top;
        const element = Array.from(
          node.querySelectorAll(
            ".message, .process-group, .message-error, .message-stopped",
          ),
        ).find((element) => element.getBoundingClientRect().bottom > top);
        if (element)
          historyAnchor.current = {
            threadId: id,
            element,
            top: element.getBoundingClientRect().top,
          };
      }
      setConversations((all) => {
        const current = all[id];
        if (!current) return all;
        return {
          ...all,
          [id]: {
            ...current,
            messages: mergeOlderMessages(current.messages, page.messages),
            next: page.next,
          },
        };
      });
    } catch (e) {
      report(e);
    } finally {
      olderRequest.current = null;
      setLoadingOlder(null);
    }
  }
  async function addAttachments(items: Attachment[], key = selectedRef.current) {
    const current = draftRef.current[key] || freshDraft();
    if (current.attachments.length + items.length > 8) {
      setError("最多添加 8 个附件");
      return;
    }
    editDraft({ attachments: [...current.attachments, ...items] }, key);
  }
  async function importAttachments(load: () => Promise<Attachment[]>, key = selectedRef.current) {
    importingAttachments.current.set(key, (importingAttachments.current.get(key) || 0) + 1);
    setImportingAttachmentCounts(Object.fromEntries(importingAttachments.current));
    try {
      const items = await load();
      if (items.length) await addAttachments(items, key);
    } catch (error) {
      report(error);
    } finally {
      const count = (importingAttachments.current.get(key) || 1) - 1;
      if (count) importingAttachments.current.set(key, count); else importingAttachments.current.delete(key);
      setImportingAttachmentCounts(Object.fromEntries(importingAttachments.current));
    }
  }
  const providerItems = catalog.providers.map((p) => ({
    id: p.id,
    label: p.id,
    icon: modelIcon(p.id),
  }));
  const modelItems = (
    catalog.providers.find((p) => p.id === draft.provider)?.models || []
  ).map((m) => ({
    id: m.id,
    label: m.id,
    icon: modelIcon(m.id) || modelIcon(draft.provider),
  }));
  function renderThread(t: any) {
    threadTitles.current.set(t.id, t.title);
    return (
      <div
        className={`thread-row ${selected === t.id ? "active" : ""}`}
        key={t.id}
        onContextMenu={(event) => {
          event.preventDefault();
          const menu = event.currentTarget.querySelector<HTMLDetailsElement>(".thread-menu");
          if (!menu) return;
          document.querySelectorAll<HTMLDetailsElement>(".thread-menu[open]").forEach((other) => {
            if (other !== menu) other.open = false;
          });
          menu.open = true;
          menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
        }}
      >
        <button
          className="thread-select"
          title={t.title}
          onClick={() => void select(t.id)}
        >
          <i
            className={`running-dot${t.running ? "" : " idle"}`}
            aria-hidden="true"
          />
          <span>{t.title}</span>
        </button>
        <details className="thread-menu">
          <summary aria-label="会话操作">
            <Icon name="more" size={17} />
          </summary>
          <div>
            <button disabled={settingsLoading || !!offline || !!busy[t.id]}
              onClick={(e) => {
                e.currentTarget.closest("details")!.open = false;
                void openSessionSettings(t.id, t.title);
              }}>设置</button>
            <button
              onClick={(e) => {
                e.currentTarget.closest("details")!.open = false;
                setTarget(t);
                setField(t.title);
                setModal("rename");
              }}
            >
              重命名
            </button>
            <button
              disabled={removing}
              onClick={(e) => {
                e.currentTarget.closest("details")!.open = false;
                void requestRemoval(t, true);
              }}
            >
              归档
            </button>
            <button
              className="danger-text"
              disabled={removing}
              onClick={(e) => {
                e.currentTarget.closest("details")!.open = false;
                void requestRemoval(t);
              }}
            >
              删除
            </button>
          </div>
        </details>
      </div>
    );
  }
  if (!ready) return (
    <main className={`startup-screen${api.platform === "win32" ? " windows" : ""}`} aria-busy="true">
      <div className="startup-drag" aria-hidden="true" />
      <WindowControls startup onError={report}/>
      <div className="startup-identity" role="status" aria-live="polite" aria-label="Corki 启动中">
        <h1>Corki</h1>
      </div>
    </main>
  );
  return (
    <div className={`desktop ${navigator.platform.startsWith("Win") ? "windows" : ""} ${collapsed ? "collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-drag" />
        <div className="brand">
          <img src="resources/corki.png" alt="" />
          <span>Corki</span>
          <button
            className="icon-button"
            title="收起侧栏"
            aria-label="收起侧栏"
            onClick={() => {
              setCollapsed(true);
              void api.request("state.write", {
                key: "ui",
                value: { selected, collapsed: true },
              });
            }}
          >
            <Icon name="sidebar" />
          </button>
        </div>
        <button className="new-chat" onClick={() => void newChat()}>
          <Icon name="chat" />
          <span>新会话</span>
          <span className="shortcut">{navigator.platform.startsWith("Mac") ? "⌘" : "Ctrl"} N</span>
        </button>
        <nav className="thread-list" aria-label="会话导航">
          <HistorySection name="最近会话" initiallyOpen>
            {threads.map(renderThread)}
            {nextThreads !== null && (
              <button
                className="more-history"
                onClick={() => void list(nextThreads).catch(report)}
              >
                更多会话
              </button>
            )}
            {!threads.length && ready && <p className="no-history">暂无会话</p>}
          </HistorySection>
          <WorkspaceHistory
            directory={{path: "", name: "机器人会话"}}
            robot
            revision={historyRevision}
            renderThread={renderThread}
          />
          {directories.map((directory) => (
            <WorkspaceHistory
              key={directory.path}
              directory={directory}
              revision={historyRevision}
              renderThread={renderThread}
            />
          ))}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          {collapsed && (
            <button
              className="icon-button"
              title="展开侧栏"
              aria-label="展开侧栏"
              onClick={() => {
                setCollapsed(false);
                void api.request("state.write", {
                  key: "ui",
                  value: { selected, collapsed: false },
                });
              }}
            >
              <Icon name="sidebar" />
            </button>
          )}
          <span className="topbar-spacer" aria-hidden="true" />
          <button className="icon-button" title="会话设置" aria-label="会话设置"
            disabled={!ready || !!offline || settingsLoading || !!busy[selected] || !!draft.uncertain}
            onClick={() => void openSessionSettings()}><Icon name="gear" size={17}/></button>
          <button className="icon-button app-settings" title="打开管理页面" aria-label="打开管理页面"
            disabled={openingManager} onClick={() => void openManager()}>
            <Icon name="menu" size={17} />
          </button>
          <button className="icon-button app-restart" title="重启应用" aria-label="重启应用"
            disabled={restarting} onClick={async () => {
              setRestarting(true);
              try {
                await Promise.all([...saveChains.current.values()]);
                const accepted = await api.restartApp();
                if (!accepted) setRestarting(false);
              } catch (error) { setRestarting(false); report(error); }
            }}>
            <Icon name="refresh" size={17} />
          </button>
          <WindowControls onError={report} beforeClose={async () => {
            await Promise.all([...saveChains.current.values()]);
          }}/>
        </header>
        {offline && (
          <div className="banner model-health-banner service-offline-banner" role="status" aria-live="polite">
            <span>{offline}</span>
            <button
              onClick={() =>
                void api.restartBackend().then(bootstrap).catch(report)
              }
            >
              重新连接
            </button>
            <button
              onClick={() =>
                void api.configurePython().then(bootstrap).catch(report)
              }
            >
              选择 Python 环境
            </button>
          </div>
        )}
        {ready && !offline && !catalog.valid && (
          <div className="banner">
            <span>{catalog.error || "请先在管理页面配置供应商和模型。"}</span>
            <button
              onClick={() => void configuration(draft.directory).catch(report)}
            >
              刷新配置
            </button>
          </div>
        )}
        {draft.modelNotice && !(conversation.running && conversation.modelWarning && conversation.modelWarningReason !== "idle") && (
          <div className="banner model-health-banner" role="status" aria-live="polite">
            <span>{draft.modelNotice}</span>
          </div>
        )}
        {conversation.running && conversation.modelWarning && conversation.modelWarningReason !== "idle" && (
          <div className="banner model-health-banner" role="status" aria-live="polite">
            <span>{conversation.modelWarningReason === "connection" ? "网络连接失败，重试中" : "当前模型响应较慢，请耐心等待"}</span>
          </div>
        )}
        <div
          className="conversation-scroll"
          ref={scroll}
          onScroll={(e) => {
            const n = e.currentTarget;
            follow.current = n.scrollHeight - n.scrollTop - n.clientHeight < 64;
            setAtBottom(follow.current);
            if (n.scrollTop <= 24 && !follow.current) void older();
          }}
          onWheel={(e) => {
            if (e.deltaY < 0 && e.currentTarget.scrollTop <= 24) {
              follow.current = false;
              void older();
            }
          }}
        >
          <div className="conversation-inner">
            {!conversation.messages.length &&
              !conversation.followUps.messages.length &&
              !conversation.pending.length &&
              !thinking ? (
              selected !== "new" && !conversation.loaded ? (
                !error && !offline ? (
                  <div className="history-loading" role="status" aria-label="加载会话">
                    <Icon name="gear" size={14} />
                    <span>加载会话中</span>
                  </div>
                ) : null
              ) : (
              <div className="welcome">
                <img src="resources/corki.png" alt="Corki" />
                <h1 className="welcome-greeting" aria-label="Learning never ends">
                  <span aria-hidden="true">{"Learning never ends".split("").map((letter, index) => (
                    <span className="greeting-letter" key={index}
                      style={{ "--letter-delay": `${index * 45}ms`, "--glow-delay": `${index * 20}ms` } as React.CSSProperties}>
                      {letter}
                    </span>
                  ))}</span>
                </h1>
              </div>
              )
            ) : (
              <>
                {conversation.loaded && conversation.messages.length > 0 &&
                  (loadingOlder === selected || (historyScrollable &&
                    (conversation.next !== null || historyReached === selected))) && (
                  <div
                    className="history-loading"
                    role="status"
                    aria-live="polite"
                  >
                    {loadingOlder === selected ? (
                      <>
                        <Icon name="gear" size={14} />
                        <span>加载更早的消息中</span>
                      </>
                    ) : (
                      <span>
                        {conversation.next === null
                          ? "没有更早的记录啦"
                          : "上划加载更早的消息"}
                      </span>
                    )}
                  </div>
                )}
                <TranscriptView
                  key={selected}
                  entries={entries}
                  active={
                    conversation.running &&
                    !["已完成", "执行失败", "回答已停止"].includes(
                      conversation.status,
                    )
                  }
                  onError={report}
                  onRetryInput={async id => { await api.request("queue.action", {threadId: selected, action: "send", id}); }}
                />
                {thinking && (
                  <div
                    className="thinking-indicator"
                    role="status"
                    aria-label="思考中"
                  >
                    <div className="thinking-content">
                      <span className="thinking-spinner" aria-hidden="true" />
                      <span className="thinking-label">{conversation.status === "正在压缩上下文" ? "压缩中" : "思考中"}</span>
                    </div>
                  </div>
                )}
                {conversation.pending.map((item) => (
                  <Interaction
                    key={item.request_id}
                    item={item}
                    onError={report}
                    onDone={() => setError("")}
                  />
                ))}
              </>
            )}
          </div>
        </div>
        <div className="composer-area">
          {!atBottom && (
            <button
              className="jump-bottom"
              aria-label="回到最新"
              onClick={() => {
                follow.current = true;
                scroll.current?.scrollTo({
                  top: scroll.current.scrollHeight,
                  behavior: "smooth",
                });
                setAtBottom(true);
              }}
            >
              <Icon name="arrow" size={18} />
            </button>
          )}
          {error && (
            <div className="inline-error" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="关闭提示"
                onClick={() => setError("")}
              >
                <Icon name="close" size={15} />
              </button>
            </div>
          )}
          {draft.uncertain && (
            <div className="inline-error">
              <span>提交结果尚未确认，已保留输入。</span>
              <button onClick={() => void select(selected)}>核对状态</button>
            </div>
          )}
          <button
            className="directory-bar"
            disabled={selected !== "new"}
            title={
              conversation.directory ||
              draft.directory ||
              "~/.corki/main/default"
            }
            onClick={() => {
              setField(draft.directory);
              setModal("directory");
              void api
                .request("state.read", { key: "recentDirectories" })
                .then((value) => setRecent(value || []))
                .catch(report);
            }}
          >
            <Icon name="folder" size={17} />
            <span>
              {conversation.directory || draft.directory || "选择工作目录"}
            </span>
            {selected === "new" && <Icon name="chevron" size={15} />}
          </button>
          <div
            className="composer"
            aria-busy={!!importingAttachmentCounts[selected]}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "copy";
            }}
            onDrop={(e) => {
              e.preventDefault();
              const files = Array.from(e.dataTransfer.files);
              if (files.length) void importAttachments(() => api.importFiles(files));
            }}
          >
            {draft.attachments.length > 0 && (
              <div className="attachments">
                {draft.attachments.map((a) => (
                  <div className="attachment" key={a.id}>
                    {a.type === "image" ? (
                      <img src={a.data} alt="" />
                    ) : (
                      <Icon name="file" size={18} />
                    )}
                    <button
                      className="attachment-name"
                      title={a.name}
                      onClick={() => void previewAttachment(a)}
                    >
                      {a.name}
                    </button>
                    <button
                      aria-label={`移除 ${a.name}`}
                      onClick={() =>
                        editDraft({
                          attachments: draft.attachments.filter(
                            (x) => x.id !== a.id,
                          ),
                        })
                      }
                    >
                      <Icon name="close" size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <textarea
              ref={input}
              rows={2}
              maxLength={100000}
              aria-label="消息"
              placeholder="Do anything"
              value={composerContent}
              onChange={(e) => editDraft({ content: e.target.value })}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData.items)
                  .filter(item => item.kind === "file" && item.type.startsWith("image/"))
                  .map(item => item.getAsFile()).filter((file): file is File => file !== null);
                if (!files.length) return; // Preserve the browser's ordinary text/IME paste behavior.
                event.preventDefault();
                const key = selectedRef.current;
                const current = draftRef.current[key] || freshDraft();
                if (current.attachments.length + files.length > 8) {setError("最多添加 8 个附件");return;}
                const text = event.clipboardData.getData("text/plain");
                if (text) {
                  const node = event.currentTarget, start = node.selectionStart, end = node.selectionEnd;
                  editDraft({content: (current.content.slice(0, start) + text + current.content.slice(end)).slice(0, 100000)}, key);
                }
                void importAttachments(async () => {
                  const images = await Promise.all(files.map(async file => ({
                    name: file.name || "粘贴图片.png", mime: file.type, bytes: await file.arrayBuffer(),
                  })));
                  return api.importImages(images);
                }, key);
              }}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onCompositionEnd={() => {
                composing.current = false;
              }}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing &&
                  !composing.current &&
                  e.keyCode !== 229
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-toolbar">
              <button
                className="icon-button"
                aria-label="添加附件"
                title="添加文本或图片"
                onClick={() =>
                  void importAttachments(() => api.chooseFiles())
                }
              >
                <Icon name="plus" />
              </button>
              <span className="model-summary" title={`${draft.provider} / ${draft.model}`}>{draft.model || "未选择模型"}</span>
              {conversation.running && (draft.content.trim() || draft.attachments.length > 0) && (
                <button className="send-button followup-send" aria-label="追加消息"
                  title="追加消息"
                  disabled={!ready || !!offline || !catalog.valid || !model || !!busy[selected] || !!draft.uncertain || !!importingAttachmentCounts[selected]}
                  onClick={() => void send()}><Icon name="send" size={18} /></button>
              )}
              {conversation.running ? (
                <button
                  className="send-button stop"
                  aria-label="停止执行"
                  onClick={() => void stop()}
                >
                  <Icon name="stop" size={18} />
                </button>
              ) : (
                <button
                  className="send-button"
                  aria-label="发送消息"
                  disabled={
                    !ready ||
                    !!importingAttachmentCounts[selected] ||
                    !!offline ||
                    !catalog.valid ||
                    !model ||
                    !!busy[selected] ||
                    !!draft.uncertain ||
                    (!draft.content.trim() && !draft.attachments.length)
                  }
                  onClick={() => void send()}
                >
                  {busy[selected] || importingAttachmentCounts[selected] ? (
                    <span className="loading-ring" />
                  ) : (
                    <Icon name="send" />
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </main>
      {attachmentPreview && (
        <Modal
          title={attachmentPreview.name}
          onClose={() => setAttachmentPreview(null)}
        >
          {attachmentPreview.type === "image" ? (
            <img
              className="image-preview"
              src={attachmentPreview.data}
              alt={attachmentPreview.name}
            />
          ) : (
            <pre className="text-preview">{attachmentPreview.data}</pre>
          )}
        </Modal>
      )}
      {managerState !== "closed" && (
        <Modal className="manager-launch" title={managerState === "starting" ? "正在开启 Corki 管理服务…" : "无法打开管理页面"}
          onClose={() => setManagerState("closed")}>
          {managerState === "starting" ? (
            <div className="manager-launch-progress" role="status" aria-live="polite">
              <span className="manager-launch-gear"><Icon name="gear" size={38}/></span>
              <span>准备就绪后将自动打开</span>
            </div>
          ) : <>
            <p className="manager-launch-error" role="alert">{managerError}</p>
            <footer><button onClick={() => setManagerState("closed")}>关闭</button>
              <button className="primary" onClick={() => void openManager()}>重试</button></footer>
          </>}
        </Modal>
      )}
      {settingsThread === selected && <SessionSettingsDialog key={settingsThread}
        catalog={catalog} draft={draft} threadId={selected} title={settingsTitle}
        running={conversation.running}
        blocked={!!busy[selected] || !!draft.uncertain}
        onCompact={async (requestId) => {
          const key = settingsThread!;
          await api.request("turn.compact", {threadId: key, requestId});
          await loadThread(key);
          await list();
        }}
        onClose={() => setSettingsThread(null)}
        onSave={async (sessionSettings, effective, name) => {
          const key = settingsThread!;
          if (key !== "new" && name !== settingsTitle) {
            await api.request("threads.rename", {threadId: key, title: name});
            threadTitles.current.set(key, name);
            setThreads(rows => rows.map(row => row.id === key ? {...row, title: name} : row));
            setSettingsTitle(name);
          }
          const value = {...(draftRef.current[key] || freshDraft()), sessionSettings, sessionTitle: name || undefined,
            provider: effective.provider, model: effective.model, reasoning: effective.reasoning};
          await persistDraft(key, value);
          draftRef.current = {...draftRef.current, [key]: value};
          setDrafts(draftRef.current);
        }}/>}
      {modal === "directory" && (
        <Modal title="工作目录" onClose={() => setModal(null)}>
          <div className="directory-input">
            <input
              autoFocus
              aria-label="工作目录路径"
              placeholder="留空使用默认工作目录"
              value={field}
              onChange={(e) => setField(e.target.value)}
            />
            <button
              onClick={() =>
                void api
                  .chooseDirectory()
                  .then((v) => {
                    if (v) setField(v);
                  })
                  .catch(reportModal)
              }
            >
              选择目录
            </button>
          </div>
          {recent.length > 0 && (
            <div className="recent-directories">
              {recent.map((d) => (
                <button key={d} title={d} onClick={() => setField(d)}>
                  <Icon name="folder" size={16} />
                  {d}
                </button>
              ))}
            </div>
          )}
          {modalError && (
            <div className="inline-error" role="alert">
              {modalError}
            </div>
          )}
          <footer>
            <button onClick={() => setModal(null)}>取消</button>
            <button
              className="primary"
              onClick={() =>
                void configuration(field)
                  .then(() => {
                    editDraft({ directory: field });
                    setModal(null);
                  })
                  .catch(reportModal)
              }
            >
              确定
            </button>
          </footer>
        </Modal>
      )}
      {modal === "rename" && (
        <Modal title="重命名会话" onClose={() => setModal(null)}>
          <input
            autoFocus
            aria-label="会话名称"
            maxLength={120}
            value={field}
            onChange={(e) => setField(e.target.value)}
          />
          {modalError && (
            <div className="inline-error" role="alert">
              {modalError}
            </div>
          )}
          <footer>
            <button onClick={() => setModal(null)}>取消</button>
            <button
              className="primary"
              disabled={!field.trim()}
              onClick={() =>
                void api
                  .request("threads.rename", {
                    threadId: target.id,
                    title: field,
                  })
                  .then(() => {
                    setModal(null);
                    void list();
                  })
                  .catch(reportModal)
              }
            >
              保存
            </button>
          </footer>
        </Modal>
      )}
      {(modal === "delete" || modal === "archive") && (
        <Modal
          className={`conversation-confirm${modal === "delete" ? " delete-confirm" : ""}`}
          title={<span className="conversation-confirm-heading"><span className="conversation-confirm-icon"><Icon name={modal === "archive" ? "folder" : "trash"} size={23} /></span>{modal === "archive" ? "归档会话" : "删除会话"}</span>}
          onClose={() => {
            if (!removing) setModal(null);
          }}
        >
          <div className="delete-conversation">
            <div className="delete-title" title={target.title}>
              <Icon name="chat" size={18} />
              <span>{target.title}</span>
            </div>
            <p>
              {target.running
                ? `此会话正在执行。停止任务后才能${modal === "archive" ? "归档" : "删除"}。`
                : "此会话及聊天记录将被永久删除，无法恢复。"}
            </p>

          </div>
          {modalError && (
            <div className="inline-error" role="alert">
              {modalError}
            </div>
          )}
          <footer>
            {!target.running && (
              <label className="reminder-option">
                <input
                  type="checkbox"
                  checked={skipDeleteReminder}
                  disabled={removing}
                  onChange={(e) => setSkipDeleteReminder(e.target.checked)}
                />
                <span className="reminder-check" aria-hidden="true">
                  ✓
                </span>
                <span>一天内不再提醒</span>
              </label>
            )}
            <button disabled={removing} onClick={() => setModal(null)}>
              取消
            </button>
            {target.running ? (
              <button
                className="primary"
                onClick={() =>
                  void api
                    .request("turn.stop", { threadId: target.id })
                    .then(() => setModal(null))
                    .catch(reportModal)
                }
              >
                停止任务
              </button>
            ) : (
              <button
                className="danger"
                disabled={removing}
                onClick={() =>
                  void removeThread(target, false, skipDeleteReminder)
                }
              >
                {removing ? "删除中…" : "删除"}
              </button>
            )}
          </footer>
        </Modal>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
