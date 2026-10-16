import { describe, expect, it } from "vitest";
import { DraftStore, pairPhotos } from "../src/drafts";
import { photoGeometry } from "../src/capture";
function storage() {
  const values = new Map<string, unknown>();
  return {
    values,
    get: async (k: string) => values.get(k),
    set: async (k: string, v: unknown) => {
      values.set(k, v);
    },
    del: async (k: string) => {
      values.delete(k);
    },
  };
}
const front = new Blob(["front"]),
  back = new Blob(["back"]);
describe("multi-card capture", () => {
  it("pairs in reviewed order and retains an odd final front without inventing a back", () => {
    const photos = [front, back, new Blob(["last"])];
    const drafts = pairPhotos(photos);
    expect(drafts).toHaveLength(2);
    expect(drafts[0].front).toBe(front);
    expect(drafts[0].back).toBe(back);
    expect(drafts[1].front).toBe(photos[2]);
    expect(drafts[1].back).toBeUndefined();
    expect(drafts[0].requestId).not.toBe(drafts[1].requestId);
  });
  it("restores a queue across restart, removes only a confirmed upload, and isolates owners", async () => {
    const backend = storage();
    const store = new DraftStore("https://cards.example", "a", backend);
    const drafts = pairPhotos([front, back, front, back]);
    await store.saveAll(drafts);
    const restored = new DraftStore("https://cards.example", "a", backend);
    expect(await restored.loadAll()).toEqual(drafts);
    await restored.clearIfRequestId(drafts[0].requestId);
    expect(await restored.loadAll()).toEqual([drafts[1]]);
    expect(
      await new DraftStore("https://cards.example", "b", backend).loadAll(),
    ).toEqual([]);
    await restored.clear();
    expect(await restored.loadAll()).toEqual([]);
  });
  it("reads legacy drafts and never clears a newer edit after an old request succeeds", async () => {
    const backend = storage();
    backend.values.set("capture:https://cards.example:a", {
      requestId: "legacy",
      front,
    });
    const store = new DraftStore("https://cards.example", "a", backend);
    expect((await store.loadAll())[0].requestId).toBe("legacy");
    await store.saveAll([{ requestId: "new-edit", front, back }]);
    await store.clearIfRequestId("legacy");
    expect((await store.loadAll())[0].requestId).toBe("new-edit");
    store.dispose();
    await expect(store.saveAll([])).rejects.toThrow();
  });
});
describe("photo processing bounds", () => {
  it("limits resolution, retains aspect ratio and applies rotation after crop", () => {
    expect(photoGeometry(4000, 3000, 90)).toMatchObject({
      width: 1920,
      height: 2560,
    });
    expect(
      photoGeometry(4000, 3000, 0, { x: 0.25, y: 0, width: 0.5, height: 1 }),
    ).toMatchObject({
      sx: 1000,
      sy: 0,
      sw: 2000,
      sh: 3000,
      width: 1707,
      height: 2560,
    });
    expect(photoGeometry(600, 800, 0)).toMatchObject({
      width: 600,
      height: 800,
    });
  });
  it("rejects invalid and out-of-bounds crop before allocating a canvas", () => {
    for (const crop of [
      { x: -1, y: 0, width: 1, height: 1 },
      { x: 0.9, y: 0, width: 0.5, height: 1 },
      { x: 0, y: 0, width: 0, height: 1 },
    ])
      expect(() => photoGeometry(800, 600, 0, crop)).toThrow();
  });
});
it("rejects a stale page snapshot rather than resurrecting an uploaded draft", async () => {
  const backend = storage(),
    store = new DraftStore("https://cards.example", "a", backend);
  const originals = pairPhotos([front, back, front, back]);
  await store.saveAll(originals);
  await store.clearIfRequestId(originals[0].requestId);
  await expect(
    store.saveAll(
      originals,
      originals.map((d) => d.requestId),
    ),
  ).rejects.toThrow("草稿已更新");
  expect(await store.loadAll()).toEqual([originals[1]]);
});
