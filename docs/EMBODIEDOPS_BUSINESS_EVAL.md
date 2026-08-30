# EmbodiedOps Business Evaluation Boundary

## What this measures

`eval/embodied_business_cases.jsonl` is a deterministic, synthetic fixture for
testing the calculation and presentation of product metrics. It measures the
post-experiment task outcome, human acceptance, diagnosis latency, collision,
timeout, diagnosis agreement, and unknown-event rate. Every rate retains a
numerator and denominator; an empty denominator is represented as `null`, not
as `0%`.

The diagnosis metrics are intentionally narrower: they count only cases with a
double-reviewed or adjudicated root-cause annotation for the same dataset
version and episode. A raw model response and a single-review annotation never
become business ground truth.

## Fixed offline fixture

All rows state:

- `real_robot_data=false`
- `manual_review_status=not_performed`
- `live_model=false` in the generated report

The fixture's before/after success change is a regression-test label. It is not
a measured productivity gain, a causal experiment result, or a production ROI
claim. The file contains no Unitree, UBTECH, Fourier, customer, or internal
robot telemetry.

Run the diagnostic and business fixtures together:

```powershell
.venv\Scripts\python.exe eval\run_embodied_eval.py `
  --fixture eval\embodied_gold_cases.jsonl `
  --output eval\embodied_results.json `
  --business-fixture eval\embodied_business_cases.jsonl `
  --business-output eval\embodied_business_results.json
```

The command emits two separate report sections: `diagnosis_evaluation` keeps
raw-provider and validated-system evidence apart, while `business_evaluation`
contains only post-validation outcomes.

## Sim-to-real gate

`evaluate_sim_to_real` can display descriptive metric deltas, but it refuses a
transfer claim unless both simulation and real cohorts provide both:

1. an explicit holdout; and
2. permissioned provenance.

For a real cohort, callers must also mark `real_robot_data=true`. Even when all
gates are present, the result is `eligible_for_review`, not a declaration that
transfer or business ROI has been proven. A human review is still required
before any real-robot rollout decision.

## What would make this a real business evaluation

Before reporting operational impact, add a permissioned real-robot data card,
frozen holdout policy, independently reviewed annotations, pre-registered
primary and guardrail metrics, and an approved rollout decision. Compute ROI
only from measured labor, cycle-time, safety, and throughput inputs supplied by
the operating team; do not estimate it from this synthetic fixture.
