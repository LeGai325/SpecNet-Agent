# SpecNet-Agent isolated proof workspace

## Deep research and optimization guide

The evidence interpretation, field lineage, terminology encyclopedia, protocol
audit, and optimization results are documented in
[`DEEP_RESEARCH_GUIDE.md`](DEEP_RESEARCH_GUIDE.md).

For a first reading, start with the problem-first explanation:
[`BEGINNER_GUIDE.md`](BEGINNER_GUIDE.md). It introduces the need, design,
mechanism, and only then names terms such as ablation, baseline, bandit, and
bootstrap.

Canonical completed runs:

- [`QUALITY_CONTRACT_BROKER_EXPERIMENT_20260816.md`](QUALITY_CONTRACT_BROKER_EXPERIMENT_20260816.md) -- validation-only quality-contract paradigm: exact V4 equivalence, uncertainty feasibility frontier, 0.95/0.94 tier negotiation, byte-debt invariants, and explicit limits before simulator integration
- [`quality_contract_validation_audit_20260816/QUALITY_CONTRACT_TRACE_AUDIT.md`](results/quality_contract_validation_audit_20260816/QUALITY_CONTRACT_TRACE_AUDIT.md) -- V1/V2 validation admission audit with per-workflow and per-template artifacts; admission bytes are not served bytes or energy
- [`TRACE_DEPLOYMENT_V5_RESOURCE_REFINEMENT_REPORT_20260806.md`](TRACE_DEPLOYMENT_V5_RESOURCE_REFINEMENT_REPORT_20260806.md) -- capacity-consistent V5 refinement: staged optional completion priority, fresh V3 confirmation, V1/V2 external tests, and the residual queue-tail trade-off
- [`TRACE_THREE_SIGNAL_FOLLOWUP_REPORT_20260806.md`](TRACE_THREE_SIGNAL_FOLLOWUP_REPORT_20260806.md) -- trace-profile three-signal transfer: V3 held-out confirmation, V1/V2 robustness checks, retained quality-failure diagnosis, and trace TTL/resource next steps
- [`trace_v3_test_v3_merged_20260806/TRACE_FACTORIZED_SIGNAL_REPORT.md`](results/trace_v3_test_v3_merged_20260806/TRACE_FACTORIZED_SIGNAL_REPORT.md) -- V3 test split, 18 balanced scenarios x 2 independent runs; all three quality-gated broad/nonjoint ablations support the v3 successor mechanism
- [`TRACE_GUARD_RESOURCE_AUDIT_20260806.md`](TRACE_GUARD_RESOURCE_AUDIT_20260806.md) -- post-confirmation V3 paired audit: the quality guard raises p99 latency, utilization, served-byte energy proxy, and speculative waste
- [`DEADLINE_AWARE_TTL_ADDENDUM_20260805.md`](DEADLINE_AWARE_TTL_ADDENDUM_20260805.md) -- current finite-TTL result: earliest-expiry-first deferred scheduling, TTL=1536, and 81-cell independent confirmation; reports the residual per-cell utilization tail explicitly
- [`deadline_aware_ttl_confirm_v1_20260805/DEADLINE_AWARE_TTL_REPORT.md`](results/deadline_aware_ttl_confirm_v1_20260805/DEADLINE_AWARE_TTL_REPORT.md) -- v1 confirmation: all frozen background, workflow floor, expiry, foreground-parity, quality, and mean-resource gates pass at TTL=1536
- [`WEEKLY_TTL_UPDATE_20260805.md`](WEEKLY_TTL_UPDATE_20260805.md) -- historical presentation update for the equal-share TTL=2048 baseline
- [`DEPLOYMENT_TTL_ADDENDUM_20260805.md`](DEPLOYMENT_TTL_ADDENDUM_20260805.md) -- equal-share finite-TTL baseline, including the rejected TTL=512 and pressure-visibility failure paths
- [`eligible_window_ttl_confirm_v3_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md`](results/eligible_window_ttl_confirm_v3_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md) -- v5 final confirmation: all TTL, per-workflow floor, foreground parity, quality, and utilization gates pass on 27 scenarios x 3 fresh runs
- [`WEEKLY_RESEARCH_BRIEF_20260805.md`](WEEKLY_RESEARCH_BRIEF_20260805.md) -- current presentation-ready weekly brief: v3 semantic repair, independent confirmation, innovations, limits, and next-week plan
- [`THREE_WEEK_PROOF_REPORT_20260802.md`](THREE_WEEK_PROOF_REPORT_20260802.md) -- canonical consolidated report, corrected on 2026-08-05 to retire the v2 lifecycle claim and record v3 evidence
- [`factorized_background_eligible_confirm_v3_20260805/FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md`](results/factorized_background_eligible_confirm_v3_20260805/FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md) -- 45 scenarios x 3 fresh runs; background/quality/p99/miss, all broad/nonjoint gates, and strict foreground parity pass under explicitly extended lifecycle semantics
- [`eligible_window_paired_audit_v2_20260805/ELIGIBLE_WINDOW_PAIRED_AUDIT.md`](results/eligible_window_paired_audit_v2_20260805/ELIGIBLE_WINDOW_PAIRED_AUDIT.md) -- paired foreground parity, background gain, utilization, and drain audit for v3
- [`eligible_window_floor_audit_v1_20260805/ELIGIBLE_WINDOW_FLOOR_AUDIT.md`](results/eligible_window_floor_audit_v1_20260805/ELIGIBLE_WINDOW_FLOOR_AUDIT.md) -- frozen-seed replay separating genuine per-workflow floor violations from floating-point rounding
- [`eligible_window_ttl_confirm_v1_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md`](results/eligible_window_ttl_confirm_v1_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md) -- historical only: TTL=512 failed the independent cell-floor gate
- [`eligible_window_ttl_confirm_v2_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md`](results/eligible_window_ttl_confirm_v2_20260805/ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md) -- historical only: pre-quiescent deferred visibility changed one foreground waste trajectory
- [`factorized_background_eligible_confirm_v2_20260730/FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md`](results/factorized_background_eligible_confirm_v2_20260730/FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md) -- historical only: pre-completion target truncation changed the foreground path; do not cite as the current lifecycle result
- [`factorized_background_select_v1_1_20260730/FACTORIZED_BACKGROUND_REPORT.md`](results/factorized_background_select_v1_1_20260730/FACTORIZED_BACKGROUND_REPORT.md) -- permanent background-weight search; no candidate passes every frozen gate
- [`factorized_background_deficit_refined_v1_20260730/FACTORIZED_BACKGROUND_DEFICIT_REPORT.md`](results/factorized_background_deficit_refined_v1_20260730/FACTORIZED_BACKGROUND_DEFICIT_REPORT.md) -- refined deficit-aware search; no original-semantics candidate passes background, p99, and miss together
- [`factorized_signal_confirm_v1_20260730/FACTORIZED_SIGNAL_REPORT.md`](results/factorized_signal_confirm_v1_20260730/FACTORIZED_SIGNAL_REPORT.md) -- strongest current three-signal broad confirmation; all broad/nonjoint gates pass on 81 scenarios x 5 fresh runs
- [`factorized_global_diagnostic_v1_20260730/FACTORIZED_GLOBAL_DIAGNOSTIC.md`](results/factorized_global_diagnostic_v1_20260730/FACTORIZED_GLOBAL_DIAGNOSTIC.md) -- fresh-seed global audit; strong latency/miss/waste gains but background floor failure
- [`RESEARCH_PROGRESS_20260730.md`](RESEARCH_PROGRESS_20260730.md) -- latest consolidated report, including all three-signal attempts, negative results, innovations, and outlook
- [`three_signal_rule_conditional_v2_2_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_conditional_v2_2_20260730/THREE_SIGNAL_RULE_REPORT.md) -- three supported identifiable-context effects on 81 scenarios x 5 fresh runs; broad slack remains unsupported
- [`three_signal_rule_replication_v2_1_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_replication_v2_1_20260730/THREE_SIGNAL_RULE_REPORT.md) -- post-confirmation high-power broad/nonjoint replication
- [`three_signal_rule_confirm_v2_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_confirm_v2_20260730/THREE_SIGNAL_RULE_REPORT.md) -- initial frozen full confirmation
- [`RESEARCH_PROGRESS_20260729.md`](RESEARCH_PROGRESS_20260729.md) -- consolidated current status, v4.3 results, innovations, and outlook
- [`optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md`](results/optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md) -- fresh holdout, nine deployment gates, and resumable training
- [`proof_full_v2_20260719/PROOF_REPORT.md`](results/proof_full_v2_20260719/PROOF_REPORT.md)
- [`optimization_full_v3_20260719/OPTIMIZATION_REPORT.md`](results/optimization_full_v3_20260719/OPTIMIZATION_REPORT.md)
- [`pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md`](results/pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md)
- [`source_control_isolation_full_20260723_v2/SOURCE_CONTROL_ISOLATION_REPORT.md`](results/source_control_isolation_full_20260723_v2/SOURCE_CONTROL_ISOLATION_REPORT.md)
- [`oracle_gap_full_20260723/ORACLE_GAP_REPORT.md`](results/oracle_gap_full_20260723/ORACLE_GAP_REPORT.md)
- [`finite_monotonicity_v2_20260729/FINITE_MONOTONICITY_REPORT.md`](results/finite_monotonicity_v2_20260729/FINITE_MONOTONICITY_REPORT.md) -- corrected full Cartesian enumeration
- [`EXPERIMENT_STATUS_20260723.md`](EXPERIMENT_STATUS_20260723.md)
- [`MATHEMATICAL_GUARANTEES.md`](MATHEMATICAL_GUARANTEES.md)

Do not cite `results/full`: it was generated by the obsolete evidence protocol.
Do not cite `results/finite_monotonicity_20260723`: iterator exhaustion left
that run at 819 cases; the v2 result above covers all 223,587 cases.

This directory validates the three claims described in
`../SpecNet-Agent_三项证明设计与意见.md` without modifying the main project.

The harness loads the canonical simulator as a read-only Python module. It
first resolves the repository-local snapshot:

`../../organized_code_files/source_snapshot/specnet_agent_experiments/specnet_agent_experiment.py`

Only when that file is unavailable does it fall back to the legacy server
path. `SPECNET_UPSTREAM` can explicitly select another read-only snapshot.

All extensions, runs, and generated evidence remain under this directory.

## Commands

```bash
python specnet_proofs/proof_harness.py --mode smoke
python specnet_proofs/proof_harness.py --mode full

# From the student15267 parent directory, the module form is preferred:
python -m specnet_proofs.proof_harness --mode smoke
```

Long full runs are resumable by stage:

```bash
python specnet_proofs/proof_harness.py --mode full --stage rq1
python specnet_proofs/proof_harness.py --mode full --stage rq1-analysis
python specnet_proofs/proof_harness.py --mode full --stage rq2
python specnet_proofs/proof_harness.py --mode full --stage rq3
python specnet_proofs/proof_harness.py --mode full --stage report
```

`full_bandit.json` is the frozen controller checkpoint shared by the resumed
RQ2/RQ3 stages. `run_manifest.json` records completed stages and verifies that
the upstream simulator hash is unchanged. The final `PROOF_REPORT.md` and
`claim_verdicts.csv` deliberately report unsupported claims as unsupported.

`smoke` checks correctness and state coverage with small budgets. `full` uses
20 paired evaluation seeds for RQ1 and 10 training seeds for RQ3 stability.

## Revised evidence protocol

The harness deliberately separates three questions that the original summary
could conflate:

- RQ1 confidence intervals use a paired, scenario-stratified bootstrap. Each
  fixed stress scenario receives equal weight, and runs are resampled only
  within scenario. Claim verdicts are generated from the preregistered
  direction, the stratified CI, and the Holm-adjusted p-value rather than being
  hard-coded in the report.
- RQ2 searches 96 rule candidates in `full` mode and exports both raw
  four-objective dominance and quality-constrained paired comparisons in
  `rule_bandit_pairwise*.csv`. Search and observed-interaction budgets are
  recorded in `selected_rule.json`.
- RQ3 reports action-table agreement separately from held-out performance
  equivalence. The latter uses explicit practical-equivalence margins and is
  exported in `policy_stability_equivalence.csv`.

After changing the evidence protocol, run `smoke` first and then rerun every
`full` stage into a new versioned directory. Never replace or cite the stale
`results/full/PROOF_REPORT.md`.

The revised protocol writes new evidence under a dated output directory. For
example:

```bash
python -m specnet_proofs.proof_harness --mode full \
  --output-dir specnet_proofs/results/proof_full_v2_20260719
```

The controller optimization study is intentionally separate from the three
proof claims. It compares scheduled learning, a fairness-aligned reward,
median Q-table ensembles, confidence fallback, fixed moderate control, and a
validation-frozen rule:

```bash
python -m specnet_proofs.optimization_study --mode smoke \
  --output-dir specnet_proofs/results/optimization_smoke_v3_20260719
python -m specnet_proofs.optimization_study --mode full \
  --output-dir specnet_proofs/results/optimization_full_v3_20260719
```

The two follow-up studies separate source admission from queue scheduling and
measure the counterfactual distance to the best per-workflow action:

```bash
python -m specnet_proofs.source_control_isolation --mode smoke
python -m specnet_proofs.oracle_gap_study --mode smoke
```

The independent three-signal studies preserve the original H1-P verdict while
testing the frozen `active_speculative_backlog` definition. The learned-policy
study records multi-seed failures; the monotone-rule study separates broad
effects from the later identifiable-context conditional hypothesis:

```bash
python -m specnet_proofs.three_signal_confirmation_study --mode smoke
python -m specnet_proofs.three_signal_rule_study --mode smoke
python -m specnet_proofs.three_signal_rule_study --mode conditional \
  --frozen-candidate specnet_proofs/results/three_signal_rule_smoke_v2_20260730/selected_candidate.json
```

Do not summarize the conditional run as three supported broad effects. Its
fresh holdout supports all three nonjoint identifiable-context effects, while
the same run's broad slack diagnostic is negative.

The later factorized study is the current broad mechanism result. It assigns
pressure, congestion, and slack to independent admission/global-scheduling/
deadline-scheduling paths and confirms all three broad effects on fresh seeds:

```bash
python -m specnet_proofs.factorized_signal_study --mode smoke
python -m specnet_proofs.factorized_signal_study --mode confirm \
  --frozen-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json
python -m specnet_proofs.factorized_signal_diagnostic \
  --frozen-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json
```

Do not call the factorized controller deployable: its fresh global diagnostic
reports mean background service `0.1053`, far below the frozen `0.20` floor.

Run the tests from the `student15267` parent directory (one level above this
README), so the namespace package import resolves correctly:

```bash
python -m unittest specnet_proofs.test_proof_harness \
  specnet_proofs.test_optimization_study -v
```

The quality-contract foundation and validation-only trace audit run separately
from the frozen three-proof protocol:

```bash
python -m unittest specnet_proofs.test_quality_contract_broker \
  specnet_proofs.test_quality_contract_trace_audit -v
python -m specnet_proofs.quality_contract_trace_audit
```

The audit intentionally has no `--split test` option. Its `0.94` degraded tier
is a validation-selected candidate that must be frozen before independent
confirmation; it is not a production quality SLO.
