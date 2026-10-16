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
