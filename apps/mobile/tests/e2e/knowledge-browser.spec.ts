import { test, expect } from "@playwright/test";

test("published knowledge on a phone preserves filters, authenticates images and closes the full-screen viewer", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const version = "published-fixture-20";
  const user = {
    id: "fixture-owner",
    email: "fixture@example.com",
    display_name: "Fixture",
    base_currency: "CNY",
  };
  const product = {
    id: "fixture-product",
    name: "Topps Chrome",
    manufacturer: "Topps",
    brand: "Chrome",
    season_start: 2025,
    season_end: 2026,
    sport: "basketball",
    league: "NBA",
    aliases: ["铬"],
    description: "已发布清单资料",
    card_count: 2,
    image_count: 2,
    paired_card_count: 1,
    text_status: "partial",
    source_url: "https://example.com/checklist",
    source_authority: "official",
    cover_image_url: null,
  };
  const card = {
    id: "fixture-card",
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
    image_count: 2,
    front_image_url: "/v1/knowledge/browse/media/front/image",
    back_image_url: "/v1/knowledge/browse/media/back/image",
    sources: [
      {
        url: "https://example.com/checklist",
        authority: "official",
        locator: "page 3",
      },
    ],
  };
  const media = ["front", "back"].map((side) => ({
    id: side,
    product_id: product.id,
    card_id: card.id,
    image_url: `/v1/knowledge/browse/media/${side}/image`,
    thumbnail_url: `/v1/knowledge/browse/media/${side}/image?thumbnail=true`,
    side,
    image_type: "verified_card_scan",
    source_url: "https://example.com/image",
    source_authority: "reviewed",
    caption: side === "front" ? "核实正面" : "核实背面",
    player: card.player,
    card_number: "30",
    parallel: "Silver",
    pair_id: "fixture-pair",
  }));
  const errors: string[] = [];
  const images: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const list = (items: unknown[]) => ({
    items,
    total: items.length,
    offset: 0,
    limit: 24,
    version: 20,
  });
  await page.route("https://cardsp.cn/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    const headers = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "content-type,authorization",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    };
    if (request.method() === "OPTIONS")
      return route.fulfill({ status: 204, headers });
    if (!path.endsWith("/auth/login"))
      expect(request.headers().authorization).toBe("Bearer fixture-token");
    let result: unknown;
    if (path.endsWith("/auth/login"))
      result = {
        user,
        token: "fixture-token",
        expires_at: "2099-01-01T00:00:00Z",
      };
    else if (path.endsWith("client-capabilities"))
      result = { portfolio_sort: true };
    else if (path.endsWith("/items/page"))
      result = { items: [], total: 0, page: 1, total_pages: 0 };
    else if (path.endsWith("/auth/sessions")) result = [];
    else if (path.endsWith("/summary"))
      result = {
        version: 20,
        version_id: version,
        discovery_complete: false,
        summary: {
          manufacturers: 2,
          products: 1,
          card_facts: 2,
          images: 2,
          paired_cards: 1,
        },
        manufacturers: [
          { name: "Topps", product_count: 1, card_count: 2, image_count: 2 },
          {
            name: "Upper Deck",
            product_count: 0,
            card_count: 0,
            image_count: 0,
          },
        ],
        seasons: [2025],
        entity_types: [{ type: "parallel", count: 1 }],
        issues: [{ source_key: "history", message: "历史来源缺失" }],
      };
    else {
      expect(url.searchParams.get("version_id")).toBe(version);
      if (/\/media\/(front|back)\/image$/.test(path)) {
        images.push(request.url());
        return route.fulfill({
          headers,
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="336"><rect width="240" height="336" fill="${path.includes("front") ? "#264e3a" : "#d5be87"}"/><text x="30" y="170" fill="white" font-size="24">Fixture ${path.includes("front") ? "front" : "back"}</text></svg>`,
        });
      }
      if (path.endsWith("/products")) result = list([product]);
      else if (path.endsWith("/products/fixture-product"))
        result = {
          product,
          sections: [{ name: "Base", card_count: 2 }],
          parallels: [{ name: "Silver", card_count: 1 }],
          version: 20,
        };
      else if (path.endsWith("/cards/fixture-card"))
        result = { card, images: media, related_versions: [], version: 20 };
      else if (path.endsWith("/cards")) result = list([card]);
      else if (path.endsWith("/media")) result = list(media);
      else if (path.endsWith("/entities"))
        result = list([
          {
            id: "silver",
            entity_type: "parallel",
            canonical_name: "Silver",
            aliases: ["银折"],
            description: "已发布版本说明",
            source_url: "https://example.com/silver",
          },
        ]);
      else
        return route.fulfill({
          status: 404,
          headers,
          json: { detail: `Unknown fixture route: ${path}` },
        });
    }
    return route.fulfill({ headers, json: result });
  });
  const noOverflow = async () =>
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  await page.goto("/");
  await page.getByLabel("用户名（邮箱）").fill(user.email);
  await page.getByLabel("密码", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: /我的/ })
    .click();
  await page.getByRole("button", { name: /^知识查询/ }).click();
  await expect(page.getByRole("heading", { name: "卡牌知识库" })).toBeVisible();
  await page.getByLabel("厂商", { exact: true }).selectOption("Topps");
  await page.getByLabel("赛季", { exact: true }).selectOption("2025");
  await page.getByLabel("知识关键词").fill("铬");
  await page.getByRole("search").getByRole("button", { name: "搜索" }).click();
  await expect(
    page.getByRole("button", { name: "Topps Chrome", exact: true }),
  ).toBeVisible();
  const emptyCover = await page
    .getByText("暂无系列图片", { exact: true })
    .boundingBox();
  const productTile = await page
    .locator("article")
    .filter({
      has: page.getByRole("button", { name: "Topps Chrome", exact: true }),
    })
    .boundingBox();
  expect(emptyCover!.x + emptyCover!.width).toBeLessThanOrEqual(
    productTile!.x + productTile!.width,
  );
  await expect(page.getByRole("navigation", { name: "知识浏览" })).toHaveCSS(
    "position",
    "static",
  );
  await noOverflow();
  await page.screenshot({
    path: "../../.runtime/knowledge-browser-verification/mobile-fixture-catalogue.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Topps Chrome", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Topps Chrome", exact: true }),
  ).toBeVisible();
  await noOverflow();
  await page.getByRole("button", { name: "子系列与平行" }).click();
  await page.getByRole("button", { name: "Silver · 1" }).click();
  await page.getByRole("button", { name: /Curry #30 Silver/ }).click();
  await expect(
    page.getByRole("heading", { name: "Curry #30 Silver" }),
  ).toBeVisible();
  await noOverflow();
  const open = page.getByRole("button", { name: "查看图片：核实正面" });
  await page.screenshot({
    path: "../../.runtime/knowledge-browser-verification/mobile-fixture-card.png",
    fullPage: true,
  });
  await open.click();
  const dialog = page.getByRole("dialog", { name: "图片查看器" });
  await expect(
    dialog.getByRole("img", { name: "核实正面", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("img", { name: "核实正面", exact: true }),
  ).toHaveJSProperty("naturalWidth", 240);
  for (let step = 0; step < 8; step++)
    await dialog.getByRole("button", { name: "放大", exact: true }).click();
  const stage = dialog.locator(".kb-viewer-stage").first();
  async function allCornersReachable() {
    for (const [right, bottom] of [[false, false], [true, false], [false, true], [true, true]]) {
      await stage.evaluate((element, position) => {
        element.scrollLeft = position.right ? element.scrollWidth : 0;
        element.scrollTop = position.bottom ? element.scrollHeight : 0;
      }, { right, bottom });
      await expect.poll(() => stage.evaluate((element, position) => {
        const image = element.querySelector("img")!.getBoundingClientRect();
        const viewport = element.getBoundingClientRect();
        return (position.right ? image.right <= viewport.right + 1 : image.left >= viewport.left - 1)
          && (position.bottom ? image.bottom <= viewport.bottom + 1 : image.top >= viewport.top - 1);
      }, { right, bottom })).toBe(true);
    }
  }
  await allCornersReachable();
  await dialog.getByRole("button", { name: "旋转", exact: true }).click();
  await allCornersReachable();
  await dialog.getByRole("button", { name: "复位", exact: true }).click();
  await dialog.getByRole("button", { name: "背面", exact: true }).click();
  await dialog.getByLabel("对照图片").selectOption("front");
  await expect(
    dialog.getByRole("img", { name: "对照：核实正面" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "放大", exact: true }).click();
  await dialog.getByRole("button", { name: "旋转", exact: true }).click();
  await noOverflow();
  await page.screenshot({
    path: "../../.runtime/knowledge-browser-verification/mobile-fixture-viewer.png",
    fullPage: false,
  });
  await page.goBack();
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
  await page.getByRole("button", { name: "返回系列" }).click();
  await expect(page.getByLabel("平行版本", { exact: true })).toHaveValue(
    "Silver",
  );
  await page.getByRole("button", { name: "返回目录" }).click();
  await expect(page.getByLabel("知识关键词")).toHaveValue("铬");
  await expect(page.getByLabel("厂商", { exact: true })).toHaveValue("Topps");
  await page.getByRole("button", { name: "知识词条", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Silver", exact: true }),
  ).toBeVisible();
  await noOverflow();
  expect(images.length).toBeGreaterThanOrEqual(5);
  expect(errors).toEqual([]);
});
