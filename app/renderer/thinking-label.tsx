import { useEffect, useState } from "react";
import type { Conversation } from "./state";

export function formatThinkingDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  return minutes ? `${minutes}m ${total % 60}s` : `${total}s`;
}

export function thinkingLabel(conversation: Pick<Conversation, "goal" | "runStartedAt" | "status">, now: number, fallback: number): string {
  const goal = conversation.goal;
  const activeGoal = goal?.status === "active";
  const seconds = activeGoal
    ? goal.time_used_seconds + Math.max(0, now / 1000 - goal.updated_at)
    : (now - (conversation.runStartedAt ?? fallback)) / 1000;
  const label = conversation.status === "正在压缩上下文" ? "压缩中" : activeGoal ? "执行Goal中" : "思考中";
  return `${label}（${formatThinkingDuration(seconds)}）`;
}

// Only this small label rerenders each second; chat history is untouched.
export function ThinkingLabel({ conversation }: { conversation: Conversation }) {
  const [fallback] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="thinking-label">{thinkingLabel(conversation, now, fallback)}</span>;
}
