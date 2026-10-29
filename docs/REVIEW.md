# Independent algorithm-library completion audit

Use this policy with CONTROLLER.md and COMPLETION_REVIEWER.md for firmware-rewrite jobs. The watcher delegates completion audits to a fresh temporary reviewer; the worker never launches or controls its own reviewer. Review the actual
assignment and evidence, not a model's confidence, stage count or elapsed time.
The recorded review policy is `algorithm-library-v2`.

## Establish the assignment

Read the delivered task, generated run details, saved skill snapshot and later
explicit user scope changes. Check selected original hashes and version choice.
Prefer the saved assigned skill over the current upstream copy. Updating the
factory does not silently amend a running worker's assignment, overwrite old
reviews or authorize a scope-change message.

For historical jobs, honor their actual instructions and approved scope changes.
Record any additional explicit obligations under assignment. Do not infer new
whole-device obligations from the names of old stages or preservation branches.
If an outcome was not required by the saved assignment, record that applicability
and its evidence rather than silently adding the newer requirement.
Preserve existing work, archives and user changes.

## Review the outcomes

Use these exact keys in the `areas` evidence mapping:

| Area | Evidence to inspect |
|---|---|
| assignment | Actual brief, assigned skill, scope changes, original integrity and appropriate supplied version. |
| algorithms | Requested modes, controls, state and coordination recovered; evidence and documented gaps; no generic substitute for distinctive behavior. |
| library | Readable, reusable and modifiable C on main; clear algorithm structure, explicit state, caller-controlled memory/rate/block size, independent instances, platform separation, working external examples and any named host. |
| device | Working compiled C implementation of the complete device behavior, including its surrounding functionality; build/run evidence that the rewritten device works without original firmware execution in a CPU emulator or instruction interpreter. Straightforward recovered C and existing components are acceptable outside the readable algorithm library. |
| fidelity | Justified tolerances, independent references, rerunnable assembled-processing checks, relevant boundaries, brief defects and long-term drift, treatment of randomness, deliberate changes and honest uncertainty. |
| browser | Playable device-like panel and important controls, rewritten library actually producing processing, original/library A/B on comparable inputs/settings, real initialization/assets/audio path, running server and reachable access. |
| reuse | Useful component/snippet/pattern/idea/pitfall cards, indexed in reuse/README.md; actual library code, independent component builds and relevant tests; dependencies, costs and limitations. |

Inspect source directly. A wrapper around opaque decompilation does not meet the
library goal. Conversely, address-based state or hardware emulation in a reused
verification environment is not a defect in the algorithm library. However,
an emulator hosting original machine code around a new library does not fulfill
the compiled-device deliverable. Trace both the processing and surrounding
device behavior to the compiled implementation. Reference emulation for original
A/B remains permitted; verify that the rewritten side runs without it.

Establish whether another developer can understand and safely modify important
algorithms without repeating the RE. Trace a representative processing path and
a consequential parameter/timing change. Meaningful constants/maps need
discoverable purpose, units, origin and relevant update/rate assumptions.
Essential coefficients and tables can stay. Require bounded explanation/naming
where missing meaning materially obstructs modification; do not mandate macros,
name every literal or turn cosmetic cleanup into a gate. Record source evidence
in the library area, following COMPLETION_REVIEWER.md.

Numerical identity is not required. Accept justified differences expected to be
inaudible to human listeners during normal use in exchange for clearer code that
is easier to reuse and modify. Inspect whether tolerances measure relevant
behavior rather than merely pass existing output. Measurements alone do not
prove inaudibility; report listening evidence and its limits. An available A/B
switch enables human evaluation without requiring the reviewer to assert it heard
a result it did not hear.

Account for random processing using controlled random inputs where appropriate,
or multiple-run behavioral/statistical comparisons. Original-to-original
variation can inform the reference but does not automatically prove equivalence.
Sample equality or an average error alone is not a universal quality measure.

Run meaningful existing checks and bounded independent probes for concrete
uncertainties. Check reference independence, whether exercised modes match the
claims, and whether meaningful errors are detectable. Do not substitute test
counts, a green build or one isolated kernel test for assembled behavior.

Open and exercise the actual browser where practical. A port or HTTP 200 is not
browser verification. Verify required assets, initialization, useful audio,
controls and A/B routing; explain which machine serves it and how to reach it.

## Keep the review within scope

Separate exact/faithful branches, complete unmodified-device browser emulation,
exhaustive feature specifications, individual switches for every quirk and
three-version comparisons are not default completion requirements.

Reuse surrounding code through adapters or port recovered code to compiled C
as needed for device functionality. Do not demand the algorithm library's
readability/refactoring standard for all surrounding code. Adapt hardware
dependencies to the host instead of reconstructing its original scheduler,
drivers or boot sequence. Meaningful timing can be represented as audio-stream
events. A peripheral discrepancy alone is not a finding requiring more work.

Runtime under two hours is a reason to inspect coverage carefully, not evidence
of laziness, an automatic failure or a reason to demand more runtime. Avoid
exhaustive simplification experiments; pursue specific material readability or
behavior problems. Do not remove deliberate sound-shaping algorithms merely
because simpler code looks preferable.

## Decide and record

- **verified_complete:** required outcomes have specific artifact/check evidence,
  limitations are consistent with the assignment, and no findings remain.
- **incomplete:** concrete missing deliverables, meaningful failed checks,
  material readability problems or unsupported required claims remain. When the
  worker is idle, send one actionable continuation tied to those findings.
- **review_pending:** the audit has not established enough evidence. State what
  remains unverified; do not restart a worker merely because review is unfinished.

Record the reviewed thread/run, full current commit, relevant worktree changes,
assignment/skill hashes, evidence paths, findings and corrective-message ID.
Verified completion requires every area above, at least one successful captured
check at the reviewed current commit, intact check logs, no tracked changes from
that commit, and no unresolved findings. The recorder enforces these mechanical
guards; the reviewer must establish semantic quality.

Keep checks bounded and durable. Handle urgent worker controls and usage-limit
recovery before a deep audit. Do not edit workers' source; use temporary copies
for experiments. Reuse completed reviews for unchanged inputs and revisit only
changed evidence or unresolved findings. Save unfinished review work between wakes.

An explicit operator re-audit or changed review policy is a new assessment even
when worker source is unchanged. Preserve prior verdicts and reuse valid checks
while performing the new assessment. Record workerAssessment and observed
correction timelines. Elapsed time alone establishes neither waste nor impossible
tolerances; missing data stays unknown.
Separate required findings from optional suggestions. A required finding must name
the code/evidence, practical consequence and finite acceptance condition. Cosmetic
preferences alone do not justify a worker continuation. Deduplicate corrections;
after two automatic correction rounds per logical job, report unresolved
findings to the user. Never reset the budget by replacing a worker. Usage-limit retries remain exempt.

Ownership, operator Stop, notifications, recovery, queue mode and usage retries
follow CONTROLLER.md. Do not change operational policy during an ordinary review.
