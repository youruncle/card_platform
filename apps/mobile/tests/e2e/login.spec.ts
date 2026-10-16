import { test, expect } from "@playwright/test";

test("fixed cloud login: phone form, readable failures, lock countdown and retry", async ({ page }) => {
  test.skip(!process.env.MOBILE_LOGIN_UI_URL, "Set MOBILE_LOGIN_UI_URL to an isolated cloud-targeting UI server.");
  const destination: string[] = [];
  let attempts = 0;
  await page.route("https://cardsp.cn/**", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type" } });
      return;
    }
    destination.push(route.request().url());
    expect(route.request().postDataJSON()).toEqual({ email: "fixture@example.com", password: "fixture-password" });
    attempts++;
    await route.fulfill({
      status: attempts === 1 ? 401 : 429,
      contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": "*", "Retry-After": "121" },
      body: JSON.stringify({ detail: attempts === 1 ? "邮箱或密码错误，请核对后重试。" : { code: "account_temporarily_locked", message: "登录尝试过多，请稍后重试。", retry_after_seconds: 121 } }),
    });
  });
  await page.goto(process.env.MOBILE_LOGIN_UI_URL!);
  await expect(page.getByRole("heading", { name: "登录你的卡库" })).toBeVisible();
  await expect(page.getByLabel("服务地址")).toHaveCount(0);
  await page.getByLabel("用户名（邮箱）").fill("fixture@example.com");
  const password = page.getByLabel("密码", { exact: true });
  await password.fill("fixture-password");
  await page.getByRole("button", { name: "显示密码" }).click();
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "隐藏密码" }).click();
  await expect(password).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("邮箱或密码错误");
  await page.clock.install();
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("2 分 1 秒");
  const submit = page.getByRole("button", { name: "请稍后重试", exact: true });
  await expect(submit).toBeDisabled();
  expect(destination).toEqual(["https://cardsp.cn/v1/mobile/auth/login", "https://cardsp.cn/v1/mobile/auth/login"]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../../work/mobile/login-cloud-refresh.png", fullPage: true });
  await page.clock.fastForward(121000);
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeEnabled();
});
