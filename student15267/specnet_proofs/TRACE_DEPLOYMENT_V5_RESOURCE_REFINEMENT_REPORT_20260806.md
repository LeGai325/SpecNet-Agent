# Trace V5 Resource-Consistent Refinement Report

Date: 2026-08-06  
Status: independent capacity-corrected refinement after V4. Historical V3/V4
artifacts were not altered.

## Outcome

The current frozen candidate is **V5-staged-b1-t0.75-z96**:

- exact V4 minimum-quality optional admission (`quality >= 0.95`);
- early selected-optional multiplier `1x`;
- switch to `96x` once 75% of the workflow's required branch bytes have
  drained, or once the workflow enters its LLM stage;
- wait for the selected optional set before judge launch.

On fresh V3-validation seeds (18 balanced scenarios x 2 runs), it retains a
quality target ratio of `1.0` in every scenario/run cell and every template,
while improving latency and resource use relative to the capacity-corrected
V4 reference. It also passes the same core gates on the V1 and V2 external
test profiles.

This is a **deployment candidate under the simulator**, not a claim of global
or production optimality. A queue-pressure tail trade-off appears on the two
external profiles and must be guarded before deployment.

## Resource-Model Correction

The inherited `ProofSimulator` first creates the actual service pool as
`path_capacities={shared: 16}`. `ProofSimulator.__init__` then scales only
`self.capacity`. As a result, a requested `capacity_scale=0.72` produced an
11.52 state-estimation capacity but still served traffic from a 16.0-capacity
pool; `capacity_scale=1.25` produced a 20.0 estimate but still served from
the same 16.0 pool.

V5 fixes this only in a new derived simulator: it scales both the state
capacity and the path capacity used by `serve_capacity_pool`. It records
per-epoch utilization and path queue-pressure p95/p99. This materially
improves the resource-constraint validity of the new experiment, but means
older V3/V4 results must not be interpreted as true capacity-scaling evidence.

## Mechanism

V4 already removes unnecessary optional work by exactly enumerating the
minimum-byte optional subset whose predicted retained utility reaches 0.95.
V5 changes only the service timing of that selected subset.

1. Required work keeps the ordinary factorized three-signal priority while
   selected optional flows start at `1x`.
2. When required-branch drain reaches 75%, all remaining selected optional
   flows receive the frozen `96x` completion multiplier (and the inherited
   tight-workflow compensation).
3. The LLM can overlap optional completion, but judge launch requires the
   selected optional set to have completed. This makes quality completion an
   explicit readiness condition instead of relying only on a static priority.

The key design claim is modest: **delay the expensive optional priority until
the critical branch barrier is substantially drained, then use a bounded
completion reservation**. It is not a successful general dynamic-deadline
controller.

## Protocol

| Stage | Profile/split | Work | Seeds | Parameter use |
|---|---|---:|---|---|
| Screen | V3 validation | 9 scenarios x 1 run | `2480000 + scenario` | Grid: base `{1,4,8,16,32}`, trigger `{0,.25,.5,.75}`; terminal fixed at 96 |
| Freeze confirmation | V3 validation | 18 scenarios x 2 runs | `2500000 + run*10000 + scenario` | Only `b1,t.75,z96` |
| External confirmation | V1 test | 18 scenarios x 2 runs | `2510000 + run*10000 + scenario` | Only frozen candidate |
| External confirmation | V2 test | 18 scenarios x 2 runs | `2520000 + run*10000 + scenario` | Only frozen candidate |

All comparisons use the same scenario and workload seed within a cell. Core
gates are: mean quality >= 0.95, mean target ratio >= 0.95, every cell and
template target ratio = 1.0, no worse p99 latency, no worse miss ratio by more
than 0.02, and lower served bytes and mean link utilization than corrected V4.

## Frozen V3 Validation Confirmation

