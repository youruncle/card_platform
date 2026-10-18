import { it, expect, vi } from "vitest";
import {
  captureForm,
  imageVariantPath,
  canSubmitCapture,
  batchDrafts,
  swapDraftSides,
} from "../../../packages/card-core/src/capture";
import { MobileApi } from "../src/api";
import { loadCapabilities } from "../src/capabilities";
it("single-side capture is explicit and never manufactures the other side", () => {
  const front = new Blob(["front"]);
  expect(canSubmitCapture({ front }, true)).toBe(false);
  expect(canSubmitCapture({ front, singleSide: true }, true)).toBe(true);
  expect(canSubmitCapture({ front, singleSide: true }, false)).toBe(false);
  const f = captureForm({ front, singleSide: true });
  expect(f.has("front")).toBe(true);
  expect(f.has("back")).toBe(false);
  expect(() => captureForm({})).toThrow();
});
it("source copies and recipes survive swapping and multipart upload", () => {
  const source = new Blob(["source"]),
    cropped = new Blob(["crop"]);
  const d = swapDraftSides({
    requestId: "a",
    front: cropped,
    frontSource: source,
    recipes: { front: { rotation: 90 } },
  });
  expect(d.backSource).toBe(source);
  expect(d.back).toBe(cropped);
  expect(d.recipes?.back).toEqual({ rotation: 90 });
  expect(d.requestId).not.toBe("a");
  const form = captureForm({
    ...d,
    targetItemId: "item",
    expectedRevision: "v1",
  });
  expect(form.has("back_source")).toBe(true);
  expect(form.get("target_item_id")).toBe("item");
  expect(form.get("crop_recipe")).toBe('{"back":{"rotation":90}}');
});
it("batch front-only and back-only retain side and one draft per photo", () => {
  const photos = [new Blob(["a"]), new Blob(["b"]), new Blob(["c"])];
  expect(
    batchDrafts(photos, "front").every(
      (d) => d.front && !d.back && d.singleSide,
    ),
  ).toBe(true);
  expect(batchDrafts(photos, "back")).toHaveLength(3);
  expect(batchDrafts(photos, "pairs")).toHaveLength(2);
});
it("variant paths are bound to resource side and revision, not another host", () => {
  expect(imageVariantPath("id", "front", "/uploads/abc.jpg", "thumb")).toBe(
    "/v1/portfolio/items/id/images/front?variant=thumb&revision=abc.jpg",
  );
  expect(imageVariantPath("id", "back", null, "thumb")).toBeNull();
  expect(
    imageVariantPath("id", "front", "https://other.test/x.jpg", "thumb"),
  ).toBe("https://other.test/x.jpg");
});
it("capability fallback applies only to missing endpoint", async () => {
  const transport = vi.fn(async () => new Response("{}", { status: 404 }));
  const api = new MobileApi("https://example.test", "t", () => {}, transport);
  expect((await loadCapabilities(api)).capture_jobs).toBe(false);
  transport.mockImplementation(async () => new Response("{}", { status: 503 }));
  await expect(loadCapabilities(api)).rejects.toThrow();
  api.dispose();
});

it('private images retry temporary generation failures only a bounded number of times',async()=>{
 vi.useFakeTimers();
 const transport=vi.fn(async()=>new Response('{}',{status:503}));
 const api=new MobileApi('https://example.test','t',()=>{},transport);
 try {
 const result=expect(api.image('/v1/portfolio/items/i/images/front?variant=thumb')).rejects.toThrow();
 await vi.advanceTimersByTimeAsync(2500);await result;expect(transport).toHaveBeenCalledTimes(3);
 transport.mockClear();transport.mockImplementation(async()=>new Response('{}',{status:404}));
 await expect(api.image('/uploads/deleted.jpg')).rejects.toThrow();expect(transport).toHaveBeenCalledOnce();
 }finally{api.dispose();vi.useRealTimers();}
});

it("shared default sorting opts into page-only projection, including subsequent snapshots", async () => {
  const {portfolioSortQuery, DEFAULT_PORTFOLIO_SORT} = await import("../../../packages/card-core/src/sorting");
  const first = new URLSearchParams(portfolioSortQuery(DEFAULT_PORTFOLIO_SORT).slice(1));
  const next = new URLSearchParams(portfolioSortQuery(DEFAULT_PORTFOLIO_SORT, "token").slice(1));
  expect(first.get("snapshot_mode")).toBe("membership");
  expect(next.get("snapshot_mode")).toBe("membership");
  expect(next.get("snapshot_token")).toBe("token");
  expect(portfolioSortQuery({sort_by:"fair_value",order:"desc"})).not.toContain("membership");
});
