#!/usr/bin/env node

const batchId = process.argv[2];
const apiBase = process.env.CARD_PLATFORM_API_BASE ?? "http://127.0.0.1:8000";
if (!batchId) throw new Error("usage: run-codex-auto-label-batch.mjs <batch-id>");

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

const response = await request(`/v1/recognition-benchmarks/${batchId}/codex-auto-label`, {
  method: "POST",
});
console.log(JSON.stringify(response, null, 2));
