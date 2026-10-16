import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { PrivateImage } from "../src/PrivateImage";
import type { MobileApi } from "../src/api";
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:private");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
it("retries private image failures and opens a viewer inside the app", async () => {
  const image = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue(new Blob(["image"]));
  render(
    <PrivateImage
      api={{ image } as unknown as MobileApi}
      path="/uploads/private.jpg"
      alt="卡片正面"
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "重新加载卡片正面" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "查看卡片正面" }));
  expect(screen.getByRole("dialog", { name: "卡片正面大图" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "放大" }));
  expect(screen.getByText("125%")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "关闭大图" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("forgets open private images when the account or path changes", async () => {
  const api = {
    image: vi.fn().mockResolvedValue(new Blob(["image"])),
  } as unknown as MobileApi;
  const view = render(
    <PrivateImage api={api} path="/uploads/one.jpg" alt="卡片正面" />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "查看卡片正面" }));
  view.rerender(<PrivateImage api={api} path={null} alt="卡片正面" />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByRole("img")).toBeNull();
});
it("fetches the thumbnail first and downloads original only on opening", async () => {
  const image = vi.fn().mockResolvedValue(new Blob(["image"]));
  render(
    <PrivateImage
      api={{ image } as unknown as MobileApi}
      path="/v1/portfolio/items/i/images/front?variant=thumb"
      originalPath="/uploads/original.jpg"
      alt="正面"
    />,
  );
  const button = await screen.findByRole("button", { name: "查看正面" });
  expect(image.mock.calls.map((c) => c[0])).toEqual([
    "/v1/portfolio/items/i/images/front?variant=thumb",
  ]);
  fireEvent.click(button);
  await screen.findByRole("dialog", { name: "正面大图" });
  expect(image.mock.calls[1][0]).toBe("/uploads/original.jpg");
  fireEvent.click(screen.getByRole("button", { name: "关闭大图" }));
  await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalled());
});
