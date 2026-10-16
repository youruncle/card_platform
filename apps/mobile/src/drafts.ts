import { get, set, del } from "idb-keyval";
import {
  batchDrafts,
  type CaptureDraft,
} from "../../../packages/card-core/src/capture";
export type Draft = CaptureDraft;
export function pairPhotos(photos: Blob[]): Draft[] {
  return batchDrafts(photos, "pairs");
}
function asQueue(value: unknown): Draft[] {
  if (!value) return [];
  return Array.isArray(value) ? value : [value as Draft];
}
interface Storage {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
  del(key: string): Promise<void>;
}
export class DraftStore {
  private key: string;
  private closed = false;
  private listeners = new Set<() => void>();
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  private changed() {
    for (const listener of this.listeners) listener();
  }
  private pending: Promise<void> = Promise.resolve();
  constructor(
    origin: string,
    owner: string,
    private storage: Storage = { get, set, del },
  ) {
    this.key = `capture:${origin}:${owner}`;
  }
  async loadAll(): Promise<Draft[]> {
    // A rejected edit must not prevent reading the last successfully saved queue.
    await this.pending.catch(() => {});
    return asQueue(await this.storage.get(this.key));
  }
  async load(): Promise<Draft | undefined> {
    return (await this.loadAll())[0];
  }
  save(draft: Draft): Promise<void> {
    return this.saveAll([draft]);
  }
  saveAll(drafts: Draft[], expectedIds?: string[]): Promise<void> {
    if (this.closed) return Promise.reject(new Error("草稿会话已关闭"));
    this.pending = this.pending
      .catch(() => {})
      .then(async () => {
        if (this.closed) throw new Error("草稿会话已关闭");
        if (expectedIds) {
          const current = asQueue(await this.storage.get(this.key))
            .filter((d) => d.front || d.back)
            .map((d) => d.requestId);
          if (JSON.stringify(current) !== JSON.stringify(expectedIds))
            throw new Error("草稿已更新，请检查当前照片后重试");
        }
        await this.storage.set(this.key, drafts);
        this.changed();
      });
    return this.pending;
  }
  clearIfRequestId(id: string): Promise<void> {
    this.pending = this.pending
      .catch(() => {})
      .then(async () => {
        const current = asQueue(await this.storage.get(this.key));
        const remaining = current.filter((draft) => draft.requestId !== id);
        if (remaining.length) await this.storage.set(this.key, remaining);
        else await this.storage.del(this.key);
        this.changed();
      });
    return this.pending;
  }
  dispose() {
    this.closed = true;
    this.listeners.clear();
  }
  clear(): Promise<void> {
    this.pending = this.pending
      .catch(() => {})
      .then(async () => {
        await this.storage.del(this.key);
        this.changed();
      });
    return this.pending;
  }
}
