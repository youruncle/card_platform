# Independent narrow review — F1/F2

Reviewed the uncommitted follow-up against `b5a1a4c5`, including the new `knowledge_atlas_lifecycle.py`, on 2026-10-30. Scope: effective identity metadata and proposal publication history only. No product edits, deployment, push or merge performed by reviewer.

## Finding

**P2 / Important — historical transaction timestamps can present publication before submission/approval.**

Location: `apps/api/src/card_platform/knowledge_atlas_lifecycle.py`, `_time` and the event sort (reviewed lines 13–22, 46).

Submission/review events use their payload wall-clock times, but publication/rollback use audit `created_at`. The baseline repository inserts audit rows with the schema default `now()` (transaction start). Thus older rows do not necessarily record action time. The new `clock_timestamp()` insertion fixes future writes only. An unqualified chronological timeline can misrepresent existing history; current snapshot membership remains correct.

Reproduction used the real repository, fixture-created temporary schema, and the authorized isolated PostgreSQL socket. A connection wrapper replaced `clock_timestamp()` with `now()` only for audit INSERTs to reproduce baseline behavior. Execute submit → approve → publish in one transaction, then `CorrectionRepository.list`. Observed:

```text
state: current
published  2026-10-30T10:57:48.840499+08:00
submitted  2026-10-30T10:57:48.843517+08:00
approved   2026-10-30T10:57:48.846250+08:00
```

The reproduction program is `/tmp/atlas_followup_review_probe.py`. It uses `database.__wrapped__`, cleans up its temporary schema, and writes no original evidence. This also illustrates the general legacy transaction-start/lock-wait ambiguity; exact historical causal times cannot be reconstructed from those timestamps alone.

Recommended disposition: preserve original timestamps, explicitly expose their basis and historical ordering uncertainty, and add a regression using baseline audit-clock behavior. The author accepted this finding and reported that this remediation is in progress. This review does **not** claim the later author fix was independently re-reviewed.

## Other assessment

- No additional concrete F1/F2 correctness defect found. Effective serial null remains unknown; autograph true/false/null remains a catalogue attribute, not physical authentication. Original source assertions remain separately labelled. Effective fields feed identity filters, and image bindings are detached by the existing correction path.
- A separate two-overlay probe passed: serial null → 10 and autograph true → false recomputed correctly; original identity/source serial assertions remained original; the latest section filter matched; input entities/artifact references were unchanged.
- Pricing groups use the separate `card_key` / KnowledgeResolver paths. They are **not recomputed or synchronized** by this browser overlay. The added API marker and UI wording state that boundary explicitly.
- Application versions and current state derive from snapshot membership, including restoration to a version whose stored status remains `rolled_back`. The API/UI explicitly distinguish inclusion from effective fields surviving a source conflict.
- Existing rollback records contain no reason; the lifecycle returns null instead of inventing one. No original evidence migration or production schema change was introduced.
- **Unbenchmarked scaling risk:** each proposal page now reads every workspace publication/rollback audit event and extracts correction membership from every referenced historical snapshot, then loops through those events per proposal. Page size does not bound historical work. Two batched queries avoid N+1 queries, but production-volume latency/TOAST decompression was not measured; consider immutable membership caching or SQL projection if historical volume is substantial.

## Verification and limits

- Independently reran `test_knowledge_atlas_corrections.py`: **23 passed** against `postgresql://atlas_test@/postgres?host=/tmp/card-atlas-pg-socket&port=55569`, using the specified Python and `PYTHONPATH=apps/api/src`.
- Initial sandbox socket access failed with `Operation not permitted`; the approved escalated rerun succeeded. No denied or uncertain mutation remains.
- Combined web command: **25/26 passed**. All nine manager tests passed, including both follow-up tests. Existing browser test `comparison keeps two distinct images when the primary selection changes` timed out waiting for its dialog at line 65; its isolated targeted rerun then **passed 1/1**. This was not attributed to F1/F2 because neither comparison implementation nor its test changed.
- No independent production-volume performance benchmark, full suite, build/typecheck, real-browser QA or production adapter validation was performed in this narrow review. The author is running broader verification separately.
- Integration remains blocked on alignment with the actual final readonly adapter deployment/files/index/cache version, which is still unconfirmed. No deployment/push/merge was attempted.
