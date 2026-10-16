import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { LibraryPage } from "../src/LibraryPage";
import type { MobileApi } from "../src/api";
beforeEach(() => { window.scrollTo = vi.fn(); });
afterEach(cleanup);
it("sends holding filters to the server with the search term, not a local single-page filter", async () => {
  const calls: string[] = [];
  const api = {
    get: async (path: string) => {
      calls.push(path);
      return { items: [], total: 0, page: 1, total_pages: 1 };
    },
  } as unknown as MobileApi;
  render(<LibraryPage api={api} onOpen={() => {}} />);
  fireEvent.change(screen.getByLabelText("持仓状态"), {
    target: { value: "sold" },
  });
  fireEvent.change(screen.getByLabelText("搜索卡片"), {
    target: { value: "Jordan" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() => {
    const params = new URL(calls.at(-1)!, "https://test.example").searchParams;
    expect(JSON.parse(params.get("search")!)).toMatchObject({
      q: "Jordan",
      holding_status: "sold",
    });
    expect(params.get("page")).toBe("1");
  });
});
it("sorts the entire server library, reuses page snapshot and restarts snapshot on sorting or expiry", async () => {
  const { LEGACY_CAPABILITIES } =
    await import("../../../packages/card-core/src/capture");
  const { ApiError } = await import("../src/api");
  const get = vi.fn(async (path: string) => {
    const q = new URL(path, "https://test.example").searchParams;
    return {
      items: [],
      total: 60,
      page: Number(q.get("page")),
      total_pages: 2,
      snapshot_token: "snapshot-one",
    };
  });
  render(
    <LibraryPage
      api={{ get } as unknown as MobileApi}
      onOpen={() => {}}
      capabilities={{ ...LEGACY_CAPABILITIES, portfolio_sort: true }}
    />,
  );
  await screen.findByText("共 60 张卡片");
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
  await waitFor(() =>
    expect(get.mock.calls.at(-1)?.[0]).toContain("snapshot_token=snapshot-one"),
  );
  await screen.findByText("2 / 2");
  fireEvent.change(screen.getByLabelText("全库排序"), {
    target: { value: "fair_value" },
  });
  await waitFor(() =>
    expect(get.mock.calls.at(-1)?.[0]).toContain("sort_by=fair_value"),
  );
  expect(get.mock.calls.at(-1)?.[0]).not.toContain("snapshot_token");
  await screen.findByText("1 / 2");
  get.mockRejectedValueOnce(new ApiError(409, "expired"));
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
  await waitFor(() => expect(get.mock.calls.at(-1)?.[0]).toContain("page=1&"));
  expect(get.mock.calls.at(-1)?.[0]).not.toContain("snapshot_token");
});

it("keeps loaded cards on next-page failure and retries without duplicates", async () => {
  let fail = true;
  const get = vi.fn(async (path: string) => {
    const page = Number(new URL(path, "https://test.example").searchParams.get("page"));
    if (page === 2 && fail) throw new Error("offline");
    return { items: (page === 1 ? [1] : [1, 2]).map(id => ({id: String(id), player_name: `Player ${id}`})), total: 2, page, total_pages: 2 };
  });
  render(<LibraryPage api={{get} as unknown as MobileApi} onOpen={() => {}} />);
  await screen.findByText("Player 1");
  fireEvent.click(screen.getByRole("button", {name: "加载更多"}));
  await screen.findByText("offline");
  expect(screen.getByText("Player 1")).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole("button", {name: "重试"}));
  await screen.findByText("Player 2");
  expect(screen.getAllByText("Player 1")).toHaveLength(1);
});

it("restores query and scroll without refetch before expiry, then revalidates in background", async () => {
  const now = vi.spyOn(Date, "now").mockReturnValue(1000);
  let finish: ((value: unknown) => void) | undefined;
  const get = vi.fn().mockResolvedValue({items: [{id: "one", player_name: "Cached"}], total: 1, page: 1, total_pages: 1});
  const api = {get} as unknown as MobileApi;
  const view = render(<LibraryPage api={api} onOpen={() => {}} />);
  await screen.findByText("Cached");
  fireEvent.change(screen.getByLabelText("搜索卡片"), {target: {value: "draft"}});
  Object.defineProperty(window, "scrollY", {value: 700, configurable: true});
  fireEvent.scroll(window);
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active={false} />);
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active />);
  expect(get).toHaveBeenCalledTimes(1);
  expect(window.scrollTo).toHaveBeenLastCalledWith(0, 700);
  expect((screen.getByLabelText("搜索卡片") as HTMLInputElement).value).toBe("draft");
  now.mockReturnValue(62000);
  get.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active={false} />);
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Cached")).toBeTruthy();
  finish!({items: [{id: "two", player_name: "Fresh"}], total: 1, page: 1, total_pages: 1});
  await screen.findByText("Fresh");
  now.mockRestore();
});

