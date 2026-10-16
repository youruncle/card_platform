# Additive recognition and cost-search contract

Existing result fields remain compatible. New optional-by-default fields in CardRecognitionResult:
- field_assessments: core field → {value, status: unknown|proposed|verified|conflicted, source: none|visual_model|local_ocr|catalog, evidence: string[], issues: string[]}.
- catalog_verified_fields: scalar field values actually applied after server HTTP source validation. Model values are cleared before enrichment. Together with the corresponding source candidate and applied evidence, these may establish verified state.
- local_ocr_observed_fields: scalar observations added by the server OCR anchor pass. These do not establish catalog verification or prove release-year semantics.
- identity_storage_binding: scanner-created version-1 snapshot of the actual stored identity and image byte digests. It does not grant authority to the caller. The purchase reader consumes it only when current values and bytes match and no unrevoked confirmed identity exists.

The fields are persisted in existing scanner JSON; no migration, new client input or new endpoint is required. Provider self-reported assessments and storage/catalog/local provenance are discarded at raw vision entry points; screenshots are finalized too.

Cost search preserves status names: incomplete when search is incomplete, not_found for a complete empty result, unmatched when orders returned but no same-card decision can be made. Candidate recall is discovery only; automatic confirmation continues to require the existing complete-order, same-card, ownership and unique-unit gates.
