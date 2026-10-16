import { test, expect } from "@playwright/test";

test("phone library retains state and scroll through tabs/details and retries incremental load", async ({page}) => {
  const calls: number[] = [];
  let fail = true;
  let secondAccount = false;
  const user = {id: "fixture-owner", email: "fixture@example.com", display_name: "Fixture", base_currency: "CNY"};
  const card = (id: number) => ({id: String(id), player_name: `Fixture ${id}`, season_start: 2026, brand_name: "Test", product_name: "Test", remaining_cost: "0"});
  await page.route("https://cardsp.cn/**", async route => {
    const url = new URL(route.request().url());
    const headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type,authorization"};
    if (route.request().method() === "OPTIONS") { await route.fulfill({status: 204, headers}); return; }
    let body: unknown = {};
    if (url.pathname.endsWith("/auth/login")) body = {user: secondAccount ? {...user, id: "second-owner"} : user, token: "fixture-token", expires_at: "2099-01-01T00:00:00Z"};
    else if (url.pathname.endsWith("client-capabilities")) body = {portfolio_sort: true};
    else if (url.pathname.endsWith("/items/page")) {
      const number = Number(url.searchParams.get("page")); calls.push(number);
      if (number === 2 && fail) { await route.fulfill({status: 503, headers, json: {detail: "fixture offline"}}); return; }
      body = {items: secondAccount ? [] : Array.from({length: number === 1 ? 30 : 5}, (_, i) => card((number - 1) * 30 + i + 1)), total: secondAccount ? 0 : 35, page: number, total_pages: secondAccount ? 1 : 2, snapshot_token: "fixture-snapshot"};
    } else if (url.pathname.endsWith("/detail")) body = {item: card(Number(url.pathname.split("/").at(-2)))};
    else if (url.pathname.endsWith("automated-valuation")) body = {comparables: []};
    else if (url.pathname.endsWith("/sessions")) body = [];
    await route.fulfill({headers, json: body});
  });
  await page.goto("/");
  await page.getByLabel("用户名（邮箱）").fill(user.email);
  await page.getByLabel("密码", {exact: true}).fill("fixture-password");
  await page.getByRole("button", {name: "登录", exact: true}).click();
  await expect(page.getByText("Fixture 1", {exact: true})).toBeVisible();
  await page.getByLabel("搜索卡片").fill("unsent query");
  await page.evaluate(() => window.scrollTo(0, 1200));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
  const scroll = await page.evaluate(() => window.scrollY);
  await page.getByRole("button", {name: "我的", exact: false}).click();
  await expect(page.getByRole("heading", {name: "我的", exact: true})).toBeVisible();
  await page.getByRole("button", {name: /卡库/}).click();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scroll);
  await expect(page.getByLabel("搜索卡片")).toHaveValue("unsent query");
  expect(calls).toEqual([1]);
  await page.getByRole("button", {name: /Fixture 15 /}).click();
  await page.getByRole("button", {name: "‹ 返回卡库"}).click();
  expect(calls).toEqual([1]);
  await page.getByRole("button", {name: "加载更多"}).scrollIntoViewIfNeeded();
  await expect(page.getByRole("alert")).toContainText("fixture offline");
  await expect(page.getByText("Fixture 1", {exact: true})).toHaveCount(1);
  fail = false;
  await page.getByRole("button", {name: "重试", exact: true}).click();
  await expect(page.getByText("Fixture 35", {exact: true})).toBeVisible();
  expect(calls).toEqual([1, 2, 2]);
  await page.getByRole("button", {name: "我的", exact: false}).click();
  await page.getByRole("button", {name: "退出登录", exact: true}).click();
  await expect(page.getByRole("heading", {name: "登录你的卡库"})).toBeVisible();
  secondAccount = true;
  await page.getByLabel("用户名（邮箱）").fill("second@example.com");
  await page.getByLabel("密码", {exact: true}).fill("fixture-password");
  await page.getByRole("button", {name: "登录", exact: true}).click();
  await expect(page.getByText("你的收藏，从第一张开始")).toBeVisible();
  await expect(page.getByText("Fixture 1", {exact: true})).toHaveCount(0);
  await expect(page.getByLabel("搜索卡片")).toHaveValue("");
});
