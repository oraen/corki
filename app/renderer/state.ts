import type { Attachment, HostEvent } from "../shared/protocol";
export interface Message {
  id: string;
  turnId?: string;
  requestId?: string;
  threadId?: string;
  sourceId?: string;
  truncated?: boolean;
  nextText?: number | null;
  imageCount?: number;
  role: string;
  content: string;
  name?: string;
  callId?: string;
  error?: boolean;
  streaming?: boolean;
  phase?: string | null;
  completed?: boolean;
  createdAt?: string;
  contextOnly?: boolean;
  startedAt?: number;
  elapsedSeconds?: number;
}
export interface FollowUpState {
  mode: "steer" | "queue";
  paused: boolean;
  messages: { id: string; content: string; attachments: string[]; state: string; error?: string }[];
}
export interface Conversation {
  modelWarning?: boolean;
  followUps: FollowUpState;
  messages: Message[];
  running: boolean;
  status: string;
  pending: any[];
  next: number | null;
  directory: string;
  model?: { provider: string; model: string; reasoning_effort?: string | null };
  loaded: boolean;
}
export function emptyConversation(): Conversation {
  return {
    messages: [],
    followUps: { mode: "steer", paused: false, messages: [] },
    running: false,
    status: "",
    pending: [],
    next: null,
    directory: "",
    loaded: false,
  };
}

export function conversationFromSnapshot(
  data: Conversation & {
    threadId: string;
    instance: string;
    live: Pick<HostEvent, "type" | "data">[];
  },
): Conversation {
  let view: Conversation = {
    ...emptyConversation(),
    ...data,
    loaded: true,
    status: data.running ? "正在执行" : "",
  };
  if (data.running) {
    let replay = { ...emptyConversation(), running: true };
    for (const event of data.live) {
      replay = reduceEvent(replay, {
        ...event,
        threadId: data.threadId,
        instance: data.instance,
        sequence: 0,
      });
    }
    // Persistence can complete while the UI is reading a snapshot. Replayed
    // deltas/completions must never replace canonical previews or append their
    // text twice. Incomplete messages exist only in the live projection.
    const canonical = new Map(
      data.messages.map((message) => [message.id, message]),
    );
    const finishedCalls = new Set(
      data.messages
        .filter((message) => message.role === "tool-result")
        .map((message) => message.callId),
    );
    // Merge in live event order, using canonical items as chronological anchors.
    // Replaying onto the page directly appends off-page older items at the end.
    const positions = new Map(data.messages.map((m, i) => [m.id, i]));
    const overlap = replay.messages.some((m) => positions.has(m.id));
    let cursor = overlap ? 0 : data.messages.length;
    while (cursor < data.messages.length && data.messages[cursor].contextOnly) cursor++;
    const messages: Message[] = data.messages.slice(0, cursor);
    for (const message of replay.messages) {
      const position = positions.get(message.id);
      if (position !== undefined) {
        if (position < cursor) continue;
        messages.push(...data.messages.slice(cursor, position));
        cursor = position + 1;
      }
      const saved = canonical.get(message.id);
      messages.push(saved && saved.role !== "tool" ? saved :
        message.callId && finishedCalls.has(message.callId)
          ? { ...message, streaming: false } : message);
    }
    messages.push(...data.messages.slice(cursor));
    view = { ...view, status: replay.status || view.status, messages, modelWarning: replay.modelWarning };

  }
  // A submitted input is a temporary display item until canonical history owns it.
  const canonicalUserTurns = new Set(
    data.messages
      .filter((m) => m.role === "user")
      .map((m) => m.turnId)
      .filter(Boolean),
  );
  view.messages = view.messages.filter(
    (m) => !(m.requestId && m.turnId && canonicalUserTurns.has(m.turnId)),
  );
  // Replayed requests may already have been answered in this still-running turn.
  view.pending = data.pending || [];
  if (view.pending.length)
    view.status = view.pending.some((request) => request.kind === "question")
      ? "等待你的回答"
      : "等待确认";
  else if (view.status === "等待你的回答" || view.status === "等待确认")
    view.status = data.running ? "正在执行" : "";
  return view;
}
// Preserve older pages only when they overlap the authoritative new page.
// A disjoint page can follow rollback or a large gap: never invent continuity.
// A context-only question is a real canonical item fetched ahead of its page.
// Keep it until its canonical page arrives; an intervening empty/compaction page
// must not erase it. Position it before the first visible item of its own turn.
export function mergeOlderMessages(current: Message[], older: Message[]): Message[] {
  const canonical = new Map([...older, ...current].filter(m => !m.contextOnly).map(m => [m.id, m]));
  const seen = new Set<string>();
  const combined = [...older, ...current].filter(m => {
    if (seen.has(m.id) || (m.contextOnly && canonical.has(m.id))) return false;
    seen.add(m.id);
    return true;
  });
  const result = combined.filter(m => !m.contextOnly);
  for (const question of combined.filter(m => m.contextOnly)) {
    const index = result.findIndex(m => m.turnId === question.turnId);
    if (index >= 0) result.splice(index, 0, question);
    else result.push(question);
  }
  return result;
}

