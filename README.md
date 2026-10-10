# experiment-kit
[![CI](https://github.com/yashsabbavarapu/experiment-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/yashsabbavarapu/experiment-kit/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A deliberately small, honest experimentation framework for growth teams. Hypothesis registry, deterministic assignment, idempotent event ingestion, and a readout that refuses to give you an answer it can't stand behind.

The thesis: **the discipline, not the platform.** Most experiment programs don't fail for lack of a platform. They fail because someone reads the dashboard on day 4, sees p = 0.03, and ships. Or because the treatment's logging silently dropped 10% of users and nobody checked the split. Or because the "win" also doubled refunds. GrowthBook, Statsig, and Optimizely all have answers to these problems. This kit is those answers stripped down to the part that matters: every guardrail a growth lead would demand, in about 1,500 lines of stdlib Python you can read in an afternoon.

**The guardrails are the product.** Each one can fail a readout on its own:

| Guardrail | What it catches | Verdict it forces |
|---|---|---|
| Sample-ratio mismatch | Arms imbalanced beyond chance (broken assignment or exposure logging) | INVALID |
| Peeking | Readout requested before the preregistered end date | INVALID |
| Minimum sample | Fewer users than the test was powered for | INVALID |
| Unmeasured guardrail metric | A preregistered guardrail with no data | INVALID |
| Guardrail metric breach | Treatment made a guardrail metric significantly worse | KILL |

Zero runtime dependencies. Python 3.11+, SQLite, `mypy --strict` clean.

## How it works

```
 hypothesis.yaml/.json                users.csv               events.csv
         |                                |                       |
         v                                v                       v
 +----------------+  frozen   +----------------------+   +------------------+
 |    registry    |---------->|      assignment      |   |      events      |
 | validate, set  | seed,     | sha256(seed:user_id) |   | strict columns,  |
 | min sample,    | split     | -> bucket in [0,1)   |   | lenient rows     |
 | freeze         |           | -> control/treatment |   +------------------+
 +----------------+           +----------------------+            |
         |                                |                       |
         v                                v                       v
 +----------------------------------------------------------------------------+
 |  SQLite: hypotheses (immutable) | assignments | events (content-hashed,    |
 |          readouts (every request logged, so peeks are counted)            |
 +----------------------------------------------------------------------------+
                                         |
                                         v
 +----------------------------------------------------------------------------+
 |  readout: per-arm rates -> z-test -> guardrails -> verdict                 |
 |    SRM chi-square | timing (peeking) | min-sample gate | guardrail metrics  |
 |    SHIP | KILL | INCONCLUSIVE | INVALID (with the reason)                  |
 +----------------------------------------------------------------------------+
```

1. **Register** a hypothesis: what changes, why it should work, the expected effect, the primary metric, guardrail metrics, dates, split, seed. If you don't set a minimum sample, the kit computes one from your baseline and expected lift (power analysis, below). The record is then frozen. Adding the same name twice is refused: you register a new name, you don't edit a preregistration.
2. **Assign** users with a hash of `seed:user_id`. No RNG, no clock, no state. Same input, same arm, on every machine.
3. **Ingest** exposure and conversion events from CSV. A missing required column rejects the file. A malformed row is skipped with a line-numbered warning. Every event is keyed by a content hash, so re-ingesting a file is a no-op.
4. **Read out.** Arms are re-derived from the hash (never trusted from the log), rates and lift are computed on events inside `[start_date, end_date]`, every guardrail runs, and the kit issues one plain-language verdict.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest mypy   # pytest and mypy are dev tools only; runtime is stdlib
experiment-kit hypothesis add fixtures/hypotheses/*
experiment-kit ingest --events fixtures/events/*.csv
experiment-kit readout --experiment ab-checkout-copy --as-of 2026-09-14
```

Or run every scenario at once, through the real CLI against a scratch database:

```bash
PYTHONPATH=src python scripts/demo.py
```

Without installing, use `PYTHONPATH=src python -m experiment_kit` wherever you see `experiment-kit`.

## Commands

| Command | What it does |
|---|---|
| `hypothesis add FILE...` | Validate and register hypothesis records (`.json`, `.yaml`, `.yml`) |
| `hypothesis list [--status running\|closed]` | Registry with last verdict and decision |
| `hypothesis show NAME` | Full record plus every readout ever requested |
| `hypothesis close NAME --decision ship\|kill\|abandon [--note ...] [--override]` | Close with a decision. Shipping when the kit's latest verdict isn't SHIP requires `--override` and a `--note`, and the override is recorded |
| `assign --experiment X --users users.csv [--output out.csv]` | Assign users, run the assignment twice and compare sha256 digests, store arms and report any stored arm that disagrees |
| `ingest --events a.csv [b.csv ...]` | Idempotent ingestion with per-row warnings |
| `readout --experiment X [--as-of YYYY-MM-DD]` | The guardrailed analysis. `--as-of` replays what was knowable on that date |

Every command takes `--db PATH` (default `experiment_kit.db`).

### Hypothesis record

```yaml
name: ab-checkout-copy
owner: Priya (growth, checkout)
change: Replace the three-paragraph checkout explainer with one line of benefit copy and a single CTA.
rationale: "Session replays show users scrolling past the explainer and abandoning; less reading before the CTA should lift completion."
primary_metric: checkout_conversion
guardrail_metrics:
  - checkout_error
baseline_rate: 0.10
expected_lift: 0.025   # absolute: 10.0% to 12.5%
start_date: 2026-09-01
end_date: 2026-09-14
split: [50, 50]                # optional, any two positive weights, e.g. [90, 10]
seed: ab-checkout-copy-v1      # optional, defaults to name
# min_sample_per_arm: 3000     # optional, int or [control, treatment]; computed if omitted
# alpha: 0.05  power: 0.8  srm_alpha: 0.001   (optional, these are the defaults)
```

Unknown fields are rejected, and every problem is reported at once. YAML is parsed by a small strict subset parser (`yamlish.py`) that raises on anything it doesn't support instead of guessing.

### Events CSV

```
user_id,experiment,timestamp,converted,checkout_error
u00388,ab-checkout-copy,2026-09-01T00:01:00Z,0,0
u00388,ab-checkout-copy,2026-09-01T01:12:00Z,1,0
```

`user_id`, `experiment`, `timestamp` (ISO 8601, naive means UTC), and `converted` (0/1) are required. Every extra column is a guardrail metric, 0/1 per row, blank meaning 0. Any row counts as an exposure. A user converted if any of their in-window rows has `converted=1`, and was flagged on a guardrail if any row has it set.

## The statistics

Everything is implemented from scratch in `stats.py` with `math`. `statistics.NormalDist` supplies normal quantiles (1.96 and friends).

**Two-proportion z-test** (primary metric, and each guardrail metric):

```
p1 = x1 / n1        p2 = x2 / n2        p = (x1 + x2) / (n1 + n2)
SE = sqrt( p * (1 - p) * (1/n1 + 1/n2) )
z  = (p2 - p1) / SE
p-value (two-sided) = erfc( |z| / sqrt(2) )

CI for p2 - p1:  (p2 - p1) +/- z_(1 - alpha/2) * sqrt( p1(1-p1)/n1 + p2(1-p2)/n2 )
```

Worked example, checked in `tests/test_stats.py`: 100/1000 vs 130/1000 gives pooled p = 0.115, SE = 0.014267, z = 2.1027, p = 0.0355.

**Sample-ratio mismatch**, Pearson chi-square goodness of fit against the planned split:

```
E_i  = N * w_i
chi2 = sum( (O_i - E_i)^2 / E_i )
p-value = erfc( sqrt(chi2 / 2) )        (two arms, so df = 1 and chi2 = z^2)
```

The default threshold is p < 0.001, the industry convention. SRM is checked often and must not cry wolf, but when it fires, the data is broken.

**Minimum sample** (fixed-horizon power analysis), for baseline p1, target p2 = p1 + delta, allocation ratio k = n2/n1:

```
pbar = (p1 + k * p2) / (1 + k)
n1 = ( z_(1-alpha/2) * sqrt(pbar(1-pbar)(1 + 1/k)) + z_(1-beta) * sqrt(p1(1-p1) + p2(1-p2)/k) )^2 / delta^2
n2 = k * n1
```

For 10% baseline, +2.5 pp, alpha 0.05, power 0.8, 50/50: 2,507 per arm.

### Verdicts

First match wins:

1. **INVALID**: any integrity guardrail failed (no exposures, SRM, peeking, below minimum sample, unmeasured guardrail metric). The numbers are still printed, labeled not decision-grade, next to what a naive dashboard would have concluded.
2. **KILL**: a guardrail metric got significantly worse, or the primary metric did.
3. **SHIP**: the primary metric improved significantly and nothing above fired.
4. **INCONCLUSIVE**: a valid test that found no detectable difference. The reason states the CI and the effect the test was powered for.

Every readout request is logged. A read before the end date shows `look #N`. The final read notes how many earlier looks happened. Earlier looks don't void a final read, because the error inflation comes from *stopping* on an early look, and a test that ran to its horizon didn't stop.

## Demo walkthrough

Fixtures are simulated by `scripts/gen_fixtures.py` with fixed RNG seeds, using the kit's own hash assignment. The generator asserts each scenario's verdict. All output below is real.

**Setup.** Register, ingest, ingest again:

```
$ experiment-kit hypothesis add fixtures/hypotheses/*
registered ab-checkout-copy: 2026-09-01 to 2026-09-14, split 50/50, seed 'ab-checkout-copy-v1', min sample 2,507 control / 2,507 treatment (to detect +2.50 pp on 10.00% at alpha 0.05, power 0.8)
...
$ experiment-kit ingest --events fixtures/events/*.csv
fixtures/events/ab_checkout_copy.csv: 8,920 valid rows, 8,919 new, 1 duplicates skipped, 3 bad rows
  skipped ab_checkout_copy.csv:12: bad timestamp 'not-a-timestamp', row skipped
  skipped ab_checkout_copy.csv:22: converted must be 0 or 1, row skipped
  skipped ab_checkout_copy.csv:32: empty user_id or experiment, row skipped
...
$ experiment-kit ingest --events fixtures/events/ab_checkout_copy.csv
fixtures/events/ab_checkout_copy.csv: 8,920 valid rows, 0 new, 8,920 duplicates skipped, 3 bad rows
```

**Assignment is deterministic:**

```
$ experiment-kit assign --experiment ab-checkout-copy --users fixtures/users.csv --output assignments.csv
assigned 20 users to ab-checkout-copy (seed 'ab-checkout-copy-v1', split 50/50): control 10, treatment 10
determinism: run 1 sha256 7a8d5a066b624236, run 2 sha256 7a8d5a066b624236: identical
stored: 20 new, 0 already stored and re-derived identically
```

Run it again and you get `0 new, 20 already stored and re-derived identically`.

### Demo 1: an A/A test reads null

Both arms see the same page. If the pipeline is sound, the kit must find nothing.

```
$ experiment-kit readout --experiment aa-homepage-hero --as-of 2026-09-14
  arm           users  conversions     rate
  control       2,982          301   10.09%
  treatment     3,018          316   10.47%

  lift: +0.38 pp absolute, +3.7% relative, 95% CI [-1.16 pp, +1.91 pp]
  z = 0.480, p = 0.6311 (two-proportion z-test, two-sided)

Guardrails
  [PASS] sample ratio: observed 49.7/50.3 vs planned 50/50 (chi2 = 0.22, p = 0.64, threshold 0.001)
  [PASS] timing: read on 2026-09-14, at or after the planned end 2026-09-14
  [PASS] sample size: 2,982 control / 3,018 treatment, minimum 2,507 control / 2,507 treatment
  [PASS] guardrail bounced: control 29.51%, treatment 29.46% (-0.05 pp, p = 0.96)

Verdict: INCONCLUSIVE
  - no detectable difference: +0.38 pp on signup_conversion (p = 0.63), 95% CI [-1.16 pp, +1.91 pp]
    includes zero. The test was powered for +2.50 pp; a smaller effect may exist. Do not ship on this
```

### Demo 2: an A/B with a real lift ships

The simulated truth is 10% vs 13%.

```
$ experiment-kit readout --experiment ab-checkout-copy --as-of 2026-09-14
  arm           users  conversions     rate
  control       4,000          430   10.75%
  treatment     4,000          489   12.22%

  lift: +1.47 pp absolute, +13.7% relative, 95% CI [+0.08 pp, +2.87 pp]
  z = 2.069, p = 0.0386 (two-proportion z-test, two-sided)

Guardrails
  [PASS] sample ratio: observed 50.0/50.0 vs planned 50/50 (chi2 = 0.00, p = 1, threshold 0.001)
  [PASS] timing: read on 2026-09-14, at or after the planned end 2026-09-14
  [PASS] sample size: 4,000 control / 4,000 treatment, minimum 2,507 control / 2,507 treatment
  [PASS] guardrail checkout_error: control 3.25%, treatment 3.12% (-0.13 pp, p = 0.75)

Verdict: SHIP
  - treatment beat control by +1.47 pp on checkout_conversion (p = 0.039) with every guardrail passing
```

The direction and the verdict are right. The magnitude is not: the true lift is +3.0 pp, and this draw's 95% CI just misses it, which a 95% interval does about 1 time in 20. Across 200 seeds of this scenario the mean estimated lift is +3.00 pp, the CI covers the truth 95.5% of the time, and the kit says SHIP in 193 of 200. That's why the readout leads with an interval, not a point. Treat the point estimate as noisy and plan rollouts on the low end of the CI.

### Demo 3: peeked early, flagged INVALID

This is the one that matters. The pricing-page experiment has **no true effect**. On day 5 of 21, the dashboard shows +2.9 pp at p = 0.033:

```
$ experiment-kit readout --experiment peek-pricing-page --as-of 2026-09-05
Numbers below are NOT decision-grade (see guardrails).
  arm           users  conversions     rate
  control       1,005           90    8.96%
  treatment       995          118   11.86%

  lift: +2.90 pp absolute, +32.4% relative, 95% CI [+0.23 pp, +5.58 pp]
  z = 2.127, p = 0.0334 (two-proportion z-test, two-sided)
  (7,023 events outside the window were ignored)

Guardrails
  [PASS] sample ratio: observed 50.2/49.8 vs planned 50/50 (chi2 = 0.05, p = 0.82, threshold 0.001)
  [FAIL] timing: peeking: read on 2026-09-05, 16 days before the planned end 2026-09-21 (look #1 before
         the end date). A fixed-horizon p-value is only valid at the planned horizon; stopping on an
         early good number inflates false positives
  [FAIL] sample size: underpowered: control 1,005/3,841, treatment 995/3,841 users against the
         preregistered minimum (3,841 control / 3,841 treatment). No verdict below the sample the test
         was powered for
  [PASS] guardrail support_ticket: control 3.78%, treatment 3.82% (+0.04 pp, p = 0.96)

Verdict: INVALID
  - peeking: read on 2026-09-05, 16 days before the planned end 2026-09-21 ...
  - underpowered: control 1,005/3,841, treatment 995/3,841 users ...

A naive dashboard would have said: SHIP (+2.90 pp, p = 0.0334). The kit issues no verdict on this data.
```

Try to ship anyway:

```
$ experiment-kit hypothesis close peek-pricing-page --decision ship
error: the kit's latest verdict for peek-pricing-page is INVALID, not SHIP; shipping anyway needs --override and a --note explaining why
```

Wait for the planned end date and the "win" is gone:

```
$ experiment-kit readout --experiment peek-pricing-page --as-of 2026-09-21
  arm           users  conversions     rate
  control       4,158          409    9.84%
  treatment     4,242          422    9.95%

  lift: +0.11 pp absolute, +1.1% relative, 95% CI [-1.17 pp, +1.39 pp]
  z = 0.171, p = 0.8639 (two-proportion z-test, two-sided)

Guardrails
  [PASS] timing: read on 2026-09-21, at or after the planned end 2026-09-21; 1 earlier look was logged
         before the end date, but the test ran to its horizon, so this read stands
  ...
Verdict: INCONCLUSIVE
```

Full disclosure on how this fixture was made: the generator searches RNG seeds for a null experiment whose day-5 look crosses p < 0.05. That isn't hiding anything. It builds the exact situation the guardrail exists for, and the situation is common. `scripts/demo.py` simulates 2,000 null experiments:

```
read once, at day 14:             4.3% false winners (alpha is 5%)
peek daily, stop at p < 0.05:    21.7% false winners
```

Daily peeking turns a 5% false-positive rate into roughly 22%. `tests/test_simulate.py` keeps this property under test.

### Bonus: sample-ratio mismatch

The onboarding-tour fixture simulates a treatment whose exposure event fails to fire for 15% of users who bounce. The planned split is 50/50 and the observed split is 53.3/46.7:

```
  [FAIL] sample ratio: sample-ratio mismatch: observed 53.3/46.7 vs planned 50/50 (chi2 = 24.27,
         p = 8.4e-07, threshold 0.001). Assignment or exposure logging is broken; lift numbers from
         this split cannot be trusted
Verdict: INVALID
```

## Known error budget

A guardrail is a statistical test too, and each one spends some false-alarm budget. Across 200 seeds of the A/A fixture (one guardrail metric, reproduce with `scripts/calibrate.py`), the kit read INCONCLUSIVE 182 times, SHIP 7, and KILL 11, about 9% non-null in total. That matches the math: about 5% from the two-sided primary test, plus about 2.5% from the one-sided "guardrail got worse" check. The KILLs are the conservative direction, which is what you want from a guardrail. Still, every guardrail metric you add raises the false-KILL rate by about alpha/2. Pick a few that matter.

## What v1 does NOT do

Honest limits, on purpose:

- **No sequential testing.** p-values are fixed-horizon and valid once, at the planned end date. That's why peeking is INVALID rather than "adjusted". There are no always-valid p-values, alpha spending, or mSPRT.
- **No Bayesian option.** No posteriors, no "probability to beat control", no expected loss.
- **Two arms only.** One control, one treatment, any split. No multi-arm tests and therefore no multiple-comparison correction across arms.
- **Binary metrics only.** Conversion-style 0/1 per user. No revenue, means, or other continuous metrics, no ratio metrics (per-session, per-converter), no delta method.
- **Guardrails are per exposed user.** A metric that scales with conversions (refunds, chargebacks) rises when conversions rise and can trip a false KILL. Use metrics that are independent of the primary per exposed user (errors, tickets, unsubscribes) until ratio metrics exist.
- **No variance reduction** (CUPED), no stratification, no novelty-effect or day-of-week analysis.
- **No traffic exclusion or mutual-exclusion layers** between concurrent experiments, and no holdouts.
- **No real-time ingestion or warehouse connection.** CSV in, SQLite at rest.
- **Users are the unit.** No cluster or account-level randomization.

## v2 roadmap

1. **Sequential testing.** Always-valid inference (mSPRT or a group-sequential design with O'Brien-Fleming alpha spending), so an interim look can be decision-grade by design. The readout log already counts looks, which is the bookkeeping this needs.
2. **Stratified assignment.** Balance arms on a pre-period covariate (plan, country, platform) and report SRM within strata, so a skew in one segment can't hide in the total.
3. **Warehouse integrations.** Read exposures and conversions from BigQuery, Snowflake, or ClickHouse by SQL template instead of CSV, keeping the same strict-columns contract.
4. Continuous and ratio metrics with the delta method, then CUPED.
5. Multi-arm tests with Holm-Bonferroni correction.

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy --strict src scripts tests
PYTHONPATH=src python scripts/gen_fixtures.py   # regenerate fixtures (deterministic)
PYTHONPATH=src python scripts/demo.py           # all scenarios end to end
PYTHONPATH=src python scripts/calibrate.py      # verdict rates across 200 seeds (the README's error budget)
```

CI (GitHub Actions) runs the tests, `mypy --strict`, and the demo on Python 3.11 and 3.12.

| Module | Responsibility |
|---|---|
| `registry.py` | Validate and freeze hypothesis records, compute minimum sample, closing rules |
| `assignment.py` | Hash-based deterministic assignment and run digests |
| `events.py` | CSV parsing: strict columns, lenient rows, content-hash event ids |
| `stats.py` | z-test, SRM chi-square, sample size, all from scratch |
| `readout.py` | Guardrails, verdict precedence, rendering |
| `simulate.py` | Monte Carlo of null experiments (peeking cost) |
| `storage.py` | SQLite: hypotheses, assignments, events, readout log |
| `yamlish.py` | Strict YAML subset parser |
