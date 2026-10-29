# fw_re_factory: worker prompts, watcher prompts and the workflow

This guide includes manual launch mode. It explains what each agent receives and how the Python controller works with the watcher. It reflects the implementation as of 26 October 2026. Examples are documentation, not commands to launch or resume a worker.

This factory depends on [benekreng/bens_skills](https://github.com/benekreng/bens_skills) for the reusable worker task and the complete `firmware-rewrite` skill. See the [README dependency section](../README.md#skill-dependency) for required files and local paths.

## The parts and their responsibilities

- **Launcher (`re-launch`, `cli.py`):** asks which firmware to process and which model/settings, directory, subagent policy and scheduling options to use. It saves the approved request and reuses the registered watcher. Adding more firmware does not require another watcher.
- **Python controller (`engine.py`, `controller.py`, `recovery.py`):** persists the queue and observations, samples usage, estimates depletion, checks admission and ownership, prepares prompts, records checks, and sends watcher wake messages. The daemon runs without an agent conversation being active.
- **Watcher agent:** reads the controller's instructions and saved state, uses T3 to launch workers, examines their actual output, resumes unfinished work and independently audits completion. The agent performs the source and behavior review; Python enforces the recorded state transitions and checks.
- **Worker agents:** carry out the firmware-rewrite task in their own device directories using their saved model/settings and subagent policy.
- **Dashboard:** displays controller state, provides a manual/automatic mode selector, lets the operator approve individual queued jobs with Launch, and edits model/settings and subagent permission/limits for unlaunched jobs, and provides Stop/Resume and manual Replace thread for registered workers. It also edits the independent recovery policy and immediately posts saved updates to the watcher. A compact host line shows aggregate CPU and RAM usage. It displays jobs, observations, usage history, forecasts, reviews and recovery records. It refreshes every 15 seconds. The configured listener is http://100.91.196.107:8787 on the Tailscale adapter.

The database is the persistent record. The watcher can be replaced without recreating the queue or losing usage and review history.

## Worker prompt

A normal launch combines two pieces: the reusable standard task prompt, followed by the generated run details. The full skill is referenced by path; its contents are not pasted into the message. Preparation saves a content-addressed assigned skill snapshot and prompt hashes for provenance; the generated message points to that saved skill.

The standard task comes from `/home/vesper/RE/bens_skills/firmware-rewrite/START_PROMPT.md`. The sections below reproduce that current template, split up for explanation. The live file and renderer remain the sources used for future launches; this guide is a readable explanation, not another prompt template.

### 1. Standard task, resources, working rules and handoff

The standard task is:

```text
Recover the device from the firmware in this folder as a working compiled C implementation, with its important processing algorithms in a readable, reusable and modifiable library. Follow the firmware-rewrite skill's goals and deliverables; choose your own method.

Read /home/vesper/RE/bens_skills/firmware-rewrite/SKILL.md. Its source file is https://github.com/benekreng/bens_skills/blob/main/firmware-rewrite/SKILL.md. If the local file is missing, create its parent directory and download only that file using the authenticated GitHub CLI: `gh api --method GET 'repos/benekreng/bens_skills/contents/firmware-rewrite/SKILL.md?ref=main' -H 'Accept: application/vnd.github.raw+json'`. Save the successful response to the local path, then read it. Do not clone the skills repository. If run details name a saved skill snapshot, use that snapshot instead; do not substitute a newer download if it is unavailable.

Device: identify it from this directory unless the run details specify it.

Start by identifying the supplied firmware and documentation. Work here; this directory becomes the project repository. The library on branch `main` is the main deliverable, with the complete device playable in the browser as compiled C using that library. Surrounding functionality may use straightforward recovered C or existing components through adapters; it does not need the same readable rewrite. The rewritten device must run without executing original firmware in a CPU emulator or instruction interpreter. Emulation remains useful for investigation, verification and original/library A/B.

Setup and resources:
- Preserve supplied originals, existing user work and unrelated changes; work on copies. Never push or upload this project or firmware, or send them to online analysis services. Only the device-neutral shared-resource contributions described below may be pushed.
- Public research is allowed. Save useful sources under research/sources/. You may install missing tools; record significant installations in STATUS.md.
- Before building infrastructure, look for suitable tools in the shared resources, then elsewhere. Build only what the processing or its verification needs.
- Shared resources: https://github.com/benekreng/fw_re holds tested components from earlier runs. Read its README. Clone it into resources/ if needed, add resources/ to .gitignore, and use a branch run/<device>. Never change its main branch or another run's branch.
- Put reusable RE infrastructure and fixes in that shared copy and use them from this project. Each contribution needs a short README covering its interface, dependencies and limits, plus a test using synthetic or public inputs without this device's firmware. Include no vendor firmware bytes, decompiled or rewritten vendor code, device assets or firmware-dependent fixtures. Commit and push only those device-neutral contributions on your run branch; record their commit IDs. Keep device-specific code and reuse/ in this local project. Contributing tools is not a reason to expand the assignment.

Work autonomously toward the skill's deliverables without routine approval requests. Keep STATUS.md concise, note the assigned skill path there, and commit useful increments. After compaction, reread the assigned skill and current project status. Document genuine blockers and unresolved issues plainly, and advance other useful work where possible.

Give a final report covering the recovered algorithms, verification and limitations, how to build/use/modify the library, deliberate differences and safety fixes, reuse/, the browser address and remaining gaps. Leave the browser server running. Do not invent mandatory stages, a three-version comparison or an exhaustive quirk-classification prerequisite.
```

The skill defines the outcomes: actual algorithms and their operating behavior,
a readable/reusable/modifiable C library, justified perceptual fidelity, a
device-like browser using that library with original/library A/B, and the reuse
catalogue. Small numerical differences expected to be inaudible to human
listeners during normal use are acceptable. Bit-exact output is not the goal.

The worker chooses its method. An exact intermediate implementation, boundary
captures and original execution can help; a fixed stage chain, three separate
versions and exhaustive quirk classification are not mandatory. The complete
device behavior must run as compiled C, with clean algorithms in the library.
Surrounding code may be straightforward recovered C or suitable existing code;
it does not need the same readable rewrite. Host adapters replace hardware
dependencies. Reference emulation is allowed, but the rewritten device must not
execute original machine code in a CPU emulator or instruction interpreter.

Preparation snapshots the skill under
`data/jobs/ID/skills/<sha256>.md` and substitutes that assigned path in both the
task and run details. The worker rereads that snapshot after compaction; it must
not silently download a newer skill over a missing assigned snapshot. Source
paths and hashes remain in provenance. Only standalone use of the standard task,
without assigned run details, uses the single-file upstream download fallback.

The two dependencies serve different purposes: `bens_skills` supplies instruction
files; `fw_re` supplies reusable infrastructure. Only device-neutral shared
contributions with synthetic/public tests may be pushed. Vendor-derived
algorithms and the device's `reuse/` catalogue stay in the local device project.

### 2. Generated run details

[`render_worker_prompt()` in controller.py](../controller.py) appends the actual device assignment. This is configuration for the task above, not another RE stage. A Bohm example with subagents disabled is:

```text
Run details (these settings override the defaults above):
Device: Ohmforce Bohm
Workspace: /home/vesper/RE/ohmforce_bohm
Skill path: /home/vesper/fw_re_factory/data/jobs/<job-id>/skills/<sha256>.md
Immutable selected originals are under originals/; LAUNCH_REQUEST.json records their hashes, roles and source URLs. Inventory these paths before analysis.
If one firmware candidate was selected, treat its version as the latest; do not search for newer firmware. If that candidate is an archive containing multiple firmware versions, inspect those candidates and use the newest appropriate version. If multiple firmware candidates were selected, identify and use the newest appropriate supplied version for this device/hardware revision. Record the selection evidence; do not choose a bootloader or incompatible hardware variant as an application update. Continue the skill's device/manual research in either case.
Target host: none; use the skill defaults
Serve the browser app on port 19100 and leave it running. Give a working T3 preview or clear access instructions for the server machine.
For every completion claim, cite the relevant artifact paths, rerunnable checks and remaining limitations.
User subagent policy: NO subagents or delegated agents. Perform all work and integration checks yourself.
Selected files:
- firmware: originals/ohm_force/bohm-eurorack-60cca68645b71ceba7e69426c3f8b06e400d0657.zip
```

These details supply the directory, selected inputs, host, browser port and policy. They appear once, at the bottom. The worker prompt adds no separate internal job-ID field or instructions about being watched; the assigned snapshot's filesystem path naturally contains the job ID. Internal IDs remain available to the controller and watcher for tracking.

The version instruction is conditional: trust a single supplied candidate as latest, inspect multiple versions inside an archive, and select the newest compatible supplied application when multiple candidates exist. Device/manual research continues in all cases.

Subagent policy has three forms:

- **None:** do all work and integration checks in the worker. This is ordinary skill compliance; delegation is not mandatory.
- **Integration only:** optional delegation for integration checks, at most one agent at a time; no delegated RE or implementation.
- **Allowed:** optional delegation within the saved cap, including nested children.

### 3. Model settings are separate from the text

For the requested Bohm profile, T3 receives:

```json
{
  "instanceId": "codex",
  "model": "gpt-6.1-sol",
  "options": {
    "reasoningEffort": "high",
    "serviceTier": "default"
  }
}
```

Other requests use their own saved selection. Before launching begins, the dashboard can update that selection using T3’s capability descriptors, and change whether subagents are allowed and their concurrent limit (1–8, including nested children), or restrict them to integration checks. Saving preserves the assignment, requeues the job for admission under its selected quota pool, clears previous manual Launch approval, and archives prepared prompt/skill evidence. Running workers and recovery replacements cannot be edited. The launch revision covers model, quota pool and subagent policy/cap together; a subagent-only edit invalidates stale reservations and regenerates the prepared prompt. The editor shows the catalog timestamp and supports a durable watcher-backed refresh request; normal watcher checks refresh catalogs older than one hour. The watcher refreshes the live capability catalog and passes the exact selection to native `t3_thread_launch`, with the device project and explicit root workspace. Writing “use high” in the task text would not configure the model. The returned provider, model, options and project are checked; a mismatch requires investigation, not another blind launch.

### 4. Replacement worker prompt

Automatic replacement uses the same current standard task, including approved prompt edits, with this preamble:

```text
Another agent has already worked on this project and was assigned the task prompt below. Its work is in this directory. Continue the work from the existing project files. Inspect and verify what is already there, then complete the remaining work. The run details at the bottom apply to this task.

--- BEGIN TASK PROMPT ---
[Complete standard task prompt: sections 1–4 above]
--- END TASK PROMPT ---

Run details (these settings override the defaults above):
[Saved assignment: section 5 above]
```

The new conversation acknowledges earlier project work and continues from the files. It receives no old conversation, summary, thread identity, provider errors or repair transcript. It must inspect and verify the existing work. The renderer always starts from the standard task, so multiple replacements do not accumulate nested preambles. “Same prompt” here means the current standard task with approved edits, rather than restoring an obsolete historical template.

The dashboard’s **Replace thread** explicitly requests this continuation workflow without automatic rules or another Launch click; it retires the old worker and creates the new thread in the same project with the saved settings. A manually created continuation can instead be adopted with `re-launch worker replace DEVICE NEW_THREAD_ID`. Adoption does not send it this prompt or any other message; that thread already exists. See [thread recovery](THREAD_RECOVERY.md).

## Operator stops in the dashboard

Stop is a durable controller decision followed by a native T3 action. The dashboard immediately saves stop_requested and blocks worker mutations, automatic resumes and recovery replacements. It invalidates prior watcher decisions and notifies the current watcher, including when periodic messages are paused. A busy or usage-limited watcher handles the interruption when it can run. The interface keeps showing Stop requested until T3 confirms an idle worker and empty queue. The watcher also checks/stops explicitly owned delegated work. A stopped job stays stopped across resets, daemon restarts and watcher handovers; it releases its capacity slot only after confirmation.

Resume is an explicit operator request to continue the same worker thread. It waits for quota admission, with concurrency and forecast throttles only in automatic mode, uses an idempotent native delivery, and does not change manual/automatic launch mode. The saved model/subagent settings and project files remain attached to that job. The worker receives no new system prompt merely because the user clicked Stop. The stopped hold cannot be cleared by a generic watcher observation or completion claim.

## Watcher prompt

The watcher receives a short startup or wake message pointing to [`CONTROLLER.md`](../CONTROLLER.md). Detailed instructions stay in that file so later changes apply on the next read. [`docs/REVIEW.md`](REVIEW.md) supplies the independent audit procedure, and [`docs/THREAD_RECOVERY.md`](THREAD_RECOVERY.md) supplies recovery details.

### 1. Startup message

The bootstrap message is:

```text
Read /home/vesper/fw_re_factory/CONTROLLER.md.
This is a new watcher. Find your T3 thread ID, then run
re-launch watcher adopt SELF_THREAD_ID.

No work before ownership is ready.

Use the saved queue according to its launch mode. Do not request
another confirmation for jobs whose launch is already authorized.
```

Initial creation uses the dedicated watcher project's configured default profile. The watcher verifies inherited provider/model/options against the configured watcher selection. An uncertain creation must be reconciled against existing T3 threads before another is attempted.

### 2. Recurring wake message

A periodic notification has this form:

```text
Firmware watch: periodic check.
Read /home/vesper/fw_re_factory/CONTROLLER.md.
Ownership: thread=<watcher thread ID> epoch=<ownership generation>.
Fence ownership before acting. Run the short check first;
review sources only when needed.
```

Other wake reasons use the same structure. The daemon owns notification timing. The watcher must not create a second timer. Default wake and quota-sample intervals are 240 seconds; token reports default to 900 seconds. These are configurable, separate intervals. Wakes are coalesced, and the daemon skips sending while the watcher is active or already has a queued request. A four-minute cadence does not guarantee provider prompt-cache retention.

### 3. Ownership and handover

The key instructions are:

```text
Persistent state belongs to the Python controller,
not to conversation memory.

Read CONTROLLER.md after compaction and on every wake.

If the registered thread is not yours, or the epoch in a wake
differs from the current epoch, stop without sending messages,
launching, reviewing, changing jobs or scheduling.

The daemon owns notifications. Do not create another timer.
```

The watcher uses fenced helpers with its exact thread ID and ownership generation. “Fenced” means the helper checks those values before accepting an action. The operator changes watcher with `re-launch watcher set NEW_THREAD_ID`; this validates the target, advances the generation, and directs later notifications there. Workers and history stay in place. An external T3 call already in flight still needs reconciliation: the database cannot atomically commit a T3 launch.

Watcher work uses no subagents unless explicitly authorized. Worker subagent policies are independent.

### 4. Short monitoring check

The following summarizes the current instructions:

```text
- Handle operator Stop/Replace requests before enabled automatic recovery rules and open incidents.
- Read pending jobs and the controller's admission plan.
- Inspect worker state and actual new/relevant messages.
- Do not ask workers for status when it is already visible.
- A busy agent is not stuck merely because its run is long.
- Keep observed facts distinct from unverified stage claims.
- Refresh usage before launch decisions and after limit errors.
- Missing or failed data is unknown, never zero usage.
- Resume a stopped worker with remaining authorized work and no
  real blocker. Send concrete remaining deliverables and deduplicate.
- Leave actively running or already queued workers alone.
- Deep review follows a completion claim, material new evidence,
  or an unfinished saved audit.
```

A usage-limited worker keeps its original thread. Save its actual inference error, failed run ID and any stated reset time. An OpenAI watcher continues monitoring while Claude is exhausted. Fresh positive quota can permit the first retry; unavailable telemetry or an exhausted account in an unverified pool cannot park the worker forever: the plan admits a controlled existing-worker probe every 20 minutes (configurable with `usageRetrySeconds`). Never retry before an explicit future reset. Subsequent attempts are normally at least 20 minutes apart; a newly crossed worker-reported reset permits retry at that boundary without adding another 20-minute delay. Stop, scheduling pause and automatic-mode worker-count limits still apply. This exception never launches a new queued job or changes accounts/models.

The watcher uses fenced `usage-retry JOB_ID` for an actual usage-limit failure. The helper checks the exact thread, failed run and explicitly empty queue, and saves the continuation message and stable delivery ID atomically with the reserved running slot. The watcher passes the returned delivery object to native `t3_thread_send` and checks the actual result. Lost acknowledgements, restarts and watcher changes reconcile the same saved intent instead of inventing another message. Another failed run is recorded as usage_limited again and retains the attempt cooldown. Its continuation tells the worker to reconcile interrupted owned subagent work under the original policy. A usage error does not authorize replacing the conversation.

Watcher wakes come from the Python daemon, so limited Claude workers cannot stop its schedule. If OpenAI also becomes unavailable, saved jobs and retry intents survive until the watcher can run again. Pausing periodic watcher messages pauses these periodic checks too. Usage-lookup HTTP 429 errors remain distinct from model-request limits.

Resets are inferred from the usage timeline and window boundaries. An early decrease may be provider replenishment or rolling recovery; it is not definitive proof of a free reset. Available vouchers and expiry generate recommendations. Account rotation and voucher redemption are not implemented.

Optional checkpoint pauses ask a worker to save/commit and acknowledge a pause when measured demand suggests fewer workers. They require opt-in; the default holds additions and lets existing workers continue. Resumes go through admission checks.

### 5. Starting queued workers

```text
In manual mode, queue submission does not authorize launch; wait
for the operator approval. In automatic mode, a saved queue request
needs no additional approval.
Do not invent devices or add firmware beyond the approved files.

- Refresh usage and examine admission decisions.
- Validate the exact model/settings against live capabilities.
- Claim and prepare the job; preserve/hash selected originals.
- Register or reconcile the actual device project/workspace.
- Reserve with `launching ID MODEL_REVISION` using the revision from the prepared job. A stale revision fails; reload and repeat admission/preparation instead of launching stale settings. Use the exact model selection returned by the reservation.
- Launch with exact saved modelSelection, root workspace and
  the complete prepared START_PROMPT as the message.
- Retain and validate the returned thread ID and launch result.
- Reconcile uncertain launches before retrying.
- Manual mode has no worker-count, forecast/calibration or per-sample admission cap; start only operator-approved queued jobs. Actual usage limits, freshness and scheduling pause still apply.
- In automatic mode, admit at most one new job per quota sample and honor concurrency and forecast guards.
```

The prepared prompt, skill snapshot and provenance live under `data/jobs/<job-id>/`. Historical snapshots remain unchanged when the canonical task template is edited. New preparation uses the current template. Re-preparing unlaunched work archives earlier prompt/skill/provenance files under `instruction-history/`; content-addressed assigned skills remain intact. A launch reservation rejects stale or modified instructions and requires preparation again. Once a launch is reserved, reconcile it without rewriting its saved prompt. Replacements use the same safeguards and save their own prompt artifacts under `recoveries/<recovery-id>/`.

### 6. Independent completion audit

The watcher follows [REVIEW.md](REVIEW.md), under policy
`algorithm-library-v2`. It checks outcomes, not completion of a stage sequence:

- `assignment`: actual brief, saved skill, approved scope changes, original
  integrity and version choice.
- `algorithms`: recovered processing and relevant controls, state and
  coordination; documented coverage and gaps.
- `library`: clear, reusable and modifiable code, portable interfaces,
  caller-controlled configuration/memory, independent instances and examples.
- `device`: complete device functionality in compiled C, build/run evidence
  without original machine-code emulation, and suitable host adapters. The
  surrounding implementation need not meet the library's readability standard.
- `fidelity`: independent assembled-processing evidence, justified perceptual
  tolerances, transient/drift/randomness checks and honest limitations.
- `browser`: device-like panel and important controls, actual library processing,
  original/library A/B, initialization/audio-path checks and reachable access.
- `reuse`: useful indexed cards, actual components, independent builds and
  meaningful tests.

The agent inspects the actual artifacts and performs bounded independent checks.
A green build, test counts, a listening port or a model's final claim do not
establish completion. Short runs warrant closer coverage inspection, not a
minimum-runtime requirement.

A surrounding compiled C device shell may use recovered code and existing
components without the algorithm library's cleanup standard. A shell that still
executes original machine code in an emulator does not fulfill the compiled-device
deliverable. Original/reference A/B may use emulation. Do not demand separate
exact/faithful branches, all quirk switches, three-version comparisons or physical
hardware/scheduler reconstruction. Historical obligations and applicability follow
the saved assignment and explicit user scope changes; do not silently migrate
running workers. Evidence does not have to use one prescribed recovery technique.

The verdicts are:

```text
verified_complete — every required area has evidence; no findings remain
incomplete        — concrete requirements remain; send actionable findings
review_pending    — the audit has not established enough evidence yet
```

The review JSON uses `areas` with exactly the seven keys above, each containing a
specific evidence string; the recorder stamps `reviewPolicy`. Verified completion
also requires a successful captured check at the reviewed current commit, intact
check logs and no tracked changes from that commit. These guards support review;
they do not prove semantic quality. An unfinished audit alone does not authorize
a corrective nudge.

Old review records and prompt snapshots remain historical evidence. This update
does not automatically reopen finished jobs, change a running worker's assignment,
send scope updates or unpause the watcher.

### 7. Recovery and durable records

```text
Use saved pending/jobs/job records rather than memory.
Preparing/prepared jobs can resume preparation.
Launching jobs require T3 reconciliation.
Attention jobs require investigation.

Queue history, checks and usage survive watcher changes
and controller restarts. Never clear records to get a clean screen.
```

Automatic broken-thread recovery starts disabled. The user supplies the matching conditions, permitted repair steps and verification procedure using [RECOVERY_MESSAGE.txt](RECOVERY_MESSAGE.txt). The watcher applies those rules to symptoms; it does not need a root-cause explanation and must not invent rules.

A matching incident is persisted before repair or launch actions. Bounded repairs may restore the existing thread; otherwise the old worker must be stopped and archived before a fresh top-level conversation is created in the same project with saved settings. The replacement prompt is the continuation form above. The controller saves launch intent before T3 creation, retains uncertain candidate IDs, and enforces caps and cooldown. Adoption clears cached observations/review and requires fresh independent completion checks; quota and operator history remain.

## How a run works from your point of view

1. **Submit work.** Run `re-launch`, select firmware and options, review the concrete request, and submit it. The controller saves the approved queue. The first request can bootstrap the watcher; later requests reuse it.
2. **Choose when to launch.** In manual mode (the default), open the dashboard and click Launch for each chosen job; other jobs stay unapproved. There is no worker-count limit or forecast/per-sample throttle in manual mode. In automatic mode, eligible queued jobs can start without a click, subject to concurrency, per-sample and forecast guards. Both modes preserve actual quota, freshness and scheduling-pause checks. **Wait for admission.** The daemon samples usage and wakes an idle watcher when permitted. The controller's plan explains whether a queued job can start. The watcher consumes that decision and creates the device project and worker through T3.
3. **Let workers run.** Workers do the RE. The watcher reads progress directly and intervenes when an agent stopped with authorized work remaining. Routine checks stay short; full source review waits for relevant evidence.
4. **Adjust concurrency using measurements.** The controller estimates percentage-point depletion per hour, time to exhaustion, and demand by active worker profile. Forecasts inform whether to add another worker. It requires calibration before expanding concurrency, respects hard limits and concurrency caps, and treats missing data as a hold. Different model/settings/subagent mixes need new measurements.
5. **Handle limits and resets.** The watcher retries limited workers in the same conversations after recovery evidence. The default `short-window` policy prioritizes Claude's five-hour window where available, while still blocking hard weekly exhaustion. `weekly-first` and `balanced` are alternatives. Other account activity affects the estimates, so exact depletion at reset is not guaranteed.
6. **Review completion.** A final “done” lets the watcher launch a fresh temporary reviewer using [COMPLETION_REVIEWER.md](COMPLETION_REVIEWER.md), the actual saved assignment, source fingerprint and previous findings/responses. The reviewer writes evidence and a verdict; the watcher sends required corrections to the existing worker. Optional polish does not block acceptance. Two automatic correction rounds are allowed, then unresolved findings go to the user. Pending review stays pending; only independently supported completion is recorded as verified. Tasks and retries are durable; see CONTROLLER.md for the exact helper workflow.
7. **Continue using the same watcher.** Submit more firmware through the launcher. To change the watcher, point ownership at another existing thread. To replace a broken worker, use the dashboard’s explicit Replace thread action, manual adoption of an existing continuation, or a configured automatic recovery rule. Neither requires discarding the logical job or project files.

Local `ccusage` reports supplement subscription percentages with token totals and available cache fields. They are cumulative snapshots and are not summed across polls. Per-worker attribution is used only where the provider-session mapping is established. Token counts are distinct from subscription-limit percentages.

The dashboard displays the controller's latest records. The Python daemon keeps sampling when watcher messages are paused, but agent monitoring, launches and audits need an active watcher turn. If a provider limit prevents the watcher from running, wake attempts cannot make it reason until access returns; the persisted state remains available for the next turn.

## Pause controls

These controls have separate effects:

- `re-launch config watcherMessagesPaused true` suppresses automatic watcher messages, including ordinary forced wakes. An explicit Launch action creates a durable request for one watcher turn even while this flag remains true; busy watchers receive it when idle. If that turn is blocked, Retry launch requests another turn without resuming periodic notifications. Usage sampling and existing workers continue. This is the user's currently requested pause as of this guide's date.
- `re-launch config schedulingPaused true` holds admission for launches/resumes while monitoring continues.
- `re-launch recovery disable` prevents new automatic recovery starts. It does not resume notifications or stop normal workers.

Saving recovery rules in the dashboard posts a dedicated update immediately, even to a busy watcher or while periodic messages are paused. It does not resume periodic watcher messages or change the queue launch mode. When the user chooses to resume automatic messages, the corresponding command is `re-launch config watcherMessagesPaused false`. This guide does not change any setting.

## Where each part comes from

- Standard worker task: `/home/vesper/RE/bens_skills/firmware-rewrite/START_PROMPT.md`.
- Skill source: `/home/vesper/RE/bens_skills/firmware-rewrite/SKILL.md`, copied into the assigned snapshot the worker reads.
- Shared infrastructure: `https://github.com/benekreng/fw_re`, used under the device's `resources/` where needed.
- Worker composition, preparation and review recording: [controller.py](../controller.py).
- Watcher startup/wakes, ownership, usage and admission: [engine.py](../engine.py).
- Watcher instructions: [CONTROLLER.md](../CONTROLLER.md).
- Independent audit: [REVIEW.md](REVIEW.md), adapted from the earlier firmware Night Watch criteria.
- Persistent recovery: [recovery.py](../recovery.py), [THREAD_RECOVERY.md](THREAD_RECOVERY.md) and [RECOVERY_MESSAGE.txt](RECOVERY_MESSAGE.txt).
- Database: `data/launcher.sqlite`; private configuration and recovery policy: `data/config.json` and `data/recovery-policy.json`.

Launch approval is persisted separately from queue submission. Normal monitoring/resumes of existing workers continue according to the watcher pause settings; recovery replacements are authorized by their enabled policy in both manual and automatic queue modes, without another Launch click. See the [README launch-mode section](../README.md#choose-manual-or-automatic-launches) for controls and the CLI equivalent.

The guide reuses and explains these components. It does not replace their live instructions, embed the full skill, or recreate the old Night Watch timer.

Completion-review orchestration is implemented by `completion_review.py`; the watcher owns native T3 delegation and messages. The worker never launches its reviewer. Current-job re-audit requests preserve original prompts and historical reviews. Reviewers currently use Opus 5.5 xhigh, fast mode off, no further delegation. They check whether important algorithms can be safely modified without repeating the RE, including consequential constants/maps and timing assumptions; essential constants need explanation, not elimination. `review_performance.py` aggregates evidence-based worker assessments and observed correction timing into each job's `WORKER_PERFORMANCE.json`. Unknown history is not zero time or failure, and changing reviewer models does not reset correction budgets.
