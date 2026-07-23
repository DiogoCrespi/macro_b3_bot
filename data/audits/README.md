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

Sprint 4D.1 financial bridge artifact:

- `financial_4d1_pilot.json`: five PIT TTM baselines, nine unit-bearing
  economic shocks and fifteen pessimistic/base/optimistic outcomes.

The artifact preserves formulas, monetary bases, evidence IDs, assumptions,
confidence and blocked channels. It uses zero future documents and never
uses a normalized `[-1, 1]` score as a financial percentage.

Base-case read-through:

- MGLU3: `+100 bps` on average post-hedge floating debt produces an
  approximately `-R$50.0m` financial-result and FCF delta. Demand remains
  blocked for missing elasticity.
- SUZB3: FX revenue translation is calculated separately from net FX debt.
  Net FX debt and cost remain blocked because approved post-hedge exposure or
  elasticity is unavailable.
- KLBN11: FX revenue is calculated. FX debt is blocked because the disclosed
  currency mix is not a net post-hedge balance. Floating and IPCA debt
  monetary bases are available but inactive because the replay has no active
  causal interest-rate or inflation channel.
- RAIL3 and SLCE3: all cases remain `NO_ACTION` with zero deltas because the
  sector state is `SECTOR_STATE_NO_ACTIVE_SIGNAL`.

TTM FCF is the explicit standardized formula `CFO + capex`; it is not
normalized recurring FCF. Both decision policies remain comparative, with no
final policy selection. Valuation, MiroFish, BUY and order execution remain
disabled.

Sprint 4D.2 directional and cash-flow semantics artifact:

- `financial_4d2_pilot.json`: the same five-company PIT pilot with signed
  causal shocks, `LOW_SHOCK`/`BASE_SHOCK`/`HIGH_SHOCK` inputs and company
  outcomes ordered afterward as pessimistic/base/optimistic.
- FX debt revaluation is identified as an accounting effect and contributes
  zero to CFO, the levered FCF proxy and net debt absent supported cash
  realization.
- FX revenue uses explicit, non-company-calibrated incremental EBITDA margin
  assumptions. The baseline identifies FCF as
  `CFO_PLUS_REPORTED_CAPEX / NOT_NORMALIZED`, average debt as a
  `TWO_POINT_AVERAGE_PROXY`, and net debt as `STANDARDIZED_CASH_ONLY`.

Sprint 4D.2A factor-direction hotfix artifact:

- `financial_4d2a_pilot.json`: preserves macro `factor_direction` separately
  from `channel_effect_direction`; financial shock direction no longer depends
  on aggregating company effects.
- Conflicting macro directions for one factor block the scenario as
  `SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION`; a zero company-effect sum
  never implies a positive macro direction.

Sprint 4D.3 calibration artifact:

- `financial_4d3_pilot.json`: diagnoses every conflicting FX path for SUZB3
  and KLBN11, preserving event, path, edges, direction, availability, lag,
  horizon, strength, confidence and evidence status.
- `CALIBRATION_MODE` runs controlled positive and negative FX/CDI/IPCA
  shocks without producing a decision. `DECISION_MODE` keeps unresolved
  real-signal conflicts blocked.
- The retrospective calibration pack uses 10–11 quarterly observations per
  bridge (2023Q1–2026Q1), official CVM statements, BCB USD/BRL and Selic, and
  FRED/BLS wood-pulp PPI (`WPU0911`). SUZB3 and KLBN11 remain partial because
  disclosed historical volume is unavailable; their confidence is capped.
- Reported CFO/capex and levered FCF proxy remain untouched. A separate,
  lower-confidence normalized FCF uses the annualized median of eight
  standalone reported CFO quarters and reported capex as a conservative
  maintenance-capex proxy, with every source document retained.
