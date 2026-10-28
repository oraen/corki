import React, { useState } from "react";
import { Icon, MessageRow } from "./components";
import { DeferredMessage } from "./pending-inputs";
import type { TranscriptEntry } from "./transcript";

function ProcessGroup({
  entries,
  active,
  onError,
  showAvatar,
}: {
  showAvatar: boolean;
  entries: TranscriptEntry[];
  active: boolean;
  onError: (e: unknown) => void;
}) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const [pinned, setPinned] = useState(false);
  const open = choice ?? (active || pinned);

  const pending = entries.filter((entry) => entry.message.role === "tool" && entry.message.streaming);
  return (
    <>
    <details
      className="process-group"
      open={open}
      onClickCapture={(event) => {
        // The avatar shares the summary's layout, but is not a toggle target.
        if ((event.target as Element).closest(".process-heading > .turn-avatar")) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        const summary = (event.target as Element).closest("summary");
        if (!summary) return;
        const details = summary.parentElement as HTMLDetailsElement;
        if (details === event.currentTarget) {
          event.preventDefault();
          setChoice(!open);
        } else if (!details.open) {
          // Explicitly inspecting one tool keeps the process visible on completion.
          setPinned(true);
        }
      }}
    >
      <summary className="process-heading">
        {showAvatar && (
          <img
            className="avatar turn-avatar"
            src="resources/corki.png"
            alt="Corki"
          />
        )}
        <Icon name="chevron" size={14} />
        <span>执行过程</span>
        <span className="process-count">{entries.length} 项</span>
      </summary>
      <div className="process-body">
        {entries.map((entry) => (
          <MessageRow
            key={entry.message.id}
            {...entry}
            showAvatar={false}
            onError={onError}
          />
        ))}
      </div>
    </details>
    {!open && pending.length > 0 && (
      <div className="process-pending">
        {pending.map((entry) => (
          <MessageRow key={entry.message.id} {...entry} showAvatar={false} onError={onError} />
        ))}
      </div>
    )}
    </>
  );
}

export function TranscriptView({
  entries,
  active,
  onError,
  onRetryInput,
}: {
  entries: TranscriptEntry[];
  active: boolean;
  onError: (e: unknown) => void;
  onRetryInput: (id: string) => Promise<void>;
}) {
  const latestTurn = [...entries]
    .reverse()
    .find((entry) => entry.message.turnId)?.message.turnId;
  const blocks: {
    key: string;
    process: boolean;
    entries: TranscriptEntry[];
  }[] = [];
  for (const entry of entries) {
    const process =
      !!entry.commentary ||
      ["tool", "tool-result", "reasoning"].includes(entry.message.role);
    const previous = blocks.at(-1);
    if (
      process &&
      previous?.process &&
      previous.entries[0].message.turnId === entry.message.turnId
    )
      previous.entries.push(entry);
    else blocks.push({ key: entry.message.id, process, entries: [entry] });
  }
  // Keep the answer's controls at the beginning of the whole response,
  // even when process rows precede the final Markdown body.
  const groups: (typeof blocks)[] = [];
  for (const block of blocks) {
    const previous = groups.at(-1);
    const item = block.entries[0].message;
    if (
      item.role !== "user" &&
      previous &&
      previous[0].entries[0].message.role !== "user" &&
      previous[0].entries[0].message.turnId === item.turnId
    )
      previous.push(block);
    else groups.push([block]);
  }
  const renderBlock = (block: (typeof blocks)[number], showAvatar = false) => {
    if (block.entries[0].message.pendingInput)
      return <DeferredMessage key={block.key} message={block.entries[0].message}
        retry={onRetryInput} onError={onError} />;
    const current = active && block.entries[0].message.turnId === latestTurn;
    return block.process ? (
      <ProcessGroup
        key={`process:${block.key}`}
        entries={block.entries}
        showAvatar={showAvatar}
        active={current}
        onError={onError}
      />
    ) : (
      <MessageRow
        key={block.key}
        {...block.entries[0]}
        showAvatar={showAvatar}
        active={current}
        collapsible={!current}
        onError={onError}
      />
    );
  };
  return (
    <>
      {groups.map((group) =>
        group[0].entries[0].message.role === "user" ? (
          renderBlock(group[0])
        ) : (
          <div
            className={`reply-block ${group[0].process ? "starts-with-process" : ""}`}
            // Steered inputs share a turn; each response segment needs its own key.
            key={`reply:${group[0].key}`}
          >
            {group.map((block, index) => renderBlock(block, index === 0 && group.some((b) => b.entries.some((entry) => entry.showAvatar))))}
          </div>
        ),
      )}
    </>
  );
}
