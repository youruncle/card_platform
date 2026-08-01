#!/usr/bin/env node

const batchId = process.argv[2];
const apiBase = process.env.CARD_PLATFORM_API_BASE ?? "http://127.0.0.1:8000";
const pollMs = Number(process.env.CODEX_CAMPAIGN_POLL_MS ?? "15000");
if (!batchId) {
  throw new Error("usage: run-codex-campaign-batch.mjs <batch-id>");
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function waitForPredictionJob(job) {
  while (true) {
    const [current, labelProgress] = await Promise.all([
      request(`/v1/recognition-benchmarks/${batchId}/prediction-jobs/${job.id}`),
      request(
        `/v1/recognition-benchmarks/${batchId}/codex-auto-label?ready_only=true`,
        {method: "POST"},
      ),
    ]);
    process.stdout.write(
      `${new Date().toISOString()} job=${job.id} status=${current.status} `
      + `predicted=${current.predicted_count}/${current.requested_count} `
      + `failed=${current.failed_count} `
      + `newly_labeled=${labelProgress.newly_labeled_count}\n`,
    );
    if (["succeeded", "partial", "failed"].includes(current.status)) return current;
    await sleep(pollMs);
  }
}

async function predictNext(options = {}) {
  const job = await request(`/v1/recognition-benchmarks/${batchId}/predict`, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      limit: 50,
      retry_errors: options.retryErrors ?? false,
      replay_all: false,
    }),
  });
  if (job.status === "succeeded" && job.requested_count === 0) return job;
  return waitForPredictionJob(job);
}

while (true) {
  const samples = await request(`/v1/recognition-benchmarks/${batchId}/samples`);
  const pending = samples.filter(
    (sample) => sample.prediction == null && sample.prediction_error == null,
  ).length;
  if (pending === 0) break;
  const outcome = await predictNext();
  if (outcome.status === "failed") {
    throw new Error(`prediction job failed: ${outcome.error_message ?? outcome.id}`);
  }
}

let samples = await request(`/v1/recognition-benchmarks/${batchId}/samples`);
if (samples.some((sample) => sample.prediction_error)) {
  const retry = await predictNext({retryErrors: true});
  if (retry.status === "failed") {
    throw new Error(`prediction retry failed: ${retry.error_message ?? retry.id}`);
  }
}

samples = await request(`/v1/recognition-benchmarks/${batchId}/samples`);
if (samples.some((sample) => sample.prediction == null)) {
  throw new Error("batch still contains samples without a prediction after retry");
}

const labelReport = await request(
  `/v1/recognition-benchmarks/${batchId}/codex-auto-label`,
  {method: "POST"},
);
const evidenceReport = await request(
  `/v1/recognition-benchmarks/${batchId}/promote-assisted-market-evidence?min_confidence=0.90`,
  {method: "POST"},
);

console.log(JSON.stringify({
  batch_id: batchId,
  label_report: labelReport,
  evidence_report: evidenceReport,
  status: "complete",
}, null, 2));
