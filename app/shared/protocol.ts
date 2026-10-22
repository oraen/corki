export const METHODS = [
  "initialize",
  "config.read",
  "threads.list",
  "threads.read",
  "messages.text",
  "messages.image",
  "assets.read",
  "threads.rename",
  "threads.delete",
  "threads.archive",
  "turn.submit",
  "queue.action",
  "turn.stop",
  "request.read",
  "interaction.respond",
  "state.read",
  "state.write",
  "events.replay",
  "status",
] as const;
export type Method = (typeof METHODS)[number];
export interface HostEvent {
  instance: string;
  sequence: number;
  type: string;
  threadId?: string;
  data: Record<string, any>;
}
export interface Attachment {
  id: string;
  name: string;
  type: "image" | "text";
  data: string;
  assetId?: string;
}
export interface Model {
  id: string;
  description: string;
  reasoningLevels: string[];
  reasoning: string | null;
  images: boolean;
}
export interface Catalog {
  valid: boolean;
  error?: string;
  providers: { id: string; models: Model[] }[];
  default?: { provider: string; model: string; reasoning: string | null };
  permissions?: string;
}
export interface DesktopAPI {
  request<T = any>(
    method: Method,
    params?: Record<string, unknown>,
  ): Promise<T>;
  subscribe(callback: (event: HostEvent) => void): () => void;
  takeLaunchDirectory?(): Promise<string | null>;
  chooseDirectory(): Promise<string | null>;
  chooseFiles(): Promise<Attachment[]>;
  importFiles(files: File[]): Promise<Attachment[]>;
  copy(text: string): Promise<void>;
  openLink(url: string): Promise<void>;
  restartBackend(): Promise<void>;
  restartApp(): Promise<boolean>;
  configurePython(): Promise<boolean>;
}
declare global {
  interface Window {
    corki: DesktopAPI;
  }
}

// Version 1 used threads.delete for archival and had no reminder preference.
export function assertThreadManagement(info: {
  threadManagementVersion?: number;
}) {
  if ((info.threadManagementVersion || 0) < 2) {
    throw new Error(
      "桌面后台版本较旧，请完全退出 Corki 后重新打开；仅刷新页面不会更新后台。",
    );
  }
}
