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

Sprint 4C.5A hardening artifacts:

- `exposure_4c5a_coverage.json` separates extraction coverage, direct impact
  compatibility, component readiness, and approved coverage;
- `exposure_4c5a_review_pending.json` replaces excerpt-only hashes with
  canonical fact-review hashes covering values, scope, denominator, formula,
  methodology, document checksum, FRE section, and evidence text.

The local CLI now requires an interactive reviewer-identity confirmation and
writes an append-only decision log transactionally. This is a locally
confirmed identity assertion, not cryptographic authentication. The current
packet has 15/15 extraction coverage, only 8/15 issuers with at least one
direct impact input, 0/15 with three calculable impact components, and 0
approved facts.

Sprint 4C.5B freeze artifacts:

- `exposure_4c5b_delegated_review.json`: selective, user-delegated AI evidence
  review for 11 facts required by the five-company pilot. The log records
  `DELEGATED_AI`, never `HUMAN`;
- `exposure_4c5b_approved_coverage.json`: five approved snapshots, approved
  versus pending fields, and impact-compatible components;
- `exposure_4c5b_impact_pilot.json`: side-by-side `THREE_COMPONENTS` and
  `MATERIALITY_COVERAGE` results for SUZB3, KLBN11, MGLU3, RAIL3 and SLCE3.

The pilot freezes Sprint 4C. It persists ten candidates, has calculable
contributions for three companies, preserves two no-active-signal
`NO_ACTION` cases, and emits no BUY or order. The proposed materiality policy
produces one `WATCH` (MGLU3); the legacy policy produces none. These pilot
thresholds are comparative and are not yet a permanent production rule.
