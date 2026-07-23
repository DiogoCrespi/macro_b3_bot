# Company exposure audit artifacts

`exposure_4c_real_20260722.json` is the pre-4C.4 historical output and is
superseded for review/readiness decisions.

Use:

- `exposure_4c4_integrity_20260722.json` for the corrected document taxonomy,
  fact scope, derivation and review-integrity audit;
- `exposure_4c4_review_pending.json` as the hash-bound human review manifest;
- `exposure_4c4_dry_run.json` for the restricted KLBN11/SLCE3 integration
  result.

Facts remain unusable by the company exposure builder until an identified
human reviewer applies explicit approvals with `apply-company-exposure-review`.

The subsequent official-FRE coverage packet is:

- `exposure_4c4_fre_ingestion.json`: latest point-in-time FRE ingested for
  15/15 pilot issuers, including document version and availability;
- `exposure_4c5_coverage.json`: 50 scoped facts, at least three fields for
  15/15 issuers, zero future documents and zero structural evidence failures;
- `exposure_4c5_review_pending.json`: the corresponding 50-decision,
  excerpt-hash-bound human review manifest.

The 15/15 threshold describes extraction coverage, not approval. All 50 facts
remain `HUMAN_REVIEW_PENDING`; no agent-generated approval is accepted.