| Metric | V3 static 100x | V4-minQ-96 | V5-staged | V5 vs V4 |
|---|---:|---:|---:|---:|
| Average quality | 0.999459 | 0.975858 | 0.975858 | 0.000000 |
| Worst cell target ratio | 1.000000 | 1.000000 | 1.000000 | 0.000000 |
| Worst template target ratio | 1.000000 | 1.000000 | 1.000000 | 0.000000 |
| p99 latency | 344.731 | 204.319 | 190.514 | -13.806 (-6.8%) |
| Deadline miss ratio | 0.380607 | 0.242879 | 0.229986 | -0.012893 |
| Served bytes | 8734.95 | 6854.10 | 6838.51 | -15.58 (-0.23%) |
| Mean link utilization | 0.729489 | 0.626915 | 0.625620 | -0.001296 |
| Speculative waste/workflow | 87.310 | 0.000 | 0.000 | 0.000 |
| Background bytes/workflow | not gated | 21.517 | 20.626 | -0.891 |
| Path queue-pressure p99 | 295.960 | 210.020 | 208.029 | -1.991 |

The chosen barrier was invoked infrequently: `0.236` wait epochs per workflow
on average, with `0.00345` of workflows waiting at all. The quality barrier is
therefore a tail safety mechanism rather than a pervasive source of delay.

Artifacts: [screen](results/trace_deployment_v5_resource_screen_20260806/),
[fresh V3 confirmation](results/trace_deployment_v5_resource_confirmation_20260806/).

## External Tests

| Test profile | Quality target ratio | V4 p99 -> V5 p99 | V4 served -> V5 served | V4 queue p99 -> V5 queue p99 | Core-gate result |
|---|---:|---:|---:|---:|---|
| V1 test | 1.000000 in every cell/template | 173.574 -> 160.499 | 6413.88 -> 6402.90 | 185.713 -> 188.224 | Pass |
| V2 test | 1.000000 in every cell/template | 144.688 -> 135.521 | 6170.67 -> 6161.91 | 165.461 -> 166.603 | Pass |

V1/V2 both improve core latency, miss, bytes, and mean utilization relative
to corrected V4. Their queue-pressure p99 is respectively `+2.511` and
`+1.143` higher. This is a real negative result: V5 is not uniformly better
on every tail resource metric across profiles.

Artifacts: [V1 external test](results/trace_deployment_v5_v1_external_confirmation_20260806/),
[V2 external test](results/trace_deployment_v5_v2_external_confirmation_20260806/).

## Innovation and Limits

The practical innovation is the combination of:

- **capacity-consistent evaluation**, separating real service resources from
  controller state estimates;
- **minimum-quality admission**, inherited from V4, which makes zero
  speculative waste possible in these runs;
- **stage-calibrated completion priority**, which lets required traffic lead
  early and reserves the expensive boost for the approach to judge readiness;
- **explicit quality readiness**, which turns a scheduling intention into an
  observable pre-judge condition.

It does not establish an optimal policy because:

- V5 tuning used the V3 validation split; fresh seeds reduce simulation noise
  but do not create a new V3 data split.
- Quality remains retained-branch utility, not end-to-end task success or
  human evaluation.
- V1/V2 queue-pressure p99 worsens slightly, so a queue-tail hard guard has
  not yet been validated.
- There is no NIC/GPU power telemetry, multi-path fairness, tenant fairness,
  or background-service floor in the deployment gate.

## Deployment Recommendation

Use V5 only as a staged canary candidate. Its runtime should export a rolling
per-path queue-pressure p99 and fall back to V4-minQ-96 whenever a calibrated
queue-tail budget is breached. The fallback threshold must be learned on a
new V3 holdout or a new trace slice, not from the V1/V2 tests above.

Before a deployment claim, acquire a new V3 holdout and run the frozen V5
parameters once; add a strict queue-tail and background-service gate; then
validate retained-utility against real task success and NIC/GPU telemetry.
