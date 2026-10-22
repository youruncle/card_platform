import { expect, it, vi } from "vitest";
import { nativeRequestOptions } from "../src/transport";
it("serializes multipart photos for native HTTP and disables credential-bearing redirects", async () => {
  const form = new FormData();
  form.append(
    "front",
    new Blob(["photo"], { type: "image/jpeg" }),
    "front.jpg",
  );
  const options = await nativeRequestOptions(
    "https://cards.example/v1/mobile/jobs",
    { method: "POST", body: form, headers: { Authorization: "Bearer secret" } },
  );
  expect(options.disableRedirects).toBe(true);
  expect(options.dataType).toBe("formData");
  expect(options.data).toEqual([
    {
      key: "front",
      value: "cGhvdG8=",
      type: "base64File",
      contentType: "image/jpeg",
      fileName: "front.jpg",
    },
  ]);
  expect(options.readTimeout).toBe(180000);
  expect(options.connectTimeout).toBe(180000);
});

it('passes explicit long JSON request timeout to native HTTP without leaking it into headers', async()=>{
  const options=await nativeRequestOptions('https://cards.example/v1/purchase-cost/cardhobby/search',{
    method:'POST',body:JSON.stringify({keywords:['fixture']}),headers:{'Content-Type':'application/json'},requestTimeoutMs:180000,
  });
  expect(options.readTimeout).toBe(180000);
  expect(options.connectTimeout).toBe(180000);
  expect(options.headers).not.toHaveProperty('requestTimeoutMs');
  const ordinary=await nativeRequestOptions('https://cards.example/v1/auth/me',{});
  expect(ordinary.readTimeout).toBe(30000);
});