it("ignores a slow prior search response", async () => {
  let resolveOld: ((value: unknown) => void) | undefined;
  const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve;}))
    .mockResolvedValue({items: [{id: "new", player_name: "New result"}], total: 1, page: 1, total_pages: 1});
  render(<LibraryPage api={{get} as unknown as MobileApi} onOpen={() => {}} />);
  fireEvent.change(screen.getByLabelText("搜索卡片"), {target: {value: "new"}});
  fireEvent.click(screen.getByRole("button", {name: "搜索"}));
  await screen.findByText("New result");
  resolveOld!({items: [{id: "old", player_name: "Old result"}], total: 1, page: 1, total_pages: 1});
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(screen.queryByText("Old result")).toBeNull();
});

it("resubmitting the same search refreshes instead of leaving an empty loading screen", async () => {
  const get = vi.fn().mockResolvedValue({items: [], total: 0, page: 1, total_pages: 1});
  render(<LibraryPage api={{get} as unknown as MobileApi} onOpen={() => {}} />);
  await screen.findByText("共 0 张卡片");
  fireEvent.click(screen.getByRole("button", {name: "搜索"}));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  await screen.findByText("共 0 张卡片");
});

it("refreshes the loaded window without discarding later pages", async () => {
  const get = vi.fn(async (path: string) => {
    const page = Number(new URL(path, "https://test.example").searchParams.get("page"));
    return {items: [{id: String(page), player_name: `Window ${page}`}], total: 2, page, total_pages: 2};
  });
  render(<LibraryPage api={{get} as unknown as MobileApi} onOpen={() => {}} />);
  await screen.findByText("Window 1");
  fireEvent.click(screen.getByRole("button", {name: "加载更多"}));
  await screen.findByText("Window 2");
  fireEvent.click(screen.getByRole("button", {name: "刷新"}));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(4));
  expect(screen.getByText("Window 1")).toBeTruthy();
  expect(screen.getByText("Window 2")).toBeTruthy();
});

it("invalidates cached cards after a write while hidden and ignores the prior request", async () => {
  let listener: (() => void) | undefined;
  let releaseOld: ((value: unknown) => void) | undefined;
  const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => {releaseOld = resolve;}))
    .mockResolvedValue({items: [{id: "new", player_name: "After write"}], total: 1, page: 1, total_pages: 1});
  const api = {get, subscribeLibrary: (fn: () => void) => {listener = fn; return () => {listener = undefined;};}} as unknown as MobileApi;
  const view = render(<LibraryPage api={api} onOpen={() => {}} />);
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active={false} />);
  const {act} = await import("@testing-library/react");
  act(() => listener!());
  await screen.findByText("After write");
  await act(async () => releaseOld!({items: [{id: "old", player_name: "Before write"}], total: 1, page: 1, total_pages: 1}));
  expect(screen.queryByText("Before write")).toBeNull();
  view.rerender(<LibraryPage api={api} onOpen={() => {}} active />);
  expect(screen.getByText("After write")).toBeTruthy();
});

it("coalesces repeated near-bottom observations while a page is pending", async () => {
  let intersect: ((entries: {isIntersecting: boolean}[]) => void) | undefined;
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: typeof intersect) {intersect = callback;}
    observe() {} disconnect() {}
  });
  let release: ((value: unknown) => void) | undefined;
  const get = vi.fn().mockResolvedValueOnce({items: [], total: 2, page: 1, total_pages: 2})
    .mockImplementationOnce(() => new Promise(resolve => {release = resolve;}));
  const {act} = await import("@testing-library/react");
  const view = render(<LibraryPage api={{get} as unknown as MobileApi} onOpen={() => {}} />);
  await screen.findByText("共 2 张卡片");
  act(() => {intersect!([{isIntersecting: true}]); intersect!([{isIntersecting: true}]);});
  expect(get).toHaveBeenCalledTimes(2);
  await act(async () => release!({items: [], total: 2, page: 2, total_pages: 2}));
  view.unmount();
  vi.unstubAllGlobals();
});
