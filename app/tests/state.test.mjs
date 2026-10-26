import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const tmp = await mkdtemp(path.join(os.tmpdir(), "corki-state-test-"));
await build({
  entryPoints: ["renderer/state.ts"],
  outfile: path.join(tmp, "state.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
});
const { reduceEvent, emptyConversation, conversationFromSnapshot } =
  await import(pathToFileURL(path.join(tmp, "state.mjs")));
test.after(() => rm(tmp, { recursive: true, force: true }));
function event(type, data) {
  return { type, data, threadId: "test", instance: "test", sequence: 1 };
}
test("model failure warning is replayable and clears on every terminal", () => {
  const warned = reduceEvent(emptyConversation(), event("model.health", { warning: true }));
  assert.equal(warned.modelWarning, true);
  for (const type of ["TurnFailed", "TurnCompleted", "TurnCancelled", "host.error", "run.settled"]) {
    assert.equal(reduceEvent(warned, event(type, { turn_id: "t", error: "模型调用失败" })).modelWarning, false);
  }
  const restored = conversationFromSnapshot({
    threadId: "test", running: true, messages: [], pending: [],
    live: [{ type: "model.health", data: { warning: true } }],
  });
  assert.equal(restored.modelWarning, true);
});
test("model failure budget is displayed as a normal reply without changing failed status", () => {
  const failed = reduceEvent(emptyConversation(), event("TurnFailed", {
    turn_id: "t", error: "模型调用失败", createdAt: "2026-10-25T00:00:00Z",
  }));
  const reply = failed.messages.at(-1);
  assert.equal(reply.role, "assistant");
  assert.equal(reply.content, "模型调用失败");
  assert.equal(reply.phase, "final_answer");
  assert.equal(reply.error, false);
  assert.equal(failed.status, "执行失败");
  assert.equal(failed.modelWarning, false);
});
test("model warning reason survives snapshot replay and distinguishes silence from failures", () => {
  for (const reason of ["idle", "failure"]) {
    const data = { warning: true, reason };
    const warned = reduceEvent(emptyConversation(), event("model.health", data));
    assert.equal(warned.modelWarningReason, reason);
    const restored = conversationFromSnapshot({
      threadId: "test", running: true, messages: [], pending: [],
      live: [{ type: "model.health", data }],
    });
    assert.equal(restored.modelWarningReason, reason);
    const cleared = reduceEvent(warned, event("model.health", { warning: false }));
    assert.equal(cleared.modelWarningReason, undefined);
    assert.equal(reduceEvent(warned, event("TurnCancelled", {})).modelWarningReason, undefined);
  }
});
test("incremental assistant completion replaces partial content without duplicate", () => {
  let s = emptyConversation();
  s = reduceEvent(s, event("TurnStarted", { turn_id: "t" }));
  s = reduceEvent(
    s,
    event("AssistantTextDelta", { turn_id: "t", item_id: "m", delta: "Hel" }),
  );
  s = reduceEvent(
    s,
    event("AssistantTextDelta", { turn_id: "t", item_id: "m", delta: "lo" }),
  );
  assert.equal(s.messages[0].content, "Hello");
  s = reduceEvent(
    s,
    event("AssistantMessageCompleted", {
      turn_id: "t",
      item_id: "m",
      text: "Hello!",
    }),
  );
  assert.equal(s.messages.length, 1);
  assert.equal(s.messages[0].content, "Hello!");
  s = reduceEvent(s, event("TurnCompleted", {}));
  assert.equal(s.running, true);
  s = reduceEvent(s, event("run.settled", {}));
  assert.equal(s.running, false);
});
test("failure preserves streamed answer and separates error", () => {
  let s = reduceEvent(
    emptyConversation(),
    event("AssistantTextDelta", { item_id: "m", delta: "partial" }),
  );
  s = reduceEvent(s, event("TurnFailed", { error: "connection lost" }));
  assert.equal(s.messages[0].content, "partial");
  assert.equal(s.messages[1].role, "error");
});
test("raw reasoning is never rendered", () => {
  const s = reduceEvent(
    emptyConversation(),
    event("AssistantReasoningDelta", { channel: "raw", delta: "private" }),
  );
  assert.equal(s.messages.length, 0);
});
test("Chat Completions anonymous deltas adopt canonical completion identity", () => {
  let s = reduceEvent(
    emptyConversation(),
    event("AssistantTextDelta", {
      turn_id: "t",
      item_id: null,
      delta: "partial",
    }),
  );
  s = reduceEvent(
    s,
    event("AssistantMessageCompleted", {
      turn_id: "t",
      item_id: "canonical",
      text: "complete",
    }),
  );
  assert.equal(s.messages.length, 1);
  assert.equal(s.messages[0].id, "canonical");
  s = reduceEvent(
    s,
    event("AssistantTextDelta", {
      turn_id: "t",
      item_id: null,
      delta: "next response",
    }),
  );
  s = reduceEvent(
    s,
    event("AssistantMessageCompleted", {
      turn_id: "t",
      item_id: "second",
      text: "next response",
    }),
  );
  assert.equal(s.messages.length, 2);
  assert.equal(s.messages[0].content, "complete");
});
test("replayed tool starts and pending interactions retain one identity", () => {
  const started = event("ToolCallStarted", {
    tool_call_id: "call",
    tool_name: "fixture",
    arguments_preview: "{}",
  });
  let s = reduceEvent(emptyConversation(), started);
  s = reduceEvent(s, started);
  assert.equal(s.messages.length, 1);
  const question = event("UserInputRequested", {
    call_id: "question",
    questions: [],
  });
  s = reduceEvent(reduceEvent(s, question), question);
  assert.equal(s.pending.length, 1);
  s = reduceEvent(s, event("interaction.resolved", { requestId: "question" }));
  assert.equal(s.pending.length, 0);
});

test("snapshot preserves canonical previews while retaining the unfinished response", () => {
  const saved = {
    id: "canonical",
    role: "assistant",
    content: "bounded preview",
    sourceId: "canonical",
    threadId: "test",
    nextText: 32000,
    truncated: true,
  };
  const data = {
    ...emptyConversation(),
    threadId: "test",
    instance: "test",
    running: true,
    messages: [saved],
    pending: [],
    live: [
      event("AssistantTextDelta", { turn_id: "t", delta: "already saved" }),
      event("AssistantMessageCompleted", {
        turn_id: "t",
        item_id: "canonical",
        text: "full unbounded saved text",
      }),
      event("AssistantTextDelta", {
        turn_id: "t",
        delta: "next unfinished answer",
      }),
      event("UserInputRequested", { call_id: "answered", questions: [] }),
    ],
  };
  const view = conversationFromSnapshot(data);
  assert.equal(view.messages.length, 2);
  assert.equal(view.messages[0], saved);
  assert.equal(!!view.messages[0].streaming, false);
  assert.equal(view.messages[1].content, "next unfinished answer");
  assert.equal(view.messages[1].streaming, true);
  assert.deepEqual(view.pending, []);
  assert.equal(view.status, "正在执行");
  assert.equal(data.messages[0], saved);
});

test("snapshot overlap never duplicates named deltas or revives finished tools", () => {
  const messages = [
    { id: "answer", role: "assistant", content: "saved" },
    { id: "tool:done", role: "tool", callId: "done", content: "{}" },
    {
      id: "output:done",
      role: "tool-result",
      callId: "done",
      content: "output",
    },
    { id: "tool:active", role: "tool", callId: "active", content: "{}" },
  ];
  const view = conversationFromSnapshot({
    ...emptyConversation(),
    threadId: "test",
    instance: "test",
    running: true,
    messages,
    pending: [{ request_id: "current" }],
    live: [
      event("AssistantTextDelta", { item_id: "answer", delta: "saved" }),
      event("ToolCallStarted", { tool_call_id: "done" }),
      event("ToolOutputDelta", { tool_call_id: "done", delta: "output" }),
      event("ToolCallStarted", { tool_call_id: "active" }),
    ],
  });
  assert.equal(view.messages[0].content, "saved");
  assert.equal(view.messages[1].streaming, false);
  assert.equal(view.messages[2].content, "output");
  assert.equal(view.messages[3].streaming, true);
  assert.equal(view.pending[0].request_id, "current");
});

test("goal continuation stays running across turn boundaries until host settlement", () => {
  let state = emptyConversation();
  for (const [type, data] of [
    ["TurnStarted", { turn_id: "first" }],
    ["GoalUpdated", { status: "active" }],
    ["TurnCompleted", {}],
    ["TurnStarted", { turn_id: "continuation" }],
    ["GoalUpdated", { status: "paused" }],
    ["TurnCancelled", {}],
  ]) {
    state = reduceEvent(state, event(type, data));
    assert.equal(state.running, true, type);
  }
  state = reduceEvent(state, event("run.settled", {}));
  assert.equal(state.running, false);
  assert.equal(state.status, "回答已停止");
});

test("accepted input stays visible before persistence and is replaced by canonical turn", () => {
  const pending = {
    type: "UserMessageSubmitted",
    data: { request_id: "r1", turn_id: "t1", text: "question" },
  };
  const snapshot = {
    ...emptyConversation(),
    threadId: "thread",
    instance: "test",
    running: true,
    live: [pending],
  };
  let view = conversationFromSnapshot(snapshot);
  assert.deepEqual(
    view.messages.map((m) => m.content),
    ["question"],
  );
  view = conversationFromSnapshot({
    ...snapshot,
    messages: [
      { id: "canonical", turnId: "t1", role: "user", content: "question" },
    ],
  });
  assert.equal(view.messages.length, 1);
  assert.equal(view.messages[0].id, "canonical");
  // Identical text from another turn is never used as a deduplication key.
  view = conversationFromSnapshot({
    ...snapshot,
    messages: [
      { id: "older", turnId: "t0", role: "user", content: "question" },
    ],
  });
  assert.equal(view.messages.length, 2);
});

test("snapshot refresh preserves loaded prefix, cursor and unchanged message identities", async () => {
  const { mergeSnapshot } = await import(
    pathToFileURL(path.join(tmp, "state.mjs"))
  );
  const messages = Array.from({ length: 100 }, (_, i) => ({
    id: `m${i}`,
    role: "user",
    content: `${i}`,
  }));
  const previous = { ...emptyConversation(), loaded: true, messages, next: 10 };
  const incoming = {
    ...emptyConversation(),
    loaded: true,
    messages: messages.slice(40).map((m) => ({ ...m })),
    next: 50,
  };
  const merged = mergeSnapshot(previous, incoming);
  assert.equal(merged.messages.length, 100);
  assert.equal(merged.next, 10);
  for (let i = 0; i < 100; i++) assert.equal(merged.messages[i], messages[i]);
  const changed = mergeSnapshot(previous, {
    ...incoming,
    messages: [
      ...incoming.messages.slice(0, -1),
      { ...messages[99], content: "updated" },
    ],
  });
  assert.equal(changed.messages[98], messages[98]);
  assert.notEqual(changed.messages[99], messages[99]);
  // A disjoint page must not hide an unread history gap or resurrect old content.
  const disjoint = mergeSnapshot(previous, {
    ...incoming,
    messages: [{ id: "new", role: "user", content: "new" }],
  });
  assert.equal(disjoint.messages.length, 1);
  assert.equal(disjoint.next, 50);
});

test("status and unknown events do not copy unchanged message arrays", () => {
  const previous = {
    ...emptyConversation(),
    messages: [{ id: "a", role: "assistant", content: "text" }],
  };
  assert.equal(
    reduceEvent(previous, event("TurnStarted", {})).messages,
    previous.messages,
  );
  assert.equal(reduceEvent(previous, event("unhandled", {})), previous);
  const updated = reduceEvent(
    previous,
    event("AssistantTextDelta", { item_id: "a", delta: "!" }),
  );
  assert.equal(previous.messages[0].content, "text");
  assert.equal(updated.messages[0].content, "text!");
});

test("thinking is scoped to the current question and hides for replies, errors, stops and questions", async () => {
  const { awaitingFirstReply: show } = await import(
    pathToFileURL(path.join(tmp, "state.mjs"))
  );
  const active = {
    ...emptyConversation(),
    running: true,
    messages: [
      { id: "old", role: "assistant", content: "old answer" },
      { id: "new", role: "user", content: "new question" },
    ],
  };
  assert(show(active, false));
  assert(show({ ...active, running: false }, true));
  assert(!show({ ...active, running: false }, false));
  assert(
    !show(
      {
        ...active,
        messages: [
          ...active.messages,
          { id: "a", role: "assistant", content: "reply", streaming: true },
        ],
      },
      false,
    ),
  );
  assert(!show({ ...active, pending: [{ kind: "question" }] }, false));
  for (const status of ["已完成", "已停止", "正在停止", "执行失败"])
    assert(!show({ ...active, status }, false));
  assert(
    !show(
      {
        ...active,
        messages: [
          ...active.messages,
          { id: "e", role: "error", content: "failed" },
        ],
      },
      false,
    ),
  );
});

test("cancellation retains streamed answer and emits one stopped marker", () => {
  const prior = {
    ...emptyConversation(),
    running: true,
    messages: [
      {
        id: "partial",
        turnId: "t",
        role: "assistant",
        content: "partial answer",
        streaming: true,
      },
    ],
  };
  const cancelled = reduceEvent(
    prior,
    event("TurnCancelled", { turn_id: "t", createdAt: "2026-10-19T08:30:00Z" }),
  );
  assert.equal(cancelled.messages[0].createdAt, "2026-10-19T08:30:00Z");
  assert.equal(cancelled.messages[0].content, "partial answer");
  assert.equal(cancelled.messages[0].streaming, false);
  assert.equal(cancelled.messages[1].content, "回答已停止");
  assert.equal(
    reduceEvent(cancelled, event("TurnCancelled", { turn_id: "t" })).messages
      .length,
    2,
  );
});

test("new admission after a failed or completed turn still shows thinking; tool continuation resumes it", async () => {
  const { awaitingFirstReply: show } = await import(
    pathToFileURL(path.join(tmp, "state.mjs"))
  );
  for (const status of ["执行失败", "已完成", "回答已停止"]) {
    const s = {
      ...emptyConversation(),
      status,
      messages: [
        { id: "old-error", role: "error", content: "400" },
        { id: "new", role: "user", content: "next" },
      ],
    };
    assert(show(s, true));
  }
  const s = {
    ...emptyConversation(),
    running: true,
    status: "执行 exec_command",
    messages: [
      { id: "u", role: "user", content: "question" },
      { id: "a", role: "assistant", content: "trying another method" },
      { id: "tool", role: "tool", content: "command" },
    ],
  };
  assert(show(s, false));
});

test("failed turn events reconcile with the same canonical error identity", () => {
  let s = emptyConversation();
  s = reduceEvent(s, event("TurnFailed", { turn_id: "failed", error: "400" }));
  s = reduceEvent(s, event("TurnFailed", { turn_id: "failed", error: "400" }));
  assert.equal(s.messages.length, 1);
  assert.equal(s.messages[0].id, "error:failed");
  assert.equal(s.messages[0].turnId, "failed");
});

test("snapshot replay of commentary and a new streamed answer keeps each item once", () => {
  const messages = [
    { id: "u", role: "user", content: "question", turnId: "t" },
    {
      id: "progress",
      role: "assistant",
      content: "trying another method",
      turnId: "t",
    },
  ];
  const live = [
    {
      type: "AssistantTextDelta",
      data: { turn_id: "t", delta: "trying another method" },
    },
    {
      type: "AssistantMessageCompleted",
      data: {
        turn_id: "t",
        item_id: "progress",
        text: "trying another method",
      },
    },
    { type: "AssistantTextDelta", data: { turn_id: "t", delta: "new answer" } },
  ];
  const view = conversationFromSnapshot({
    ...emptyConversation(),
    threadId: "thread",
    instance: "host",
    messages,
    running: true,
    live,
  });
  assert.deepEqual(
    view.messages.map((m) => m.content),
    ["question", "trying another method", "new answer"],
  );
});

test('accepted startup without a persisted user row retains a waiting indicator', async()=>{
 const {awaitingFirstReply:show}=await import(pathToFileURL(path.join(tmp,'state.mjs')));
 assert(show({...emptyConversation(),running:true},false));
});

test("message timestamps survive live events and authoritative history", () => {
  let s = reduceEvent(emptyConversation(), event("UserMessageSubmitted", {
    request_id: "r", text: "hi", createdAt: "2026-10-17T05:00:00Z",
  }));
  assert.equal(s.messages[0].createdAt, "2026-10-17T05:00:00Z");
  s = reduceEvent(s, event("AssistantTextDelta", {turn_id: "t", item_id: "a", delta: "hello"}));
  assert.equal(s.messages.at(-1).createdAt, undefined);
  s = reduceEvent(s, event("AssistantMessageCompleted", {
    turn_id: "t", item_id: "a", text: "hello", createdAt: "2026-10-17T05:01:00Z",
  }));
  assert.equal(s.messages.at(-1).createdAt, "2026-10-17T05:01:00Z");
});

test('refresh keeps a loaded question ahead of earlier tools instead of duplicating context anchor', async () => {
  const {mergeSnapshot} = await import(pathToFileURL(path.join(tmp, 'state.mjs')));
  const question={id:'u',role:'user',content:'question',turnId:'t'};
  const early={id:'early',role:'assistant',content:'progress',turnId:'t'};
  const tail={id:'tail',role:'assistant',content:'answer',turnId:'t'};
  const previous={...emptyConversation(),messages:[question,early,tail],next:null};
  const incoming={...emptyConversation(),messages:[{...question,contextOnly:true},tail],next:100};
  const merged=mergeSnapshot(previous,incoming);
  assert.deepEqual(merged.messages.map(m=>m.id),['u','early','tail']);
  assert.equal(merged.messages[0].contextOnly,undefined);
});

test("completed progress messages keep waiting visible until the turn finishes", async () => {
  const { awaitingFirstReply } = await import(pathToFileURL(path.join(tmp, "state.mjs")));
  let s = reduceEvent(emptyConversation(), event("TurnStarted", {turn_id:"t"}));
  s = reduceEvent(s,event("AssistantMessageCompleted",{turn_id:"t",item_id:"p",text:"Checking remaining items",phase:"commentary"}));
  assert.equal(s.messages[0].phase,"commentary");
  assert(awaitingFirstReply(s,false));
  s = reduceEvent(s,event("TurnCompleted",{turn_id:"t"}));
  assert(!awaitingFirstReply(s,false));
});

test("live replay does not append older off-page progress after newer canonical history", () => {
  const messages = [
    {id:"middle",role:"assistant",turnId:"t",content:"middle",phase:"commentary"},
    {id:"tool:c",role:"tool",turnId:"t",callId:"c",content:"{}",name:"test"},
  ];
  const live = [
    event("TurnStarted",{turn_id:"t"}),
    event("AssistantMessageCompleted",{turn_id:"t",item_id:"first",text:"first",phase:"commentary"}),
    event("AssistantMessageCompleted",{turn_id:"t",item_id:"middle",text:"middle",phase:"commentary"}),
    event("ToolCallStarted",{turn_id:"t",tool_call_id:"c",tool_name:"test",arguments_preview:"{}"}),
    event("AssistantTextDelta",{turn_id:"t",item_id:"last",delta:"latest"}),
  ];
  const s=conversationFromSnapshot({...emptyConversation(),threadId:"test",instance:"test",running:true,messages,live});
  assert.deepEqual(s.messages.map(m=>m.id),["first","middle","tool:c","last"]);
  assert.equal(s.messages[1],messages[0]);
});

test("live-only unfinished reply follows history without overlap; question anchors stay first",()=>{
 const history={id:"old",role:"assistant",turnId:"old",content:"old"};
 const live=[event("AssistantTextDelta",{turn_id:"t",item_id:"new",delta:"new"})];
 let s=conversationFromSnapshot({...emptyConversation(),threadId:"test",instance:"test",running:true,messages:[history],live});
 assert.deepEqual(s.messages.map(m=>m.id),["old","new"]);
 const question={id:"q",role:"user",turnId:"t",content:"q",contextOnly:true};
 const middle={id:"m",role:"assistant",turnId:"t",content:"m"};
 s=conversationFromSnapshot({...emptyConversation(),threadId:"test",instance:"test",running:true,messages:[question,middle],live:[
 event("AssistantMessageCompleted",{turn_id:"t",item_id:"early",text:"early"}),
 event("AssistantMessageCompleted",{turn_id:"t",item_id:"m",text:"m"})]});
 assert.deepEqual(s.messages.map(m=>m.id),["q","early","m"]);
});

test("progress phase survives deltas before message completion", async()=>{
 const {awaitingFirstReply}=await import(pathToFileURL(path.join(tmp,"state.mjs")));
 let s=reduceEvent(emptyConversation(),event("TurnStarted",{turn_id:"t"}));
 s=reduceEvent(s,event("AssistantTextDelta",{turn_id:"t",item_id:"p",delta:"working",phase:"commentary"}));
 assert.equal(s.messages[0].phase,"commentary");
 assert(awaitingFirstReply(s,false));
 s=reduceEvent(s,event("AssistantTextDelta",{turn_id:"t",item_id:"p",delta:"…"}));
 assert.equal(s.messages[0].phase,"commentary");
});

test("cancellation snapshot keeps canonical question ahead of an initially provisional long turn", async()=>{
 const {mergeSnapshot}=await import(pathToFileURL(path.join(tmp,"state.mjs")));
 let previous=reduceEvent(emptyConversation(),event("UserMessageSubmitted",{request_id:"r",text:"question"}));
 previous=reduceEvent(previous,event("TurnStarted",{turn_id:"t"}));
 assert.equal(previous.messages[0].turnId,"t");
 previous.messages.push(
  {id:"progress",role:"assistant",turnId:"t",content:"working",phase:"commentary"},
  {id:"tool:old",role:"tool",turnId:"t",content:"{}",streaming:false},
  {id:"tool:anchor",role:"tool",turnId:"t",content:"{}",streaming:false});
 const incoming={...emptyConversation(),next:20,messages:[
  {id:"canonical-question",role:"user",turnId:"t",content:"question",contextOnly:true},
  {id:"tool:anchor",role:"tool",turnId:"t",content:"{}"},
  {id:"stopped:t",role:"stopped",turnId:"t",content:"stopped"}]};
 const merged=mergeSnapshot(previous,incoming);
 assert.deepEqual(merged.messages.map(m=>m.id),["canonical-question","progress","tool:old","tool:anchor","stopped:t"]);
});

test("older pages preserve canonical question anchors through empty pages and promote once", async () => {
  const { mergeOlderMessages } = await import(pathToFileURL(path.join(tmp, "state.mjs")));
  const q = { id: "question", role: "user", turnId: "turn", content: "question" };
  const anchor = { ...q, contextOnly: true };
  const tail = { id: "tail", role: "assistant", turnId: "turn", content: "tail" };
  const early = { ...tail, id: "early" };
  let messages = mergeOlderMessages([anchor, tail], []);
  assert.deepEqual(messages, [anchor, tail]);
  messages = mergeOlderMessages(messages, [anchor, early]);
  assert.deepEqual(messages, [anchor, early, tail]);
  messages = mergeOlderMessages(messages, [q]);
  assert.deepEqual(messages, [q, early, tail]);
});

test("snapshot refresh keeps anchors belonging to earlier loaded turns", async () => {
  const { mergeSnapshot } = await import(pathToFileURL(path.join(tmp, "state.mjs")));
  const q = { id: "q", role: "user", turnId: "old", content: "question", contextOnly: true };
  const old = { id: "old", role: "assistant", turnId: "old", content: "answer" };
  const fresh = { id: "fresh", role: "user", turnId: "new", content: "new question" };
  const previous = { ...emptyConversation(), messages: [q, old, fresh], next: 10 };
  const incoming = { ...emptyConversation(), messages: [fresh], next: 20 };
  assert.deepEqual(mergeSnapshot(previous, incoming).messages, [q, old, fresh]);
});

test('steering uses canonical identity across live events and snapshot recovery', () => {
  let state = reduceEvent(emptyConversation(), event('UserMessageSubmitted', {
    request_id:'r-steer', item_id:'canonical-steer', turn_id:'t', text:'additional question'
  }));
  assert.equal(state.messages[0].id, 'canonical-steer');
  assert.equal(state.messages[0].requestId, undefined);
  state = conversationFromSnapshot({ ...emptyConversation(), threadId:'thread', instance:'i',
    running:true, messages:[{id:'canonical-steer',role:'user',turnId:'t',content:'additional question'}],
    live:[{type:'UserMessageSubmitted',data:{request_id:'r-steer',item_id:'canonical-steer',turn_id:'t',text:'additional question'}}] });
  assert.equal(state.messages.length, 1);
});

test('queue changes do not insert unsubmitted questions into transcript', () => {
  const original = emptyConversation();
  const next = reduceEvent(original, event('queue.changed', {
    mode:'queue', paused:true, messages:[{id:'q',content:'later',state:'queued',attachments:[]}]
  }));
  assert.equal(next.followUps.messages.length,1);
  assert.equal(next.followUps.paused,true);
  assert.equal(next.messages.length,0);
  assert.equal(original.followUps.messages.length,0);
});
