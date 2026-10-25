# fw_re_factory: worker prompts, watcher prompts and the workflow

This guide includes manual launch mode. It explains what each agent receives and how the Python controller works with the watcher. It reflects the implementation as of 25 October 2026. Examples are documentation, not commands to launch or resume a worker.

This factory depends on [benekreng/bens_skills](https://github.com/benekreng/bens_skills) for the reusable worker task and the complete `firmware-rewrite` skill. See the [README dependency section](../README.md#skill-dependency) for required files and local paths.

## The parts and their responsibilities

- **Launcher (`re-launch`, `cli.py`):** asks which firmware to process and which model/settings, directory, subagent policy and scheduling options to use. It saves the approved request and reuses the registered watcher. Adding more firmware does not require another watcher.
- **Python controller (`engine.py`, `controller.py`, `recovery.py`):** persists the queue and observations, samples usage, estimates depletion, checks admission and ownership, prepares prompts, records checks, and sends watcher wake messages. The daemon runs without an agent conversation being active.
- **Watcher agent:** reads the controller's instructions and saved state, uses T3 to launch workers, examines their actual output, resumes unfinished work and independently audits completion. The agent performs the source and behavior review; Python enforces the recorded state transitions and checks.
- **Worker agents:** carry out the firmware-rewrite task in their own device directories using their saved model/settings and subagent policy.
- **Dashboard:** displays controller state, provides a manual/automatic mode selector, lets the operator approve individual queued jobs with Launch, and edits model/settings and subagent permission/limits for unlaunched jobs. It also edits the independent recovery policy and immediately posts saved updates to the watcher. A compact host line shows aggregate CPU and RAM usage. It displays jobs, observations, usage history, forecasts, reviews and recovery records. It refreshes every 15 seconds. The configured listener is http://100.91.196.107:8787 on the Tailscale adapter.

The database is the persistent record. The watcher can be replaced without recreating the queue or losing usage and review history.

## Worker prompt

A normal launch combines two pieces: the reusable standard task prompt, followed by the generated run details. The full skill is referenced by path; its contents are not pasted into the message. Preparation saves a separate skill snapshot and prompt hashes for provenance.

The standard task comes from `/home/vesper/RE/bens_skills/firmware-rewrite/START_PROMPT.md`. The sections below reproduce that current template, split up for explanation. The live file and renderer remain the sources used for future launches; this guide is a readable explanation, not another prompt template.

### 1. Task and skill

```text
Reverse engineer the firmware in this folder and rewrite it. Follow the firmware-rewrite skill from start to finish, through every stage. Read /home/vesper/RE/bens_skills/firmware-rewrite/SKILL.md. Its source file is https://github.com/benekreng/bens_skills/blob/main/firmware-rewrite/SKILL.md. If the local file is missing, create its parent directory and download only that file using the authenticated GitHub CLI: `gh api --method GET 'repos/benekreng/bens_skills/contents/firmware-rewrite/SKILL.md?ref=main' -H 'Accept: application/vnd.github.raw+json'`. Save the successful response to the local path, then read it. Do not clone the skills repository.

Device: Figure it out from this directory.

What's here: everything you need is in the current directory: the firmware (an update file, a binary or a dump) and whatever documentation I found. Start by listing what's there and identifying each file.

The result: the library on branch `main` is the main deliverable, for people and agents who want to understand how the device works or build its features into their own projects. Like the rest of the project, it stays on this machine: don't copy it anywhere else.
- Target host for `main`: none; use the skill's defaults.
- Quirk review: in the final report; don't stop for it.
```

Only the skill file is fetched if it is missing. The authenticated GitHub command supports the private repository; no clone of `bens_skills` is requested. The worker still reads the full skill from the local path.

### 2. Setup, preservation and reuse

```text
Setup:
- Work in this directory. It becomes the project repository (run git init if there isn't one).
- The files you find here are the originals. Never edit, move or delete them; work on copies.
- Never push or upload this project or the firmware, and never send the firmware to online services. They belong to the vendor. Your branch of the shared resources below is the only thing you push.
- You may search the web and download manuals, release notes and datasheets for stage 0 into research/sources/.
- Before you write a tool from scratch, look for one: in the shared resources below, then on the web (for example an established emulator for the CPU). Build only what nothing covers, and note in STATUS.md what you looked at and why it didn't fit.
- You may install missing tools with the system's package manager or pip. Record what you installed in STATUS.md.
- Shared resources: https://github.com/benekreng/fw_re holds tested components from earlier runs; its README lists them. Clone it into resources/ in this folder, create a branch run/<device> in it, and add resources/ to .gitignore. Build against that copy. Commit your changes there with messages that say what changed and why, and push run/<device> after each commit. Never touch its main branch or other runs' branches.
- Contribute reusable RE infrastructure to resources/: both fixes to existing components and new tools that other firmware projects could use, such as CPU/peripheral models, image parsers, experiment drivers and comparison harnesses. Put the implementation in the shared repository and use it from this project. Each contribution needs a short README explaining its purpose, interface, dependencies and limits, plus a test that runs on synthetic or public inputs without this device's firmware. Include no vendor firmware bytes, decompiled or rewritten vendor code, device assets, or firmware-dependent test fixtures. Keep device-specific code and the device's reuse/ catalogue in this local project. Record contributions and their shared commit IDs in STATUS.md.
```

The two repositories serve different purposes. `bens_skills` supplies instructions, accessed as one file. `fw_re` supplies reusable code, so workers clone it into `resources/` and work on a device-specific shared-resource branch. Device firmware and rewritten vendor code stay in the local device project. Shared tests must work with synthetic or public inputs independently of that firmware.

### 3. How to continue working

```text
How to run:
- Don't ask me questions and don't wait for approval. Include the quirk review in the final report and continue through the remaining stages. The skill fixes the goal.
- At the end of each stage: commit, update STATUS.md, give me a short report in plain words, and start the next stage in the same response. Ending your turn stops the run.
- At the start of each stage and after every context compaction, reread the skill's SKILL.md in full: the ground rules and the current stage. Note its path at the top of STATUS.md so you can find it. Don't work from memory or a summary of it. Before you report a stage done, check each point of its "Done when" line and say in the report how you checked it.
- Stop only if no readable firmware image can be obtained at all. Anything else goes into STATUS.md as an open question, and you carry on.
```

Quirk review belongs in the final report. The old exception telling the worker to stop for that review has been removed. User-selected subagent restrictions in the run details override the skill's default delegation requirements.

### 4. Final report and browser handoff

```text
When the last stage is done, give me a final report:
- what works, and how each version was checked;
- how the three versions compare: size, longest functions, speed, and what each keeps of the original;
- the quirks, sorted into character, edge case and broken in normal use (broken first); the unsafe bugs fixed; where the original disagrees with its documentation;
- what is unknown;
- how to use the library from another project, with the smallest working example;
- what's in reuse/: the components, and the few ideas and patterns most worth knowing;
- where the code is still harder to read or reuse than it should be, and why.
Leave the browser app's server running and give me its address.
```

The browser deliverable must be usable. The watcher separately checks initialization, processing and controls rather than accepting a listening port as completion evidence.

### 5. Generated run details

[`render_worker_prompt()` in controller.py](../controller.py) appends the actual device assignment. This is configuration for the task above, not another RE stage. A Bohm example with subagents disabled is:

```text
Run details (these settings override the defaults above):
Device: Ohmforce Bohm
Workspace: /home/vesper/RE/ohmforce_bohm
Skill path: /home/vesper/RE/bens_skills/firmware-rewrite/SKILL.md
Immutable selected originals are under originals/; LAUNCH_REQUEST.json records their hashes, roles and source URLs. Inventory these paths before analysis.
If one firmware candidate was selected, treat its version as the latest; do not search for newer firmware. If that candidate is an archive containing multiple firmware versions, inspect those candidates and use the newest appropriate version. If multiple firmware candidates were selected, identify and use the newest appropriate supplied version for this device/hardware revision. Record the selection evidence; do not choose a bootloader or incompatible hardware variant as an application update. Continue the skill's device/manual research in either case.
Target host: none; use the skill defaults
Serve the browser app on port 19100 and leave it running. Give a working T3 preview or clear access instructions for the server machine.
For every completion claim, cite the relevant artifact paths, rerunnable checks and remaining limitations.
User subagent policy: NO subagents or delegated agents. Perform all work yourself. This explicitly overrides the skill parallel-agent and fresh-agent integration requirements. Document this exception and perform independent integration checks yourself; do not claim unchanged full skill compliance.
Selected files:
- firmware: originals/ohm_force/bohm-eurorack-60cca68645b71ceba7e69426c3f8b06e400d0657.zip
```

These details supply the directory, selected inputs, host, browser port and policy. They appear once, at the bottom. The worker prompt does not include the controller's internal job ID or instructions about being watched. Internal IDs remain available to the controller and watcher for tracking.

The version instruction is conditional: trust a single supplied candidate as latest, inspect multiple versions inside an archive, and select the newest compatible supplied application when multiple candidates exist. Device/manual research continues in all cases.

Subagent policy has three forms:

- **None:** do all work in the worker; explicitly document the exception to the skill's fresh-agent integration requirement and perform the checks yourself.
- **Integration only:** one fresh integration agent at a time, solely for integration checks; no delegated RE or implementation.
- **Allowed:** use the saved cap, including nested children.

### 6. Model settings are separate from the text

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

### 7. Replacement worker prompt

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

A manually created continuation can instead be adopted with `re-launch worker replace DEVICE NEW_THREAD_ID`. Adoption does not send it this prompt or any other message; that thread already exists. See [thread recovery](THREAD_RECOVERY.md).

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
- Check enabled recovery rules and open incidents first.
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

A usage-limited worker keeps its original thread. Save the actual error and any stated reset time; refresh usage, wait until the stated boundary and actual recovery evidence, reserve admission, then retry at most once per wake. A usage error does not automatically authorize replacing the conversation.

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
- Admit at most one new job per quota sample.
```

The prepared prompt, skill snapshot and provenance live under `data/jobs/<job-id>/`. Historical snapshots remain unchanged when the canonical task template is edited. New preparation uses the current template. Automatic replacements save their own prompt artifacts under the job's `recoveries/<recovery-id>/` directory.

### 6. Independent completion audit

```text
Worker claims, stage checklists, huge test counts, elapsed runtime
and a green build alone do not establish completion.

Runtime under two hours is an extra audit trigger,
not automatic failure or a minimum-runtime requirement.

Inspect the actual assignment, version choice, source/readability,
feature specification and tests, original/exact proof and limits,
readable faithful implementation, portable main, integrations,
and browser initialization and actual audio processing.

Run meaningful independent checks.
A listening port or HTTP 200 alone does not verify the browser.
```

The detailed audit in [REVIEW.md](REVIEW.md) requires the watcher to:

1. Recover the actual assignment and later scope changes.
2. Check each deliverable against artifacts.
3. Inspect readability and reuse directly.
4. Verify proof quality and behavioral coverage, not just totals.
5. Exercise the browser handoff.
6. Give short runs extra scrutiny without demanding wasted time.
7. Decide and act on evidence.

The verdicts are:

```text
verified_complete — every required area has evidence; no findings remain
incomplete        — concrete requirements remain; send actionable findings
review_pending    — the audit has not established enough evidence yet
```

The recorder requires evidence for assignment, original, specification, exact, faithful, main and browser. Verified completion also requires a successful captured check at the reviewed current commit, intact check logs, and no tracked changes from that commit. These guards support the review; they do not automatically establish semantic quality. An unfinished audit alone is not grounds to tell the worker to redo its work.

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
2. **Choose when to launch.** In manual mode (the default), open the dashboard and click Launch for the chosen job; other jobs stay unapproved. In automatic mode, eligible queued jobs can start without a click. Both modes preserve quota and concurrency checks. **Wait for admission.** The daemon samples usage and wakes an idle watcher when permitted. The controller's plan explains whether a queued job can start. The watcher consumes that decision and creates the device project and worker through T3.
3. **Let workers run.** Workers do the RE. The watcher reads progress directly and intervenes when an agent stopped with authorized work remaining. Routine checks stay short; full source review waits for relevant evidence.
4. **Adjust concurrency using measurements.** The controller estimates percentage-point depletion per hour, time to exhaustion, and demand by active worker profile. Forecasts inform whether to add another worker. It requires calibration before expanding concurrency, respects hard limits and concurrency caps, and treats missing data as a hold. Different model/settings/subagent mixes need new measurements.
5. **Handle limits and resets.** The watcher retries limited workers in the same conversations after recovery evidence. The default `short-window` policy prioritizes Claude's five-hour window where available, while still blocking hard weekly exhaustion. `weekly-first` and `balanced` are alternatives. Other account activity affects the estimates, so exact depletion at reset is not guaranteed.
6. **Review completion.** A final “done” starts an independent audit. Concrete findings go back to the worker; pending review stays pending; only supported completion is recorded as verified.
7. **Continue using the same watcher.** Submit more firmware through the launcher. To change the watcher, point ownership at another existing thread. To replace a broken worker, use manual adoption or a configured recovery rule. Neither requires discarding the logical job or project files.

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
- Full firmware workflow: `/home/vesper/RE/bens_skills/firmware-rewrite/SKILL.md`, read by the worker.
- Shared infrastructure: `https://github.com/benekreng/fw_re`, cloned into each device's `resources/`.
- Worker composition, preparation and review recording: [controller.py](../controller.py).
- Watcher startup/wakes, ownership, usage and admission: [engine.py](../engine.py).
- Watcher instructions: [CONTROLLER.md](../CONTROLLER.md).
- Independent audit: [REVIEW.md](REVIEW.md), adapted from the earlier firmware Night Watch criteria.
- Persistent recovery: [recovery.py](../recovery.py), [THREAD_RECOVERY.md](THREAD_RECOVERY.md) and [RECOVERY_MESSAGE.txt](RECOVERY_MESSAGE.txt).
- Database: `data/launcher.sqlite`; private configuration and recovery policy: `data/config.json` and `data/recovery-policy.json`.

Launch approval is persisted separately from queue submission. Normal monitoring/resumes of existing workers continue according to the watcher pause settings; recovery replacements are authorized by their enabled policy in both manual and automatic queue modes, without another Launch click. See the [README launch-mode section](../README.md#choose-manual-or-automatic-launches) for controls and the CLI equivalent.

The guide reuses and explains these components. It does not replace their live instructions, embed the full skill, or recreate the old Night Watch timer.
