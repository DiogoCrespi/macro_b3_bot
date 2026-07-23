# Sprint 4C.3 — Pilot accounting audit

Cutoff: `2026-07-22T23:59:59+00:00`  
Exposure run: `exposure_4c3_20260722_pit`

## Result

| Check | Result |
|---|---:|
| Pilot mappings | 15/15 |
| Exposure snapshots | 15/15 |
| Revenue reconciliations | 15/15 |
| Non-bank gross financial debt reconciliations | 13/13 |
| Material differences | 0 |
| Values without field evidence | 0 |
| Future evidence | 0 |
| Undeclared availability precision | 0 |

All 28 accounting reconciliations matched the normalized official statement line
exactly. The audit preserves the raw value and its CVM scale separately from the
normalized BRL value.

## Availability qualification

The 15 selected filings have
`availability_precision=CONSERVATIVE_RESOURCE_DATE`. Their availability is
bounded by the CVM ZIP resource's HTTP `Last-Modified`, not an exact
filing-level publication timestamp. This is safe for the current cutoff but is
not equivalent to exact historical filing availability.

## Debt definition

`gross_financial_debt` methodology `4C.3-v1` is:

```text
2.01.04 Empréstimos e Financiamentos (current)
+ 2.02.01 Empréstimos e Financiamentos (non-current)
```

It is gross, does not net cash, and excludes accounts outside those standardized
codes. It is intentionally `UNKNOWN` for banks because their standardized
taxonomy represents deposits/interbank funding rather than comparable corporate
debt.

## Confidence decomposition

```text
average evidence_quality_score = 0.9800
average completeness_score     = 0.1519
average overall_confidence     = 0.3813
```

The evidence found is high quality, while macro-exposure coverage remains low.
The snapshot must not be interpreted as a complete macro exposure matrix.

## Remaining product blockers

- At least three evidenced FX, interest-rate, or commodity exposures per company.
- Exact filing-level availability where the official timestamp is obtainable.
- Sector states for petroleum, electricity, logistics, and agribusiness.
- Real company-impact execution for all 15 companies.

Valuation, BUY, and order execution remain disabled.
