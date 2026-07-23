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
