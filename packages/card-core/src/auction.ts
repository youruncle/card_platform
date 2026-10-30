export type AuctionPhoto = { id: string; side: 'front' | 'back' | 'detail'; source: 'existing' | 'upload' | 'camera' | 'redmi'; url: string; sha256: string };
export type AuctionDraft = {
  id: string; item_id: string; revision: number; status: 'draft'; title: string; description: string;
  condition_notes: string; selected_photo_ids: string[]; photos: AuctionPhoto[];
  removed_photo_ids?: string[];
  identity: Record<string, unknown>; generation_basis: Record<string, unknown>;
  external: { supported: false; status: 'unavailable'; reason: string }; updated_at: string;
};
export type AuctionRequest = <T>(path: string, options?: RequestInit) => Promise<T>;
type Editable = Pick<AuctionDraft, 'title' | 'description' | 'condition_notes' | 'selected_photo_ids'>;
export type AuctionEditorState = Editable & {
  draft: AuctionDraft | null; busy: boolean; error: string; notice: string; conflict: boolean;
  pendingFile: File | null; pendingSide: AuctionPhoto['side']; pendingSource: Exclude<AuctionPhoto['source'], 'existing'>;
  automaticGenerationPending: boolean; latestConflicts: Partial<Editable>;
};
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const editableKeys: (keyof Editable)[] = ['title', 'description', 'condition_notes', 'selected_photo_ids'];
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
const editable = (draft: AuctionDraft): Editable => ({ title: draft.title, description: draft.description, condition_notes: draft.condition_notes, selected_photo_ids: [...draft.selected_photo_ids] });
export const auctionSideName = (side: AuctionPhoto['side']) => ({ front: '正面', back: '背面', detail: '细节' })[side];
export class AuctionEditor {
  state: AuctionEditorState = { draft: null, title: '', description: '', condition_notes: '', selected_photo_ids: [], busy: false, error: '', notice: '', conflict: false, pendingFile: null, pendingSide: 'front', pendingSource: 'upload', automaticGenerationPending: false, latestConflicts: {} };
  private listeners = new Set<() => void>();
  private controller: AbortController | null = null;
  private textEdits = new Set<keyof Editable>();
  private pendingUpload: { file: File; side: AuctionPhoto['side']; source: Exclude<AuctionPhoto['source'], 'existing'>; requestId: string } | null = null;
  constructor(private request: AuctionRequest) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.state;
  private update(value: Partial<AuctionEditorState>) { this.state = { ...this.state, ...value }; for (const listener of this.listeners) listener(); }
  private async run(operation: (signal: AbortSignal) => Promise<void>) {
    if (this.state.busy) return;
    const controller = new AbortController(); this.controller = controller;
    this.update({ busy: true, error: '', notice: '' });
    try { await operation(controller.signal); }
    catch (error) {
      if (!controller.signal.aborted) {
        const conflict = (error as { status?: number })?.status === 409;
        this.update({ conflict, error: conflict ? '草稿版本冲突，另一端可能已修改。请读取最新版本核对后再保存；本页输入已保留。' : error instanceof Error ? error.message : '请求失败，请重试；本页输入已保留。' });
      }
    } finally { if (this.controller === controller) { this.controller = null; this.update({ busy: false }); } }
  }
  private async call(path: string, options: RequestInit, signal: AbortSignal) {
    const result = await this.request<AuctionDraft>(path, { ...options, signal });
    if (signal.aborted) throw new Error('请求已取消');
    return result;
  }
  async open(itemId: string) {
    await this.run(async signal => {
      const draft = await this.call(`/v1/auction/items/${encodeURIComponent(itemId)}/draft`, { method: 'POST' }, signal);
      this.textEdits.clear(); this.update({ draft, ...editable(draft), automaticGenerationPending: false, latestConflicts: {} });
    });
  }
  edit(value: Partial<Editable>) {
    if (this.state.busy) return;
    const latestConflicts = { ...this.state.latestConflicts };
    for (const key of Object.keys(value) as (keyof Editable)[]) {
      if (same(value[key], this.state.draft?.[key])) { this.textEdits.delete(key); delete latestConflicts[key]; }
      else this.textEdits.add(key);
    }
    this.update({ ...value, latestConflicts, notice: '' });
  }
  select(id: string) {
    if (!this.state.draft?.photos.some(photo => photo.id === id)) return;
    const ids = this.state.selected_photo_ids;
    if (!ids.includes(id) && ids.length >= 12) { this.update({ error: '最多选择 12 张照片' }); return; }
    this.edit({ selected_photo_ids: ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id] });
  }
  async save() {
    if (!this.state.draft || this.state.conflict) return;
    await this.run(async signal => {
      const { draft, title, description, condition_notes, selected_photo_ids } = this.state;
      const result = await this.call(`/v1/auction/drafts/${draft!.id}`, json('PATCH', { revision: draft!.revision, title, description, condition_notes, selected_photo_ids }), signal);
      this.textEdits.clear(); this.update({ draft: result, ...editable(result), latestConflicts: {}, notice: '平台草稿已保存' });
    });
  }
  async changePhoto(id: string, restore = false) {
    if (!this.state.draft || this.state.busy || this.state.conflict) return;
    await this.run(async signal => {
      const { draft, selected_photo_ids } = this.state;
      const path = `/v1/auction/drafts/${draft!.id}/images/${encodeURIComponent(id)}${restore ? '/restore' : ''}`;
      const result = await this.call(path, json(restore ? 'POST' : 'DELETE', { revision: draft!.revision, selected_photo_ids }), signal);
      this.textEdits.delete('selected_photo_ids');
      const latestConflicts = { ...this.state.latestConflicts }; delete latestConflicts.selected_photo_ids;
      this.update({ draft: result, selected_photo_ids: result.selected_photo_ids, latestConflicts,
        notice: restore ? '照片已恢复，原有文案已保留。' : '照片已从草稿移除，可撤销。原卡库与手机图片、手工文案均保留；需要时可重新生成文案。' });
    });
  }
  async refresh() {
    if (!this.state.draft) return;
    await this.run(async signal => {
      const previous = this.state.draft!;
      const draft = await this.call(`/v1/auction/drafts/${previous.id}`, {}, signal);
      const merged = editable(draft), latestConflicts: Partial<Editable> = {};
      for (const key of editableKeys) {
        if (!this.textEdits.has(key)) continue;
        if (!same(draft[key], previous[key]) && !same(draft[key], this.state[key])) Object.assign(latestConflicts, { [key]: draft[key] });
        Object.assign(merged, { [key]: this.state[key] });
      }
      if (this.textEdits.has('selected_photo_ids')) {
        const valid = new Set(draft.photos.map(photo => photo.id));
        const newlySelected = draft.selected_photo_ids.filter(id => !previous.photos.some(photo => photo.id === id));
        merged.selected_photo_ids = [...new Set([...merged.selected_photo_ids.filter(id => valid.has(id)), ...newlySelected])].slice(0, 12);
      }
      for (const key of editableKeys) { if (same(merged[key], draft[key])) this.textEdits.delete(key); }
      this.update({ draft, ...merged, latestConflicts, conflict: false, notice: '已读取最新版本，合并其他端未冲突的修改；保留本页编辑，请对照后保存。' });
    });
  }
  private async generateDraft(signal: AbortSignal, automatic: boolean) {
    const { draft, condition_notes, selected_photo_ids } = this.state;
    if (selected_photo_ids.length < 1 || selected_photo_ids.length > 12) throw new Error('请至少选择 1 张照片，最多 12 张');
    const overrides = automatic ? { ...(this.textEdits.has('title') ? { edited_title: this.state.title } : {}), ...(this.textEdits.has('description') ? { edited_description: this.state.description } : {}) } : {};
    const result = await this.call(`/v1/auction/drafts/${draft!.id}/generate`, json('POST', { revision: draft!.revision, selected_photo_ids, condition_notes, use_order_examples: true, automatic, ...overrides }), signal);
    this.textEdits.clear();
    const retained = automatic && (Array.isArray(result.generation_basis.manual_fields) && result.generation_basis.manual_fields.length > 0 || Object.keys(overrides).length > 0);
    this.update({ draft: result, ...editable(result), automaticGenerationPending: false, latestConflicts: {}, notice: retained ? '新照片已记录，手工文案已保留并保存至平台草稿。' : '文案已生成，可编辑后保存平台草稿' });
  }
  async generate() { if (!this.state.draft || this.state.conflict) return; await this.run(signal => this.generateDraft(signal, this.state.automaticGenerationPending)); }
  async add(file: File, side: AuctionPhoto['side'], source: Exclude<AuctionPhoto['source'], 'existing'>) {
    if (!this.state.draft || this.state.busy || this.state.conflict) return;
    if (!this.pendingUpload || this.pendingUpload.file !== file || this.pendingUpload.side !== side || this.pendingUpload.source !== source) this.pendingUpload = { file, side, source, requestId: globalThis.crypto.randomUUID() };
    const requestId = this.pendingUpload.requestId;
    this.update({ pendingFile: file, pendingSide: side, pendingSource: source });
    await this.run(async signal => {
      const previous = this.state.draft!;
      const form = new FormData(); form.append('revision', String(previous.revision)); form.append('image', file, file.name); form.append('side', side); form.append('source', source); form.append('request_id', requestId);
      const draft = await this.call(`/v1/auction/drafts/${previous.id}/images`, { method: 'POST', body: form }, signal);
      const newIds = draft.photos.filter(photo => !previous.photos.some(old => old.id === photo.id)).map(photo => photo.id);
      this.pendingUpload = null;
      this.update({ draft, pendingFile: null, selected_photo_ids: [...new Set([...this.state.selected_photo_ids, ...newIds])].slice(0, 12), automaticGenerationPending: true });
      await this.generateDraft(signal, true);
    });
  }
  dispose() { this.controller?.abort(); this.controller = null; this.pendingUpload = null; this.state = { ...this.state, busy: false, pendingFile: null }; }
}
