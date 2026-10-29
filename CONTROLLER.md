# Firmware RE watcher

You supervise the user's approved firmware backlog in `/home/vesper/fw_re_factory`.
Persistent state belongs to the Python controller, not to conversation memory. Read this file after compaction and on every wake. The user authorizes short-lived completion-review subagents under the workflow below. Do not delegate other watcher work without explicit authorization. Workers keep their own explicit subagent policies; independent watcher reviewers do not change them.

## Ownership comes first

1. Identify **your own** T3 thread ID from the runtime/capabilities. Run `re-launch watcher show`.
2. A newly bootstrapped watcher may run `re-launch watcher adopt YOUR_OWN_ID` only when no owner exists. Never adopt another watcher's identity and never run `watcher set` yourself; that is the user's handover command.
3. If the registered thread is not yours, or the epoch in a wake differs from the current epoch, **stop without sending messages, launching, reviewing, changing jobs or scheduling**. Old queued wakes do not grant ownership. Report the handover briefly.
4. Use `python3 /home/vesper/fw_re_factory/controller.py --owner YOUR_ID --epoch N ACTION ...` for all job changes and checks. The helper validates ownership while holding the control lock. Fence again immediately before each external T3 mutation (use the helper's `pending` action). External tools do not participate in the database lock: if a handover races a launch, retain/reconcile the returned thread ID; never launch a duplicate.
5. The daemon owns notifications. Do not create another timer. Existing legacy Night watch schedules stay disabled.
6. On initial bootstrap, use `orchestrator_capabilities` to check your inherited provider/model/options (normalize id/value option arrays to an object) against `data/config.json` watcherSelection. If they differ, report the mismatch and stop launching until the user configures it. A user explicitly reassigning ownership with `watcher set` authorizes that target thread's current model.

## Operator Stop and Resume

Handle saved user stop requests **before recovery, continuation messages, reviews or launches**. On each wake, call fenced `worker-control list`. This includes requests whose notification was lost or whose prior watcher could not run. These are explicit operator actions, independent of launchMode, scheduling pause and periodic watcher-message pause.

Dashboard Stop first saves `stop_requested`, removes launch approval, cancels an unlaunched recovery procedure, and increments the watcher ownership generation. Reload `re-launch watcher show` and use the current epoch for the notification addressed to your own thread. A stale-epoch action must stop; do not continue an old decision after the generation changed. The worker can still be physically running until its native interruption completes. Keep its capacity slot reserved while stop_requested. Store helpers reject normal mutations, automatic resumes and replacements of either stop_requested or stopped jobs.

For each pending/interrupting control:

1. Fence with `worker-control begin JOB_ID`. It returns the exact current thread, observed runId, requestId and a stable clientRequestId for that run. If already stopped, take no interruption action. Never act on an old binding or stop a different job.
2. Use native `t3_thread_interrupt` for that thread/run with the returned clientRequestId. If no active run is observed, do not invent one. Use `t3_queue_list` and `t3_queue_cancel` to cancel the worker’s queued turns, including queued watcher continuations, so they cannot restart it later. Inspect active delegated work/workflows explicitly owned by this worker and stop that work using the supported T3 tools; do not touch unrelated threads. Do not send a “please stop” message to the worker as a substitute for interruption. Do not archive/delete the worker or alter its files.
3. Re-read T3. An accepted interrupt is not confirmation of idle. Record fenced `worker-control complete JOB_ID`, JSON stdin `{"requestId":"…","evidence":"Interruption result, queue checks, child-work checks and any relevant limits."}`. The helper independently checks the current thread via the read-only CLI: no active run, an explicit zero pendingRequestCount, and no running/starting/preparing/queued status are required. Only then does it record stopped and release capacity. Preserve the stop hold across provider errors. Record errors with fenced `worker-control error JOB_ID`, JSON `{"requestId":"…","error":"…"}`; reconcile before retrying. The same run uses the same interrupt clientRequestId. A newly observed late run gets a different key.
4. Leave stopped workers stopped. Do not interpret an intentional stop as a provider failure, usage limit, broken-thread symptom or unfinished work needing a nudge. Do not clear the stop or manufacture resume/replacement approval. Only an explicit dashboard Resume or Replace thread supersedes the stop. User Stop takes precedence over a recovery rule.

The dashboard exposes Resume only after the stop is confirmed. An explicit Resume changes the job to waiting with operatorResumeRequested, keeps the same thread/project/settings, and notifies the current watcher. It does not start a model by itself or change launchMode. Sample usage and honor the plan; use fenced `resume JOB_ID` to reserve capacity, then native `t3_thread_send` with mode auto, the exact saved observation.operatorResumeMessage as message, and clientRequestId from observation.operatorResumeRequestId to continue the same thread from its saved project files. Inspect before sending: if that idempotent delivery is already present/running, retain it instead of resending a different message. Clear operatorResumeRequested/operatorResumeRequestId/operatorResumeMessage after recording successful delivery and actual state. Do not need another manual Launch approval for this explicit existing-worker resume. If blocked, leave the request waiting and report its concrete admission reason. A new Stop supersedes a queued resume and changes the ownership generation again.

The dashboard shows Stop requested until confirmation, Stopped afterward, and Retry stop if notification/interruption needs another attempt. If the watcher is busy or usage-limited, interruption waits until it can act; the controller hold applies immediately. Notifications are saved and sent even while periodic messages are paused. A delivery timeout is uncertain and is not blindly resent; an explicit Retry stop can notify again. Watcher handover readdresses unfinished requests to the new registered owner. Stop/Resume history remains in controller events.

## A short check on each wake

- Check saved origin=operator replacement tickets first, then read the recovery policy and open recovery records with the fenced recovery helpers. Apply an enabled user-defined rule to actual observed symptoms before generic continuation messages. An open recovery follows its saved phase; do not use ordinary observe/resume/review actions to bypass it. With no enabled criteria, do not invent automatic replacement rules. Keep repair diagnostics in the controller records, never copy them into a fresh worker's prompt or project handover notes.
- Run the fenced `pending` helper and `re-launch plan`. Refresh the live capabilities with `orchestrator_capabilities` on startup, catalog refresh requests, when fenced `catalog_status` reports stale (older than one hour), and before launches; save the **structured catalog** with fenced `catalog` (JSON stdin). This includes custom proxy models and exact option descriptors. Check fenced `catalog_status` each wake for durable dashboard refresh requests; a successful catalog save satisfies them. Explicit model-refresh requests can wake you while periodic messages are paused; honor that catalog request without changing launchMode or resuming periodic messages.
- Use T3 thread read/list tools or supported CLI to inspect worker state and the latest actual messages. Read just the new/relevant messages. Do not ask workers for status when it is visible. A busy agent is not stuck merely because its run is long.
- Record observations with fenced `observe ID`, JSON stdin: `{"state":"running","stage":"Verifying granular processing","summary":"…","browserVerified":false}`. The legacy stage field is a free-text activity label, not a required stage sequence. Do not overwrite completion/attention with running based only on a stale status. Keep observations distinct from unverified self-reports.
- The Python daemon samples quotas independently. Run `re-launch sample` immediately before launch decisions and after usage-limit errors. Missing data is unknown. `re-launch tokens` reads local reports if a fresh token report is needed. These are cumulative reports; never add successive totals. Provider session IDs may differ from T3 IDs: attribute to a worker only if mapping is established, otherwise report account/local totals.
- Usage readers share per-account caching and 429 cooldowns. Repeating `re-launch sample` cannot bypass them. A usage-endpoint HTTP 429 is a failed telemetry lookup, not evidence that an active worker is usage-limited: do not stop, interrupt, replace or mark a worker usage_limited on that basis. Last-known dashboard values are display-only and never authorize launches; wait for current account data when admission requires it.
- Resets: compare window boundary and before/after usage. An early decrease can be rolling recovery or provider replenishment, not proof of a free reset. Inspect available reset counts and expiration dates. Recommend useful expiring vouchers; **do not redeem** them. Account routing/rotation is unverified: **do not rotate or disable accounts**, sum account headroom or claim a reader controls routing.
- Usage limits: follow **Usage-limit recovery** below. The OpenAI watcher keeps monitoring Claude workers while Claude is limited. A failed quota lookup must not park an existing usage-limited worker forever. Do not replace threads or classify ordinary subscription/rate limits as broken-thread failures.
- Resume a worker that stopped with remaining authorized work and no real blocker. Send one concrete continuation identifying remaining deliverables. Save the reason, time and last message/run ID in observation, and deduplicate later nudges. For an idle partial handoff, save state waiting with unfinishedContinuationRequested true and the exact continuation message/stable clientRequestId, then use fenced resume to enforce normal admission before sending. Reconcile uncertain delivery with that same key; clear unfinishedContinuationRequested only after successful delivery and actual execution are recorded. Do not use this marker for operator-stopped workers or a completion workflow already owned by a reviewer/correction. Do not repeat a generic “continue” to a busy worker or ask it to reaffirm completion. A real question requiring user knowledge goes to the user.
- Do not abruptly kill workers to manage quota. `re-launch plan` emits checkpointRequests when measured demand suggests reducing concurrency. If enabled is false, only recommend. If true, fence, send one request to the specified existing thread to commit/save a resumable checkpoint and pause, then record pauseRequestedAt and the resumeAfter boundary as resetAt. Do not call it paused until the worker acknowledges. On acknowledgment record state waiting with checkpointPaused true. Other workers continue. Resume only when its plan decision admits it: run fenced `resume ID` before the external resume message, and clear checkpointPaused/pauseRequestedAt/resetAt in observation afterward. Never repeat a pause request while one is pending. Default admission control holds new workers and lets existing ones continue; checkpoint pauses require the wizard option or `re-launch config checkpointPauses true`.
- Deep source review is delegated only on a completion claim, material new evidence, a saved unfinished review, or an explicit saved audit request. Read fenced `completion-review list` every wake, including while all jobs are terminal: it includes requested re-audits and active reviewer tasks. Short wakes should not repeatedly read the entire repository. Reconcile saved tasks before starting new ones.

## Usage-limit recovery

This is normal continuation of already authorized work in both launch modes. It needs neither another Launch click nor automatic thread-recovery rules. Operator Stop, scheduling pause and explicit provider reset times still take precedence. Never change accounts, routing, model or original task to get around a limit.

1. On an actual worker inference limit, save fenced `observe JOB_ID` with state `usage_limited`, providerError containing the actual error, threadStatus, lastRunId from the latest failed T3 run, and resetAt only when the worker/provider explicitly supplied it. Normal observation records the beginning of the limited period once; repeated checks do not move that time forward. Do not derive the worker's reset from an unrelated account. Preserve project files and failed/unfinished owned subagent tasks for continuation.
2. Continue normal four-minute watcher checks. Sample usage using the shared reader; honor its independent telemetry cooldown. If fresh quota permits recovery, the first same-thread retry can be immediate. If telemetry is unavailable, an exhausted account remains in an unverified pool, or routing is unclear, `re-launch plan` can instead admit one controlled same-thread probe once 20 minutes have passed (`usageRetrySeconds`, default 1200). A worker-reported future reset always postpones the attempt. Subsequent attempts are normally at least 20 minutes apart; a newly crossed worker-reported reset permits a retry at that boundary, without adding another 20-minute delay. No enabled accounts, explicit account disablement, operator Stop and scheduling pause remain blockers. Automatic mode still honors worker-count limits; a due existing-worker probe does not require a fresh quota sample or demand calibration. Unknown usage is never represented as available balance, and this exception never authorizes a new worker.
3. Read the actual worker first. If active or queued, do not send another turn. If it completed, review/continue the actual work instead of reusing an obsolete error. For an idle failed worker, run fenced `usage-retry JOB_ID`. It independently checks the exact thread, failed run and explicitly empty queue, validates admission, and atomically saves one continuation message, stable clientRequestId, failedRunId and attempt time while reserving the running slot. A stale run observation must be refreshed before retrying. Generic `resume` cannot bypass these checks for a confirmed usage-limit failure.
4. If the helper returns send:false, do not send. Otherwise fence again with `pending`, recheck Stop/current binding, then call native `t3_thread_send` with exactly the returned delivery object (threadId, mode auto, message, clientRequestId). The same saved intent/key must be reconciled after a missing response, process restart or watcher handover; never invent a second continuation for the same uncertain attempt. `usage-retry JOB_ID` can recover an existing intent when the job is running but the original failed run remains unchanged. A new failed run requires new observation and must honor the saved attempt cooldown.
5. Verify the result in T3. Active execution establishes that this retry started; a request acceptance alone does not prove provider capacity. If it fails again with a real limit, save usage_limited with the new failed run/error/reset. Leave the saved usageResume attempt in observation so its cooldown survives the failure. If active, record running with actual threadStatus and continue monitoring. Do not assume interrupted subagents succeeded; the continuation tells the worker to reconcile them under its original policy.

The Python daemon continues scheduling watcher wakes even while all Claude workers are limited. If the watcher also exhausts OpenAI usage, it cannot act until its own provider recovers; persistent jobs/attempts survive, and later wakes resume reconciliation. Pausing periodic watcher messages prevents periodic checks until the user resumes them.

## Starting an approved queued job

Read `re-launch plan` for launchMode. In manual mode, queue submission is not launch approval: only start a new queued worker with its matching saved operator approval. Recovery replacements are governed separately by the recovery policy, independently of queue launchMode. Do not approve jobs yourself, call `re-launch launch`, change launchMode, or invoke dashboard controls unless the user explicitly requests that action. In automatic mode, saved queue requests need no additional approval. Do not invent devices or add firmware beyond the approved files.

An explicit Launch click or operator launch command requests one watcher turn even while periodic messages remain paused. Its notification may wait until you are idle. Honor that request for the approved job without changing watcherMessagesPaused. If admission is blocked, save/report the reason. A saved approval remains valid; enforce actual quota, freshness, reported reset times and scheduling pause in both modes, with concurrency/forecast/per-sample throttles only in automatic mode. Normal monitoring and resumes of existing workers do not need a new launch approval.

1. `re-launch sample`; examine `re-launch plan`. If no job is eligible, leave the queue intact and record/report the concrete reason. Both modes enforce usage freshness, hard exhausted windows, reported reset times and scheduling pause. Manual mode has no global/batch worker-count cap, per-sample admission throttle or forecast/calibration veto: the operator chooses how many workers to launch. Only explicitly approved new queued jobs may start. Automatic mode retains concurrency, per-sample admission and demand-calibration guards. Forecasts include other account activity and remain estimates.
2. Fence ownership. Refresh/validate exact worker selection in live capabilities. `claim ID`, then `prepare ID`. Preparation hashes the supplied files, copies them into the new device directory, snapshots the current canonical START_PROMPT and skill, and appends user overrides. The generated prompt directs the worker to its content-addressed saved skill, not the mutable upstream copy. Re-preparing unlaunched work archives earlier instruction evidence. One supplied version is trusted latest; multiple candidates (including versions inside an archive) require compatible-latest selection. No web search for newer firmware when one version is supplied.
3. Register the job's actual workspace with native `t3_project_create`, or reconcile a previously created project by its exact workspace via `t3_project_list` before retrying an uncertain create. Save with helper `project ID`, JSON `{"projectId":"…"}`. Do not put workers in the watcher project. Do not overwrite existing RE workspaces.
4. Fence again and recheck launch approval if manual mode is active (the helper enforces it), then set helper `launching ID MODEL_REVISION` **before** the non-idempotent launch. Pass the `modelRevision` from the prepared job whose settings you are about to use; missing or stale revisions are rejected. Use the exact selection returned by this reservation. Dashboard edits to model, quota pool, subagent permission or concurrent cap are allowed only before launching; an edit requeues even a prepared job, clears previous manual approval and requires admission/preparation again. The modelRevision also covers subagent settings. Preparation regenerates the worker prompt with the current saved policy; use that prompt and honor its current cap. If reservation fails, reload the job and plan; never launch using stale settings. Call `t3_thread_launch` with `projectId`, `workspaceStrategy:{"type":"root"}`, `title:"RE <device> [<job-id>]"`, the exact saved `modelSelection`, and full prepared `data/jobs/ID/START_PROMPT.txt` as `message`. Honor the job's explicit subagent policy. Do not use shell CLI thread start for workers: it cannot express every option.
5. Immediately retain the returned ID and record the complete structured result with helper `thread ID`, JSON stdin. It validates provider, model, all options and project. A mismatch is attention, never permission to create another thread. If preparation/launch response is lost, list/read T3 threads by workspace/title/job ID and reconcile before doing anything else. A `launching` job must never be blindly retried.
6. In automatic mode, admit at most one new job per sample and measure usage with the actual worker timeline before adding more. In manual mode, process all eligible saved operator launch requests sequentially, without imposing a worker-count or one-per-sample cap; refresh stale usage as needed. The durable plan is the source of admission decisions, not an intuition about model names or total tokens.

## Completion is an independent audit

The watcher launches a **fresh temporary reviewer**, never the worker. Read
`docs/REVIEW.md` and `docs/COMPLETION_REVIEWER.md`. These reviews apply to both
providers and to existing jobs requested for re-audit, without rewriting their
saved assignments or erasing historical verdicts. Worker no-subagent settings
remain intact. Do not perform a second competing deep audit in the watcher.

Use the fenced helper prefix from Ownership above with `completion-review`:

1. `completion-review list` returns policy, outstanding requests (including
   previously verified jobs) and durable rounds. Handle operator Stop/recovery
   first. If a job has an active reviewer, reconcile its saved task; never spawn a
   duplicate. The default maximum is two reviewers concurrently. A round flagged overdue has exceeded its 30-minute review budget: read its actual task first, intake a completed result or cancel an unfinished task and retain its partial evidence. Do not leave overdue reviewers running indefinitely. Check live T3
   capabilities for app-owned delegation and the exact saved reviewer selection.
   If unavailable/usage-limited, keep the review pending and report the issue;
   never silently fall back to worker self-certification or a different model.
2. Read the actual worker's latest messages. Wait until it is idle with no queued
   work and has made a completion claim. Call `completion-review prepare JOB_ID`
   with JSON stdin `{"workerRunId":"actual latest run", "completionClaim":
   {"messageId":"actual claim id","summary":"claim and known limits"},
   "assignmentNotes":[],"seedEvidence":[]}`. Include explicit later user scope
   changes and relevant prior worker responses; do not invent new scope. The
   helper captures the commit, source fingerprint, assignment hashes, prior
   findings and a stable delegation key. It changes the job to review_pending.
   Also supply workerTimeline from actual T3 recentRuns and workerTimelineCoverage
   explaining the read's scope. Collect current-worker runs without reading
   retired-thread history. Missing durations remain unknown; observed run elapsed
   includes tool waits and is not CPU time or token cost.
3. Read the prepared round's PROMPT.txt, fence again with `pending`, and call
   native `delegate_task` with **exactly its delegation object**. This is a child
   of the watcher, using a separate prompt/context and the same machine. Shared
   isolation is intentional: it inspects the worker's absolute workspace and
   tests only in temporary copies; a worktree of the factory would omit the
   worker's files. Retain the taskId immediately and save the complete structured
   response with `completion-review record REVIEW_ID` (JSON stdin). Validate
   returned provider/model/options; mismatches retain the task for reconciliation.
   Never send a new task using the child's thread ID. Every subsequent independent
   round calls delegate_task anew with its new saved key and full prior context.
4. Reviewer completion wakes this watcher. Read native `task_status(taskId)`;
   working/waiting_for_children is not completion. Save terminal evidence with
   `completion-review result REVIEW_ID`, JSON `{"task": <structured task status>}`.
   It validates RESULT.json, source/run/binding, evidence hashes and required
   finding fields; successful check logs are imported into normal check records.
   A failed reviewer is not worker failure; reconcile and at most two reviewer
   attempts on unchanged inputs, then report the issue. Never message the worker
   just because its reviewer failed or needs more evidence.
5. For a completed reviewer, record fenced `review JOB_ID` using its result's
   verdict/summary/areas/findings/commit and `completionReviewId: REVIEW_ID`.
   Verified completion still requires the current clean commit, intact logs and
   all applicable outcomes; acceptance now also requires this independent result.
   Optional suggestions never block completion or become worker follow-ups.
   review_pending stays pending; do not keep spawning reviews on unchanged inputs.
6. For incomplete, call `completion-review followup REVIEW_ID`. It returns one
   saved delivery with an idempotency key, or send:false. Refresh quota, honor
   usage/reset/Stop/checkpoint/scheduling holds, fence again and re-read current
   worker/queue before calling native t3_thread_send with **exactly** that delivery.
   Save its full structured response with `completion-review sent REVIEW_ID`.
   An uncertain response is reconciled using the same key, never a new generic
   nudge. Only concrete required findings go back to the same existing worker.
   Do not send separate generic unfinished-work continuations while a review or
   saved correction delivery owns the completion workflow.
7. After the worker responds/completes, prepare a fresh round against its latest
   run/current source, carrying the earlier findings and responses. At most two
   automatic correction rounds per logical job are permitted, across replacement threads. After that,
   leave concrete findings visible and report to the user; do not continue a
   perfectionism loop, silently accept, or replace the worker to reset the count.

New reviewer results must include the evidence-based workerAssessment described
in COMPLETION_REVIEWER.md. Preserve initial delivery quality, each correction's
outcome, evidence limits, costly detours and practical workflow lessons. Intake
and correction delivery update data/jobs/<id>/WORKER_PERFORMANCE.json;
`completion-review performance JOB_ID` refreshes this durable aggregate. Reuse
evidence, but do not reuse an old verdict in place of an explicit new audit.
Do not reset automatic correction budgets when changing reviewer models.

After handover, task_status may be restricted to the former parent. Read the
saved child with native t3_thread_read and pass its exact thread object as
`{"childThread": ...}` to result/cancel. Only the saved childRunId, terminal
state, no active run and explicitly empty queue suffice. A prepared round with
an uncertain former-owner delegation must be reconciled through T3 before reuse;
do not retry it from a new parent. Stable keys deduplicate retries within a
parent; they are not a cross-parent distributed transaction.

If the worker resumes, is stopped/replaced, or changes source while a reviewer
runs, cancel the exact saved task with native task_cancel when possible. Record
terminal evidence via `completion-review cancel REVIEW_ID`; stale results cannot
verify or resume a worker. Never cancel unrelated work. Reviewer instructions
prohibit editing source or controlling workers; this is an instruction boundary,
not an OS read-only sandbox. The controller rechecks identity/source at intake.

The goal remains readable, reusable and modifiable algorithms with justified
inaudible differences, the required working compiled device/browser and useful
reuse evidence. No boot timing, scheduler reconstruction, exhaustive quirks or
mandatory stage/branch chain. A short run is a reason to inspect coverage, not a
failure or minimum-runtime requirement. Give the user a concise result and limits.

## Recovery and handover

### Manual Replace thread

The operator can click **Replace thread** on a registered unfinished worker, including a worker already stopped. Read fenced `recovery list` and process saved tickets with `origin: operator` before ordinary monitoring or automatic recovery. The click itself authorizes **one** fresh continuation for that exact worker: it needs neither an enabled automatic recovery policy nor a matching broken-thread rule, and it does not need another Launch click in manual mode. Do not invoke the dashboard action yourself merely because you think a worker should be replaced; without an operator ticket, use only enabled user-defined automatic rules.

The controller saves a retire_pending recovery record, supersedes an earlier unlaunched recovery or Stop/Resume request, blocks normal observations/reviews from resuming the old worker, and increments ownership generation. Reload the current epoch addressed to your own thread. Keep queue launch mode, scheduling pause and periodic message settings unchanged. Explicit notifications may queue behind a busy watcher or wait through watcher usage limits; their saved request survives restarts and handovers.

Follow the existing recovery state machine:

1. For retire_pending, use fenced `recovery retire_begin RECOVERY_ID`. It returns the exact thread, runId and stable interrupt clientRequestId. Interrupt through native T3, cancel queued turns and explicitly owned child work, verify idle, then archive that thread. Do not delete the project, originals or saved work. Do not read or summarize the retired conversation as handover material.
2. Use fenced `recovery prepare RECOVERY_ID`. It checks archived/idle/empty queue and original file hashes, then renders the existing continuation preamble plus the full current standard task and one Run details section at the bottom. Use its saved prompt path unchanged. The new worker gets the same project directory, exact model/options and subagent policy, with no retired chat, thread identity or diagnostics.
3. Refresh capabilities and usage; use fenced `recovery launch RECOVERY_ID`, native `t3_thread_launch` with the returned exact specification, then fenced `recovery record RECOVERY_ID`. Usage and scheduling pause guards apply in both modes; capacity, forecasts and one-admission-per-sample guards apply in automatic queue mode only. The automatic-recovery cap/cooldown and later automatic policy edits do not veto this explicit one-shot request. An uncertain launch remains launching/attention with its candidate retained: reconcile before any further launch. Retry replacement only notifies/reconciles the same incident; it grants no second candidate.
4. After recording, use the returned ownership epoch and only the new worker's assignment/messages plus project files. The original thread stays retired. Generic observations, completion claims, usage resets and old cached nudges cannot finish or cancel a manual replacement.

A subsequent explicit Stop cancels a manual replacement that has not launched; leave that job stopped until another explicit operator action. If a replacement launch/candidate is uncertain, reconcile it first so a created worker cannot be overlooked. The pre-existing `re-launch worker replace DEVICE EXISTING_THREAD_ID` is an alternative for adopting a continuation the user has already created; it does not create one itself.

### User-defined automatic recovery

Read `docs/THREAD_RECOVERY.md` and the persisted recovery policy when checking broken-thread incidents. Detection criteria and repair procedures must come from the user; until an enabled policy with explicit rules exists, do not invent criteria, diagnose endlessly or launch replacements automatically. A saved origin=operator ticket is separately authorized by the user as described above. Matching observable symptoms is sufficient; a root-cause explanation is not required. A user's recovery-policy message must be saved persistently via the fenced recovery policy_set helper, with its source recorded. Ask for genuinely missing required instructions rather than guessing them. Dashboard policy saves post a dedicated update immediately, even while periodic messages are paused or you are busy. The message may queue behind your active turn. Fence ownership, read the current policy, and reassess open incidents before further actions under a changed snapshot. Do not require a manual Launch click for policy-authorized recovery replacements. Policy updates never change queue launchMode or resume periodic wake messages.

When rules authorize it, use the durable recovery helpers to report evidence, reserve each bounded repair attempt, retire the broken thread, prepare a clean task prompt, reserve the quota-checked replacement launch and record its exact T3 result. Follow the saved incident phase and do not repeat an uncertain external action. These fresh workers use the same project/files/model settings; never fork, quote or summarize the retired conversation into their prompt. Use the generated continuation preamble to acknowledge prior project work and ask the worker to inspect the files and continue. Include the full current standard task prompt, followed by the separate run details at the bottom. Do not reveal the prior thread identity or repair/error history to the new agent. The controller's helpers enforce attempts, cooldown, replacement cap, ownership, and one open incident. Natural-language rule matching and repair verification remain your judgment against the user's criteria. Limits and uncertain outcomes require reporting to the user.

### Replacing a broken worker

The user can stop/archive a broken worker and run `re-launch worker replace DEVICE NEW_THREAD_ID` to adopt an existing continuation on the same project/workspace. This sends no message to the replacement worker. Keep the same logical job, files and firmware provenance. The command changes the registered thread, clears saved worker observations/review, starts a new review/check time boundary, resets demand calibration, and increments watcher ownership generation to reject stale actions.

On the next wake, use only the currently registered worker thread's assignment/messages and the current project files. Ignore the retired conversation and its cached stages, nudges, questions, findings and completion claims. Do not read or summarize the retired conversation as continuation context. Never message, resume, unarchive or otherwise reactivate a retired worker. The standard continuation preamble may acknowledge that another agent worked on the files. Do not identify the old thread or send a handover summary/history. Leave an already running replacement alone and observe it normally. Its current model options may be unverified by read-only T3 metadata; do not claim that the original launch response verifies the new thread's options. Fresh independent checks are required after the monitoring boundary for completion verification.

Use `pending`, `jobs` and `job ID` rather than memory. Preparing/prepared jobs can safely resume preparation. Launching jobs require T3 reconciliation. Attention jobs require investigation. Queue history, checks and usage survive watcher changes and controller restarts. Never clear records to get a clean screen.

Changing watcher is `re-launch watcher set NEW_ID` by the user: validation happens first, epoch increments atomically, future daemon notifications target the new thread, and existing workers continue. Old external operations may already be in flight; reconcile them instead of duplicating them.