export function mergeSnapshot(
  previous: Conversation | undefined,
  incoming: Conversation,
): Conversation {
  if (!previous) return incoming;
  const oldById = new Map(previous.messages.map((m) => [m.id, m]));
  const incomingIds = new Set(incoming.messages.filter((m) => !m.contextOnly).map((m) => m.id));
  let prefix: Message[] = [];
  let next = incoming.next;
  if (incoming.next !== null) {
    const anchor = incoming.messages.find(
      (m) => oldById.has(m.id) && !m.requestId && !m.streaming && !m.contextOnly,
    );
    if (anchor) {
      const index = previous.messages.findIndex((m) => m.id === anchor.id);
      prefix = previous.messages
        .slice(0, index)
        .filter((m) => !incomingIds.has(m.id) && !m.requestId && !m.streaming);
      next = previous.next;
    }
  }
  // A paged snapshot may bring back the canonical question while the old live
  // question was still provisional. Keep its anchor before that turn's prefix,
  // never between two pieces of the same reply (which also duplicates React keys).
  for (const question of incoming.messages.filter((m) => m.contextOnly && m.role === "user")) {
    if (prefix.some((m) => m.role === "user" && m.turnId === question.turnId)) continue;
    const start = prefix.findIndex((m) => m.turnId === question.turnId);
    if (start >= 0) prefix.splice(start, 0, question);
  }
  const messages = [
    ...prefix,
    ...incoming.messages.filter((m) => !m.contextOnly || !prefix.some((old) => old.id === m.id)).map((message) => {
      const old = oldById.get(message.id);
      if (!old) return message;
      const keys = new Set([...Object.keys(old), ...Object.keys(message)]);
      return [...keys].every(
        (key) => (old as any)[key] === (message as any)[key],
      )
        ? old
        : message;
    }),
  ];
  return { ...incoming, next, messages };
}

