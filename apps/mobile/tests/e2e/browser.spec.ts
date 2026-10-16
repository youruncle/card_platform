import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

test("phone viewport: login, empty library, upload, confirm fixture, private images and logout", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("用户名（邮箱）", { exact: true })
    .fill(process.env.MOBILE_TEST_EMAIL!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.MOBILE_TEST_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("你的收藏，从第一张开始")).toBeVisible();
  await page.screenshot({ path: "../../work/mobile/library-empty.png" });
  await page.getByRole("button", { name: "录卡", exact: false }).click();
  await expect(page.getByLabel("选择正面照片")).toBeEnabled();
  // Batch pairing stays device-only until the user submits a complete pair.
  await page
    .getByLabel("批量选择相册照片")
    .setInputFiles([
      process.env.MOBILE_TEST_IMAGE!,
      process.env.MOBILE_TEST_IMAGE!,
      process.env.MOBILE_TEST_IMAGE!,
    ]);
  await expect(
    page.getByRole("heading", { name: "核对配对顺序" }),
  ).toBeVisible();
  await page.getByLabel("照片 1 的位置").selectOption("2");
  await page.getByRole("button", { name: "加入草稿" }).click();
  await expect(page.getByText("待录入 2 张")).toBeVisible();
  await page.getByRole("button", { name: "第 2 张", exact: true }).click();
  await expect(page.getByRole("button", { name: "提交识别" })).toBeDisabled();
  await page.getByRole("button", { name: "任务", exact: false }).click();
  await page.getByRole("button", { name: "录卡", exact: false }).click();
  await expect(page.getByText("待录入 2 张")).toBeVisible();
  await page.getByRole("button", { name: "第 2 张", exact: true }).click();
  await page.getByRole("button", { name: "移除此草稿" }).click();
  await expect(page.getByText("待录入 1 张")).toBeVisible();
  await page.getByRole("button", { name: "交换正反面" }).click();
  await page.getByRole("button", { name: "裁切正面" }).click();
  await expect(
    page.getByRole("heading", { name: "核对卡片四角" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "预览裁切" }).click();
  await expect(
    page.getByRole("img", { name: "透视裁切预览", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../work/mobile/crop-phone.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认裁切" }).click();
  await expect(page.getByRole("button", { name: "提交识别" })).toBeEnabled();
  await expect(page.getByLabel("选择正面照片")).toBeEnabled();
  await page
    .getByLabel("选择正面照片")
    .setInputFiles(process.env.MOBILE_TEST_IMAGE!);
  await page.getByRole("button", { name: "保留整张", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "卡片正面", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("选择背面照片")).toBeEnabled();
  await page
    .getByLabel("选择背面照片")
    .setInputFiles(process.env.MOBILE_TEST_IMAGE!);
  await page.getByRole("button", { name: "保留整张", exact: true }).click();
  await expect(page.getByRole("button", { name: "提交识别" })).toBeEnabled();
  await page.getByRole("button", { name: "提交识别" }).click();
  await expect(
    page.getByRole("button", { name: /新的录卡任务/ }),
  ).toBeVisible();
  // Only the external model boundary is a fixture, scoped to this disposable account.
  execFileSync(path.resolve("../../.venv/bin/python"), [
    "-c",
    `import os,psycopg
from psycopg.types.json import Jsonb
with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
 db.execute("UPDATE scanner_recognition_job SET status='succeeded',result=%s WHERE owner_id=(SELECT id FROM app_user WHERE email=%s)",(Jsonb({'confidence':'0.6','player_name':'Synthetic Player','brand_name':'Test','product_name':'Mobile Sample','season_start':2026,'caveats':['测试模型边界样本，非真实识别结果']}),os.environ['MOBILE_TEST_EMAIL']))`,
  ]);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: /Synthetic Player/ }).click();
  await expect(
    page.getByText("测试模型边界样本，非真实识别结果"),
  ).toBeVisible();
  await page.screenshot({ path: "../../work/mobile/confirm.png" });
  await page.getByRole("button", { name: "确认入库" }).click();
  await expect(
    page.getByRole("heading", { name: "Synthetic Player", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "卡片正面", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "卡片背面", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看卡片正面", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "卡片正面大图" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "放大", exact: true }).click();
  await expect(page.getByText("125%", { exact: true })).toBeVisible();
  await page.screenshot({ path: "../../work/mobile/viewer-phone.png" });
  await page.getByRole("button", { name: "关闭大图" }).click();
  await page.screenshot({ path: "../../work/mobile/detail.png" });
  await page.getByRole("button", { name: "我的", exact: false }).click();
  await page.getByRole("button", { name: "知识查询", exact: true }).click();
  await page.getByLabel("知识关键词").fill("SyntheticNoMatch");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.getByText("未找到相关知识，试试其他名称。")).toBeVisible();
  expect(
    await page.getByRole("button", { name: "发布", exact: true }).count(),
  ).toBe(0);
  await page.getByRole("button", { name: "返回我的", exact: false }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登录你的卡库" }),
  ).toBeVisible();
  expect(await page.locator("img.card-image").count()).toBe(0);
});

test("single back recognition, missing front supplement keeps the same card, sorted library uses thumbnails", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("用户名（邮箱）", { exact: true })
    .fill(process.env.MOBILE_TEST_EMAIL!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.MOBILE_TEST_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "录卡", exact: false }).click();
  await expect(page.getByLabel("选择背面照片")).toBeEnabled();
  await page
    .getByLabel("选择背面照片")
    .setInputFiles(process.env.MOBILE_TEST_IMAGE!);
  await page.getByRole("button", { name: "保留整张", exact: true }).click();
  await page.getByLabel("先识别这一面").check();
  await page.getByRole("button", { name: "提交识别", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /新的录卡任务/ }),
  ).toBeVisible();
  function finish() {
    execFileSync(path.resolve("../../.venv/bin/python"), [
      "-c",
      `import os,psycopg
from psycopg.types.json import Jsonb
with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
 db.execute("UPDATE scanner_recognition_job SET status='succeeded',result=%s WHERE owner_id=(SELECT id FROM app_user WHERE email=%s) AND collection_item_id IS NULL",(Jsonb({'confidence':'0.5','player_name':'Single Side Fixture','brand_name':'Test','product_name':'Single','season_start':2026}),os.environ['MOBILE_TEST_EMAIL']))`,
    ]);
  }
  finish();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: /Single Side Fixture/ }).click();
  await expect(
    page.getByText("本次只上传了背面，未提供的字段需要确认。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认入库", exact: true }).click();
  await page.getByRole("button", { name: "补拍正面", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "补充卡片照片" }),
  ).toBeVisible();
  await expect(page.getByLabel("选择正面照片")).toBeEnabled();
  await page
    .getByLabel("选择正面照片")
    .setInputFiles(process.env.MOBILE_TEST_IMAGE!);
  await page.getByRole("button", { name: "保留整张", exact: true }).click();
  await page.getByRole("button", { name: "提交识别", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /新的录卡任务/ }),
  ).toBeVisible();
  finish();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page
    .getByRole("button", { name: /Single Side Fixture.*待确认/ })
    .click();
  await page.getByRole("button", { name: "确认补充到原卡片" }).click();
  await expect(
    page.getByRole("heading", { name: "Single Side Fixture" }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "卡片正面", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "卡片背面", exact: true }),
  ).toBeVisible();
  expect(
    await page.getByRole("button", { name: "补拍正面", exact: true }).count(),
  ).toBe(0);
  await page.getByRole("button", { name: "返回卡库", exact: false }).click();
  await page.getByLabel("全库排序").selectOption("player_name");
  await expect(page.getByText("共 2 张卡片")).toBeVisible();
  await page.screenshot({
    path: "../../work/mobile/sorted-library.png",
    fullPage: true,
  });
});
