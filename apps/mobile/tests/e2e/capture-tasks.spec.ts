import { test, expect } from "@playwright/test";

test("phone task center shows scanner failures, current-photo review, recovery history and request errors", async ({
  page,
}, testInfo) => {
  const user = {
    id: "task-fixture-owner",
    email: "task@example.com",
    display_name: "Task Fixture",
    base_currency: "CNY",
    role: "member",
  };
  const failure = {
    id: "scan",
    status: "failed",
    display_status: "failed",
    source: "scanner",
    require_confirmation: false,
    collection_item_id: null,
    target_item_id: null,
    result: null,
    attempt_count: 3,
    created_at: "2026-10-19T00:00:00Z",
    revision: 1,
    front_image_url: "/v1/capture/jobs/scan/images/front",
    back_image_url: "/v1/capture/jobs/scan/images/back",
    images: {},
    available_sides: ["front", "back"],
    error_message: "识别服务超时，请核对照片或重试",
    attention_reasons: ["识别服务超时，请核对照片或重试"],
    can_retry: true,
    image_content_hashes: { front: "current-front", back: "current-back" },
    replacement_job_id: null,
    history: [
      {
        id: "failure",
        occurred_at: "2026-10-19T00:01:00Z",
        attempt_count: 3,
        display_status: "failed",
        message: "第3次识别超时",
        source: "worker",
      },
    ],
  };
  let current = structuredClone(failure);
  let failing = false;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
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
      expect(request.headers().authorization).toBe("Bearer task-fixture-token");
    let result: unknown;
    if (path.endsWith("/auth/login"))
      result = {
        user,
        token: "task-fixture-token",
        expires_at: "2099-01-01T00:00:00Z",
      };
    else if (path.endsWith("/client-capabilities"))
      result = { capture_jobs: true, portfolio_sort: true };
    else if (path.endsWith("/items/page"))
      result = { items: [], total: 0, page: 1, total_pages: 0 };
    else if (/\/images\/(front|back)$/.test(path))
      return route.fulfill({
        headers,
        contentType: "image/svg+xml",
        body: `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="336"><rect width="240" height="336" fill="${path.endsWith("front") ? "#264e3a" : "#9d7837"}"/><text x="25" y="170" fill="white" font-size="24">Task ${path.endsWith("front") ? "front" : "back"}</text></svg>`,
      });
    else if (path.endsWith("/tasks")) {
      expect(url.searchParams.get("page_size")).toBe("20");
      if (failing)
        return route.fulfill({
          status: 503,
          headers,
          json: { detail: "云端任务暂不可用，请重试" },
        });
      result = {
        items: [current],
        total: 121,
        page: Number(url.searchParams.get("page")),
        page_size: 20,
        total_pages: 7,
        counts: {
          processing: current.display_status === "processing" ? 1 : 0,
          needs_review: 0,
          failed: current.display_status === "failed" ? 1 : 0,
          imported: 0,
          cancelled: 0,
          total: 121,
        },
      };
    } else if (path.endsWith("/tasks/scan/retry")) {
      expect(request.method()).toBe("POST");
      current = {
        ...current,
        status: "queued",
        display_status: "processing",
        can_retry: false,
        attention_reasons: [],
        error_message: "",
        history: [
          ...current.history,
          {
            id: "retry",
            occurred_at: "2026-10-19T00:02:00Z",
            attempt_count: 3,
            display_status: "processing",
            message: "用户请求一次新尝试",
            source: "manual",
          },
        ],
      };
      result = current;
    } else if (path.endsWith("/tasks/scan")) result = current;
    else
      return route.fulfill({
        status: 404,
        headers,
        json: { detail: `Unexpected fixture route: ${path}` },
      });
    return route.fulfill({ headers, json: result });
  });
  await page.goto("/");
  await page.getByLabel("用户名（邮箱）", { exact: true }).fill(user.email);
  await page.getByLabel("密码", { exact: true }).fill("test-only-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: /任务/ }).click();
  await expect(page.getByLabel("任务状态")).toHaveValue("attention");
  await expect(
    page.getByText("识别服务超时，请核对照片或重试", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("第 1 / 7 页 · 共 121 条")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("task-list-phone.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /新的录卡任务/ }).click();
  await expect(page.getByAltText("任务正面", { exact: true })).toBeVisible();
  await expect(page.getByAltText("任务背面", { exact: true })).toBeVisible();
  await expect(page.getByText("第3次识别超时", { exact: true })).toBeVisible();
  await expect(page.getByLabel("新秀卡")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "确认入库", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("我已查看当前正反面，确认属于同一张卡").check();
  await expect(
    page.getByRole("button", { name: "确认入库", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("task-failure-detail-phone.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "重新识别", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "处理中", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("第3次识别超时", { exact: true })).toBeVisible();
  await expect(
    page.getByText("用户请求一次新尝试", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "‹ 返回任务" }).click();
  failing = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "云端任务暂不可用，请重试",
  );
  await expect(page.getByText("当前筛选下没有任务")).toHaveCount(0);
  expect(errors).toEqual([]);
});
