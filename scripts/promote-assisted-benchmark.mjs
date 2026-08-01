#!/usr/bin/env node

const apiBase = process.env.CARD_PLATFORM_API_BASE ?? "http://127.0.0.1:8010";
const actor = process.env.CARD_PLATFORM_KNOWLEDGE_ACTOR;
const args = process.argv.slice(2);
const batchId = args.find((value) => !value.startsWith("--"));
const apply = args.includes("--apply");
const thresholdArg = args.find((value) => value.startsWith("--threshold="));
const threshold = Number(thresholdArg?.split("=")[1] ?? "0.95");

if (!batchId) {
  throw new Error("usage: promote-assisted-benchmark.mjs <batch-id> [--threshold=0.95] [--apply]");
}
if (apply && !actor) {
  throw new Error("CARD_PLATFORM_KNOWLEDGE_ACTOR is required with --apply");
}

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

function canonicalName(sample) {
  const identity = sample.assisted_identity;
  const parts = [
    identity.player_name,
    identity.season_start && identity.season_end
      ? identity.season_start === identity.season_end
        ? String(identity.season_start)
        : `${identity.season_start}-${String(identity.season_end).slice(-2)}`
      : identity.season_start ?? null,
    identity.product_name,
    identity.insert_name,
    identity.parallel_name,
    identity.card_number ? `#${identity.card_number}` : null,
    `CardHobby ${sample.source_item_id}`,
  ];
  return parts.filter(Boolean).join(" · ").slice(0, 200);
}

function seasonName(identity) {
  if (!identity.season_start) return null;
  if (!identity.season_end || identity.season_start === identity.season_end) {
    return String(identity.season_start);
  }
  return `${identity.season_start}-${String(identity.season_end).slice(-2)}`;
}

function parallelEntity(sample) {
  const identity = sample.assisted_identity;
  if (!identity.parallel_name || !identity.product_name) return null;
  const season = seasonName(identity);
  const parts = [
    season,
    identity.product_name,
    identity.insert_name,
    identity.parallel_name,
    identity.serial_total ? `/${identity.serial_total}` : null,
  ].filter(Boolean);
  return {
    entity_type: "parallel",
    canonical_name: parts.join(" · ").slice(0, 200),
    attributes: {
      product: identity.product_name,
      insert: identity.insert_name,
      season_start: identity.season_start,
      season_end: identity.season_end,
      parallel_name: identity.parallel_name,
      serial_total: identity.serial_total,
      observed_from_card: true,
      benchmark_sample_id: sample.id,
      identity_status: "codex_source_assisted",
    },
    source: {
      kind: "codex_source_assisted_parallel",
      source_key: `benchmark-parallel-cardhobby-${sample.source_item_id}`,
      source_url: sample.source_url,
      benchmark_batch_id: batchId,
      labeler: sample.assisted_labeler,
    },
    confidence: Number(sample.assisted_confidence),
  };
}

const samples = await request(`/v1/recognition-benchmarks/${batchId}/samples`);
const candidates = samples.filter((sample) =>
  ["top2_correct", "top3_correct", "none_correct"].includes(sample.assisted_label_result)
  && Number(sample.assisted_confidence) >= threshold
  && sample.assisted_identity
);

const plan = candidates.map((sample) => ({
  sample_id: sample.id,
  source_item_id: sample.source_item_id,
  label: sample.assisted_label_result,
  confidence: Number(sample.assisted_confidence),
  canonical_name: canonicalName(sample),
  parallel_entity: parallelEntity(sample)?.canonical_name ?? null,
}));

if (!apply) {
  console.log(JSON.stringify({apply: false, threshold, count: plan.length, plan}, null, 2));
  process.exit(0);
}

const headers = {
  "content-type": "application/json",
  "x-knowledge-actor": actor,
};
let created = 0;
let existing = 0;
let parallelCreated = 0;
let parallelExisting = 0;
async function createAndApprove(body, createKey, approveKey) {
  const response = await fetch(`${apiBase}/v1/knowledge/entities`, {
    method: "POST",
    headers: {...headers, "idempotency-key": createKey},
    body: JSON.stringify(body),
  });
  if (response.status === 409) return null;
  if (!response.ok) {
    throw new Error(`create ${createKey}: ${response.status} ${await response.text()}`);
  }
  const entity = await response.json();
  await request(`/v1/knowledge/entity/${entity.id}/review`, {
    method: "POST",
    headers: {...headers, "idempotency-key": approveKey},
    body: JSON.stringify({decision: "approved"}),
  });
  return entity;
}
for (const sample of candidates) {
  const identity = {
    ...sample.assisted_identity,
    benchmark_sample_id: sample.id,
    source_item_id: sample.source_item_id,
    identity_status: "codex_source_assisted",
    assisted_label_result: sample.assisted_label_result,
    assisted_evidence: sample.assisted_evidence,
  };
  const body = {
    entity_type: "card",
    canonical_name: canonicalName(sample),
    attributes: identity,
    source: {
      kind: "codex_source_assisted_benchmark",
      source_key: `benchmark-cardhobby-${sample.source_item_id}`,
      source_url: sample.source_url,
      benchmark_batch_id: batchId,
      labeler: sample.assisted_labeler,
    },
    confidence: Number(sample.assisted_confidence),
  };
  const entity = await createAndApprove(
    body,
    `assisted-benchmark-${sample.id}`,
    `approve-assisted-benchmark-${sample.id}`,
  );
  if (entity === null) {
    existing += 1;
  } else {
    created += 1;
  }
  const parallel = parallelEntity(sample);
  if (parallel) {
    const parallelResult = await createAndApprove(
      parallel,
      `assisted-parallel-${sample.id}`,
      `approve-assisted-parallel-${sample.id}`,
    );
    if (parallelResult === null) parallelExisting += 1;
    else parallelCreated += 1;
  }
}

console.log(JSON.stringify({
  apply: true,
  threshold,
  eligible: candidates.length,
  created,
  existing,
  parallel_created: parallelCreated,
  parallel_existing: parallelExisting,
}, null, 2));
