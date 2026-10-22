import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { KnowledgePage } from "../src/KnowledgePage";
import { ApiError, type MobileApi } from "../src/api";
import type {
  KnowledgeBrowserSummary,
  KnowledgeCard,
  KnowledgeMedia,
  KnowledgeProduct,
} from "../../../packages/card-core/src/knowledge-browser";

const summary: KnowledgeBrowserSummary = {
  version: 20,
  version_id: "snapshot/20",
  discovery_complete: false,
  summary: {
    manufacturers: 3,
    products: 25,
    card_facts: 81,
    images: 4,
    paired_cards: 1,
  },
  manufacturers: [
    { name: "Topps", product_count: 25, card_count: 81, image_count: 4 },
    { name: "Upper Deck", product_count: 0, card_count: 0, image_count: 0 },
  ],
  seasons: [2025, 2024],
  entity_types: [{ type: "parallel", count: 2 }],
  issues: [{ source_key: "historical-source", message: "历史清单缺失" }],
};
const product: KnowledgeProduct = {
  id: "product/one",
  name: "Topps Chrome",
  manufacturer: "Topps",
  brand: "Chrome",
  season_start: 2025,
  season_end: 2026,
  sport: "basketball",
  league: "NBA",
  aliases: ["铬"],
  description: "官方清单收录的系列",
  card_count: 81,
  image_count: 4,
  paired_card_count: 1,
  text_status: "partial",
  source_url: "https://example.com/checklist",
  source_authority: "official",
  cover_image_url: null,
};
const card: KnowledgeCard = {
  id: "card/one",
  name: "Curry #30 Silver",
  product_id: product.id,
  product_name: product.name,
  manufacturer: "Topps",
  season_start: 2025,
  season_end: 2026,
  player: "Stephen Curry",
  card_number: "30",
  section: "Base",
  parallel: "Silver",
  serial_total: null,
  is_rookie: null,
  is_autograph: false,
  is_patch: null,
  image_count: 0,
  front_image_url: null,
  back_image_url: null,
  sources: [
    {
      url: "https://example.com/card",
      authority: "official",
      locator: "page 3",
    },
  ],
};
const media: KnowledgeMedia[] = [
  {
    id: "front",
    product_id: product.id,
    card_id: card.id,
    image_url: "/v1/knowledge/browse/media/front/image",
    thumbnail_url: "/v1/knowledge/browse/media/front/image?thumbnail=true",
    side: "front",
    image_type: "verified_specimen",
    source_url: "https://example.com/front",
    source_authority: "verified",
    caption: "核实正面",
    player: "Curry",
    card_number: "30",
    parallel: "Silver",
    pair_id: "pair-one",
  },
  {
    id: "back",
    product_id: product.id,
    card_id: card.id,
    image_url: "/v1/knowledge/browse/media/back/image",
    thumbnail_url: "/v1/knowledge/browse/media/back/image?thumbnail=true",
    side: "back",
    image_type: "verified_specimen",
    source_url: "https://example.com/back",
    source_authority: "verified",
    caption: "核实背面",
    player: "Curry",
    card_number: "30",
    parallel: "Silver",
    pair_id: "pair-one",
  },
  {
    id: "design",
    product_id: product.id,
    card_id: null,
    image_url: "/v1/knowledge/browse/media/design/image",
    thumbnail_url: "/v1/knowledge/browse/media/design/image?thumbnail=true",
    side: "unknown",
    image_type: "official_design",
    source_url: null,
    source_authority: "official",
    caption: "官方设计示意",
    player: null,
    card_number: null,
    parallel: null,
    pair_id: null,
  },
];
function page<T>(items: T[], total = items.length, offset = 0, limit = 24) {
  return { items, total, offset, limit, version: 20 };
}
function fixture(override?: (url: URL) => unknown | Promise<unknown>) {
  const get = vi.fn(async (path: string) => {
    const url = new URL(path, "https://cards.example");
    const changed = override?.(url);
    if (changed !== undefined) return await changed;
    if (url.pathname.endsWith("/summary")) return summary;
    if (url.pathname.endsWith("/products")) return page([product]);
    if (url.pathname.endsWith("/products/product%2Fone"))
      return {
        product,
        sections: [{ name: "Base", card_count: 81 }],
        parallels: [{ name: "Silver", card_count: 8 }],
        version: 20,
      };
    if (url.pathname.endsWith("/cards/card%2Fone"))
      return {
        card,
        images: [],
        related_versions: [
          { ...card, id: "gold", name: "Curry #30 Gold", parallel: "Gold" },
        ],
        version: 20,
      };
    if (url.pathname.endsWith("/cards/gold"))
      return {
        card: { ...card, id: "gold", name: "Curry #30 Gold", parallel: "Gold" },
        images: [],
        related_versions: [],
        version: 20,
      };
    if (url.pathname.endsWith("/cards")) return page([card], 1, 0, 40);
    if (url.pathname.endsWith("/media")) return page(media);
    if (url.pathname.endsWith("/entities"))
      return page(
        [
          {
            id: "parallel-silver",
            entity_type: "parallel",
            canonical_name: "Silver",
            aliases: ["银折"],
            description: "官方平行版本",
            source_url: "https://example.com/silver",
          },
        ],
        1,
        0,
        40,
      );
    throw new Error(`Unexpected route ${path}`);
  });
  const image = vi.fn(
    async (_path: string) => new Blob(["image"], { type: "image/jpeg" }),
  );
  return { api: { get, image } as unknown as MobileApi, get, image };
}
beforeEach(() => {
  let serial = 0;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => `blob:knowledge-${++serial}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows a gallery image's series and returns to the same gallery", async () => {
  const { api } = fixture((url) => url.pathname.endsWith("/media")
    ? page([{ ...media[2], product_name: "Topps Chrome", manufacturer: "Topps", season_start: 2025, season_end: 2026 }])
    : undefined);
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "图库" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看所属系列：Topps Chrome" }));
  await screen.findByRole("heading", { name: "Topps Chrome" });
  fireEvent.click(screen.getByRole("button", { name: "返回图库" }));
  expect(screen.getByRole("button", { name: "图库" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "查看所属系列：Topps Chrome" })).toBeTruthy();
});

it("translates source categories and keeps long source captions inside the viewer", async () => {
  const caption =
    "Manufacturer sell-sheet design example; page 5. Preview only; exact player, card number and parallel are not verified.";
  const { api } = fixture((url) =>
    url.pathname.endsWith("/cards/card%2Fone")
      ? {
          card: {
            ...card,
            sources: [
              {
                url: "https://example.com/card",
                authority: "manufacturer_document_third_party_host",
                locator: "page 3",
              },
            ],
          },
          images: [{ ...media[2], caption }],
          related_versions: [],
          version: 20,
        }
      : undefined,
  );
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "卡牌查询" }));
  fireEvent.click(
    await screen.findByRole("button", { name: /Curry #30 Silver/ }),
  );
  await screen.findByRole("heading", { name: "Curry #30 Silver" });
  expect(
    screen.getByRole("link", { name: "经销商托管原厂资料 · page 3" }),
  ).toBeTruthy();
  expect(screen.queryByText(caption)).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "查看图片：官方设计示意 · 第 5 页" }),
  );
  expect(within(screen.getByRole("dialog")).getByText(caption)).toBeTruthy();
});

it("browses published products with filters and pagination, preserving catalogue context after detail", async () => {
  const { api, get } = fixture((url) =>
    url.pathname.endsWith("/products")
      ? page([product], 25, Number(url.searchParams.get("offset")))
      : undefined,
  );
  const onBack = vi.fn();
  render(<KnowledgePage api={api} onBack={onBack} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  expect(screen.getByText(/已发布版本.*20/)).toBeTruthy();
  expect(screen.getByText(/历史清单缺失/)).toBeTruthy();
  expect(screen.getByText(/不代表完整收录/)).toBeTruthy();
  expect(
    within(screen.getByLabelText("厂商")).getByRole("option", {
      name: /Upper Deck/,
    }),
  ).toBeTruthy();
  fireEvent.change(screen.getByLabelText("厂商"), {
    target: { value: "Topps" },
  });
  fireEvent.change(screen.getByLabelText("赛季"), {
    target: { value: "2025" },
  });
  fireEvent.change(screen.getByLabelText("知识关键词"), {
    target: { value: "铬" },
  });
  fireEvent.submit(screen.getByRole("search"));
  await waitFor(() =>
    expect(
      get.mock.calls.some(
        ([path]) =>
          path.includes("q=%E9%93%AC") &&
          path.includes("manufacturer=Topps") &&
          path.includes("season=2025") &&
          path.includes("offset=0"),
      ),
    ).toBe(true),
  );
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("25–25 / 25");
  fireEvent.click(screen.getByRole("button", { name: /Topps Chrome/ }));
  await screen.findByRole("heading", { name: "Topps Chrome" });
  fireEvent.click(screen.getByRole("button", { name: "子系列与平行" }));
  await screen.findByRole("button", { name: /Silver.*8/ });
  fireEvent.click(screen.getByRole("button", { name: "返回目录" }));
  expect((screen.getByLabelText("厂商") as HTMLSelectElement).value).toBe(
    "Topps",
  );
  expect((screen.getByLabelText("知识关键词") as HTMLInputElement).value).toBe(
    "铬",
  );
  await screen.findByText("25–25 / 25");
  expect(
    get.mock.calls
      .filter(([path]) => !path.endsWith("/summary"))
      .every(
        ([path]) =>
          new URL(path, "https://a").searchParams.get("version_id") ===
          "snapshot/20",
      ),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "‹ 返回我的" }));
  expect(onBack).toHaveBeenCalledOnce();
});

it("ignores a late older search response", async () => {
  let resolveOld!: (value: unknown) => void;
  const old = new Promise((resolve) => {
    resolveOld = resolve;
  });
  const { api } = fixture((url) =>
    url.pathname.endsWith("/products") && url.searchParams.get("q") === "old"
      ? old
      : undefined,
  );
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.change(screen.getByLabelText("知识关键词"), {
    target: { value: "old" },
  });
  fireEvent.submit(screen.getByRole("search"));
  fireEvent.change(screen.getByLabelText("知识关键词"), {
    target: { value: "new" },
  });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByRole("button", { name: /Topps Chrome/ });
  await act(async () => {
    resolveOld(page([{ ...product, name: "Outdated product" }]));
  });
  expect(screen.queryByText("Outdated product")).toBeNull();
});

it("opens card facts and related versions while reporting exact front and back gaps honestly", async () => {
  const { api } = fixture();
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "卡牌查询" }));
  await screen.findByRole("button", { name: /Curry #30 Silver/ });
  fireEvent.change(screen.getByLabelText("实卡图片"), {
    target: { value: "missing_back" },
  });
  await screen.findByRole("button", { name: /Curry #30 Silver/ });
  fireEvent.click(screen.getByRole("button", { name: /Curry #30 Silver/ }));
  await screen.findByRole("heading", { name: "Curry #30 Silver" });
  expect(screen.getByText("未收录该卡正面图片")).toBeTruthy();
  expect(screen.getByText("未收录该卡背面图片")).toBeTruthy();
  expect(screen.getByText(/设计示意不能代替该卡实图/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /page 3/ }).getAttribute("href"),
  ).toBe("https://example.com/card");
  fireEvent.click(screen.getByRole("button", { name: /Curry #30 Gold/ }));
  await screen.findByRole("heading", { name: "Curry #30 Gold" });
  fireEvent.click(screen.getByRole("button", { name: "返回浏览" }));
  expect((screen.getByLabelText("实卡图片") as HTMLSelectElement).value).toBe(
    "missing_back",
  );
});

it("loads only authenticated blobs, compares actual paired sides, and restores focus when closing the viewer", async () => {
  const { api, image } = fixture();
  const view = render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "图库" }));
  const open = await screen.findByRole("button", {
    name: "查看图片：核实正面",
  });
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("img")
        .every((img) => img.getAttribute("src")?.startsWith("blob:")),
    ).toBe(true),
  );
  expect(screen.getByText(/侧别未知/)).toBeTruthy();
  expect(screen.getByText("官方设计示意 · 非逐卡实图")).toBeTruthy();
  open.focus();
  fireEvent.click(open);
  const dialog = await screen.findByRole("dialog", { name: "图片查看器" });
  expect(document.activeElement).toBe(
    within(dialog).getByRole("button", { name: "关闭图片" }),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "背面" }));
  await waitFor(() =>
    expect(
      image.mock.calls.some(
        ([path]) =>
          path.includes("/back/image?") && !path.includes("thumbnail=true"),
      ),
    ).toBe(true),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "放大" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "旋转" }));
  await waitFor(() =>
    expect(
      within(dialog)
        .getByRole("img", { name: "核实背面" })
        .getAttribute("style"),
    ).toContain("rotate(90deg)"),
  );
  fireEvent.change(within(dialog).getByLabelText("对照图片"), {
    target: { value: "front" },
  });
  await within(dialog).findByRole("img", { name: "对照：核实正面" });
  expect(within(dialog).getByText(/已核实同组正反面/)).toBeTruthy();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(open);
  view.unmount();
  await waitFor(() =>
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(
      vi.mocked(URL.createObjectURL).mock.calls.length,
    ),
  );
  expect(
    image.mock.calls.every(
      ([path]) =>
        new URL(path, "https://a").searchParams.get("version_id") ===
        "snapshot/20",
    ),
  ).toBe(true);
});

it("keeps failed media explicit and does not attach late image results after leaving gallery", async () => {
  let resolveImage!: (value: Blob) => void;
  const { api, image } = fixture();
  image.mockImplementationOnce(() =>
    Promise.reject(new ApiError(404, "图片文件缺失")),
  );
  image.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveImage = resolve;
      }),
  );
  const view = render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "图库" }));
  await screen.findByText(/图片文件缺失/);
  fireEvent.click(screen.getByRole("button", { name: "系列目录" }));
  view.unmount();
  const before = vi.mocked(URL.createObjectURL).mock.calls.length;
  await act(async () => {
    resolveImage(new Blob(["late"]));
  });
  expect(URL.createObjectURL).toHaveBeenCalledTimes(before);
});

it("searches published glossary by type and retains explicit source links", async () => {
  const { api, get } = fixture();
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "知识词条" }));
  await screen.findByRole("heading", { name: "Silver" });
  fireEvent.change(screen.getByLabelText("词条类型"), {
    target: { value: "parallel" },
  });
  fireEvent.change(screen.getByLabelText("知识关键词"), {
    target: { value: "银折" },
  });
  fireEvent.submit(screen.getByRole("search"));
  await waitFor(() =>
    expect(
      get.mock.calls.some(
        ([path]) =>
          path.includes("entity_type=parallel") &&
          path.includes("q=%E9%93%B6%E6%8A%98"),
      ),
    ).toBe(true),
  );
  expect(
    screen.getByRole("link", { name: "查看词条来源" }).getAttribute("href"),
  ).toBe("https://example.com/silver");
});

it("shows preparing and retry without confusing an unavailable catalogue with empty results", async () => {
  let attempt = 0;
  const { api } = fixture((url) =>
    url.pathname.endsWith("/summary") && ++attempt === 1
      ? Promise.reject(
          new ApiError(503, "知识目录索引正在准备，请稍后重试", undefined, 3),
        )
      : undefined,
  );
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("alert");
  expect(screen.queryByText(/没有符合条件/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await screen.findByRole("button", { name: /Topps Chrome/ });
});

it("stops automatic preparation retries honestly and allows a manual retry", async () => {
  vi.useFakeTimers();
  try {
    const { api, get } = fixture((url) => url.pathname.endsWith("/summary")
      ? Promise.reject(new ApiError(503, "知识目录索引正在准备，请稍后重试", undefined, 3))
      : undefined);
    render(<KnowledgePage api={api} onBack={() => {}} />);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(33000); });
    expect(screen.getByText("自动重试已停止，请点击重试继续浏览。")).toBeTruthy();
    const stoppedCount = get.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(get.mock.calls.length).toBe(stoppedCount);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await act(async () => { await Promise.resolve(); });
    expect(get.mock.calls.length).toBe(stoppedCount + 1);
  } finally {
    vi.useRealTimers();
  }
});

it("labels captured scan types and never counts a design-bound or unknown asset as an exact card face", async () => {
  const scans = [
    { ...media[0], image_type: "verified_card_scan" },
    {
      ...media[2],
      card_id: card.id,
      side: "back",
      image_type: "official_design_sample",
    },
    { ...media[1], image_type: "unknown", pair_id: "pair-one" },
  ];
  const { api } = fixture((url) =>
    url.pathname.endsWith("/cards/card%2Fone")
      ? { card, images: scans, related_versions: [], version: 20 }
      : undefined,
  );
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "卡牌查询" }));
  fireEvent.click(
    await screen.findByRole("button", { name: /Curry #30 Silver/ }),
  );
  await screen.findByRole("heading", { name: "Curry #30 Silver" });
  expect(screen.queryByText("未收录该卡正面图片")).toBeNull();
  expect(screen.getByText("未收录该卡背面图片")).toBeTruthy();
  expect(screen.getByText(/尚无已确认的该卡正反面配对/)).toBeTruthy();
  expect(screen.getByText("已核实实卡图片")).toBeTruthy();
  expect(screen.getByText("图片类型待核对")).toBeTruthy();
});

it("keeps product card section and parallel context while returning from a card", async () => {
  const { api, get } = fixture();
  render(<KnowledgePage api={api} onBack={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /Topps Chrome/ }));
  await screen.findByRole("heading", { name: "Topps Chrome" });
  fireEvent.click(screen.getByRole("button", { name: "子系列与平行" }));
  fireEvent.click(screen.getByRole("button", { name: /Silver.*8/ }));
  const result = await screen.findByRole("button", {
    name: /Curry #30 Silver/,
  });
  expect((screen.getByLabelText("平行版本") as HTMLSelectElement).value).toBe(
    "Silver",
  );
  expect(
    get.mock.calls.some(
      ([path]) =>
        path.includes("product_id=product%2Fone") &&
        path.includes("parallel=Silver"),
    ),
  ).toBe(true);
  fireEvent.click(result);
  await screen.findByRole("heading", { name: "Curry #30 Silver" });
  fireEvent.click(screen.getByRole("button", { name: "返回系列" }));
  expect(
    (screen.getByRole("combobox", { name: "平行版本" }) as HTMLSelectElement)
      .value,
  ).toBe("Silver");
  expect(screen.getByRole("button", { name: /Curry #30 Silver/ })).toBeTruthy();
});

it("closes the modal on browser back and never invents a switch for an unknown-side design", async () => {
  const { api } = fixture();
  render(<KnowledgePage api={api} onBack={() => {}} />);
  await screen.findByRole("button", { name: /Topps Chrome/ });
  fireEvent.click(screen.getByRole("button", { name: "图库" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "查看图片：官方设计示意" }),
  );
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("button", { name: "正面" })).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "背面" })).toBeNull();
  fireEvent.popState(window);
  expect(screen.queryByRole("dialog")).toBeNull();
});
