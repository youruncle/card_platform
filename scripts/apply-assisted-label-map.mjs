#!/usr/bin/env node

import fs from "node:fs/promises";

const [batchId, mapPath] = process.argv.slice(2);
const apiBase = process.env.CARD_PLATFORM_API_BASE ?? "http://127.0.0.1:8010";
if (!batchId || !mapPath) {
  throw new Error("usage: apply-assisted-label-map.mjs <batch-id> <map.json>");
}

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

const judgments = JSON.parse(await fs.readFile(mapPath, "utf8"));
const samples = await request(`/v1/recognition-benchmarks/${batchId}/samples`);
let saved = 0;
for (const sample of samples) {
  const judgment = judgments[sample.source_item_id];
  if (!judgment) {
    throw new Error(`missing judgment for ${sample.source_item_id}`);
  }
  const top1 = sample.prediction?.candidates?.[0]?.identity ?? {};
  const identity = judgment.result === "none_correct"
    ? {...top1, ...(judgment.identity_patch ?? {})}
    : null;
  await request(
    `/v1/recognition-benchmarks/${batchId}/samples/${sample.id}/assisted-label`,
    {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({
        result: judgment.result,
        identity,
        confidence: judgment.confidence ?? 0.9,
        evidence: judgment.evidence ?? [],
        labeler: judgments._labeler ?? "codex-source-assisted-v3",
      }),
    },
  );
  saved += 1;
}

console.log(JSON.stringify({batch_id: batchId, saved}, null, 2));
