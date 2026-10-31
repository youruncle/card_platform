export async function chatgptRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/v1/chatgpt${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.message ?? body.detail ?? "请求失败，请重新登录并核对授权");
  }
  return body as T;
}

export const scopeLabels: Record<string, string> = {
  "cards:read": "查看我的卡库、图片和账本",
  "cards:write": "准备录卡及收藏修改（需要另行确认）",
  "transactions:write": "准备买卖及费用记录（需要另行确认）",
  "research:run": "发起真实成交研究",
};