export function reduceEvent(
  previous: Conversation,
  event: HostEvent,
): Conversation {
  const s = {
      ...previous,
      messages: previous.messages,
      pending: previous.pending,
    },
    d = event.data;
  const update = (
    id: string,
    fn: (row: Message) => Message,
    initial: Message,
  ) => {
    const index = s.messages.findIndex((m) => m.id === id);
    if (index < 0) s.messages = [...s.messages, fn(initial)];
    else {
      s.messages = [...s.messages];
      s.messages[index] = fn(s.messages[index]);
    }
  };
  const base = { turnId: d.turn_id, content: "" };
  switch (event.type) {
    case "queue.changed":
      s.followUps = d as FollowUpState;
      break;
    case "UserMessageSubmitted":
      s.status = "正在思考";
      update(
        d.item_id || `submitted:${d.request_id}`,
        (m) => ({ ...m, content: d.text, turnId: d.turn_id || undefined }),
        {
          id: d.item_id || `submitted:${d.request_id}`,
          role: "user",
          requestId: d.item_id ? undefined : d.request_id,
          createdAt: d.createdAt,
          content: d.text,
        },
      );
      break;
    case "TurnStarted":
      if (s.messages.some((m) => m.role === "user" && m.requestId && !m.turnId))
        s.messages = s.messages.map((m) =>
          m.role === "user" && m.requestId && !m.turnId ? { ...m, turnId: d.turn_id } : m,
        );
      s.running = true;
      s.status = "正在思考";
      break;
    case "AssistantTextDelta": {
      const id = d.item_id || `answer:${d.turn_id}`;
      update(
        id,
        (m) => ({ ...m, content: m.content + d.delta, phase: d.phase ?? m.phase, streaming: true }),
        { ...base, id, role: "assistant" },
      );
      s.status = "正在回复";
      break;
    }
    case "AssistantMessageCompleted": {
      const id = d.item_id || `answer:${d.turn_id}`;
      const provisional = `answer:${d.turn_id}`;
      if (id !== provisional) {
        if (s.messages.some((m) => m.id === id))
          s.messages = s.messages.filter((m) => m.id !== provisional);
        else
          s.messages = s.messages.map((m) =>
            m.id === provisional ? { ...m, id } : m,
          );
      }
      update(id, (m) => ({ ...m, content: d.text, phase: d.phase ?? m.phase, streaming: false, createdAt: d.createdAt || m.createdAt }), {
        ...base,
        id,
        role: "assistant",
      });
      break;
    }
    case "AssistantReasoningDelta": {
      if (d.channel !== "summary") return previous;
      const id = `reasoning:${d.item_id || d.turn_id}`;
      update(
        id,
        (m) => ({ ...m, content: (m.content + d.delta).slice(-32000) }),
        { ...base, id, role: "reasoning" },
      );
      break;
    }
    case "ToolCallStarted":
      update(`tool:${d.tool_call_id}`, (m) => ({ ...m, streaming: true }), {
        ...base,
        id: `tool:${d.tool_call_id}`,
        callId: d.tool_call_id,
        role: "tool",
        name: d.tool_name,
        content: d.arguments_preview,
        startedAt: d.startedAt,
        streaming: true,
      });
      s.status = `执行 ${d.tool_name}`;
      break;
    case "ToolOutputDelta": {
      const id = `output:${d.tool_call_id}`;
      update(
        id,
        (m) => ({ ...m, content: (m.content + d.delta).slice(-64000) }),
        { ...base, id, callId: d.tool_call_id, role: "tool-result" },
      );
      break;
    }
    case "ToolCallCompleted":
      s.messages = s.messages.map((m) =>
        m.callId === d.tool_call_id
          ? {
              ...m,
              streaming: false,
              completed: true,
              error: d.is_error,
              startedAt: d.startedAt ?? m.startedAt,
              elapsedSeconds: d.elapsedSeconds,
            }
          : m,
      );
      break;
    case "ContextCompactionStarted":
      s.status = "正在整理上下文";
      break;
    case "ModelRetryScheduled":
      s.status = `连接重试 ${d.attempt}`;
      break;
    case "model.health":
      s.modelWarning = !!d.warning;
      break;
    case "UserInputRequested":
      s.pending = s.pending.filter((p) => p.request_id !== d.call_id);
      s.pending.push({
        ...d,
        kind: "question",
        request_id: d.call_id,
        threadId: event.threadId,
      });
      s.status = "等待你的回答";
      break;
    case "approval":
      s.pending = s.pending.filter((p) => p.request_id !== d.request_id);
      s.pending.push(d);
      s.status = "等待确认";
      break;
    case "interaction.resolved":
      s.pending = s.pending.filter((p) => p.request_id !== d.requestId);
      s.status = "正在执行";
      break;
    case "WarningEvent":
      s.status = d.message;
      break;
    case "GoalUpdated":
      s.status = d.status === "active" ? "正在推进目标" : `目标：${d.status}`;
      break;
    case "TurnFailed":
    case "host.error": {
      s.modelWarning = false;
      const id = d.turn_id ? `error:${d.turn_id}` : `error:${event.sequence}`;
      const message = d.error || d.message || "执行失败";
      const modelFailure = message === "模型调用失败";
      const presentation = {
        role: modelFailure ? "assistant" : "error",
        error: !modelFailure,
        phase: modelFailure ? "final_answer" : undefined,
        streaming: false,
        createdAt: d.createdAt,
      };
      update(
        id,
        (m) => ({ ...m, ...presentation, content: message }),
        {
          ...base,
          id,
          ...presentation,
        },
      );
      s.status = "执行失败";
      break;
    }
    case "TurnCompleted":
      s.modelWarning = false;
      s.status = "已完成";
      break;
    case "TurnCancelled":
      s.modelWarning = false;
      s.status = "回答已停止";
      s.messages = s.messages.map((m) =>
        m.turnId === d.turn_id && m.streaming
          ? { ...m, streaming: false, createdAt: m.createdAt || (m.role === "assistant" ? d.createdAt : undefined) }
          : m,
      );
      if (!s.messages.some((m) => m.id === `stopped:${d.turn_id}`))
        s.messages = [
          ...s.messages,
          {
            id: `stopped:${d.turn_id}`,
            turnId: d.turn_id,
            role: "stopped",
            content: "回答已停止",
          },
        ];
      break;
    case "run.settled":
      s.modelWarning = false;
      s.running = false;
      s.pending = [];
      s.messages = s.messages.map((m) =>
        m.streaming ? { ...m, streaming: false } : m,
      );
      break;
    default:
      return previous;
  }
  return s;
}
export interface Draft {
  modelNotice?: string;
  content: string;
  attachments: Attachment[];
  directory: string;
  provider: string;
  model: string;
  reasoning: string | null;
  request?: Record<string, any>;
  uncertain?: boolean;
}
export const freshDraft = (): Draft => ({
  content: "",
  attachments: [],
  directory: "",
  provider: "",
  model: "",
  reasoning: null,
});

export function awaitingFirstReply(
  conversation: Conversation,
  submitting: boolean,
): boolean {
  if (
    (!conversation.running && !submitting) ||
    conversation.pending.length ||
    (!submitting &&
      ["执行失败", "已停止", "回答已停止", "已完成", "正在停止"].includes(
        conversation.status,
      ))
  )
    return false;
  for (let i = conversation.messages.length - 1; i >= 0; i--) {
    const message = conversation.messages[i];
    if (message.role === "error") return false;
    if (message.role === "assistant" && message.content.trim())
      return message.phase === "commentary" || !message.streaming;
    if (["tool", "tool-result"].includes(message.role)) return true;
    if (message.role === "user") return true;
  }
  return conversation.running || submitting;
}
