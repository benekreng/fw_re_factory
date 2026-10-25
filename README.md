# fw_re_factory

A terminal launcher, durable Python controller, one T3 watcher, and a local dashboard with manual launch controls for firmware RE work.

See [Worker prompts, watcher prompts and the workflow](docs/PROMPTS_AND_WORKFLOW.md) for a section-by-section explanation of both prompts, how the agent and scripts work together, usage scheduling, completion review and replacement workers.

## Skill dependency

This project depends on [benekreng/bens_skills](https://github.com/benekreng/bens_skills), specifically its `firmware-rewrite` workflow:

- `firmware-rewrite/START_PROMPT.md` supplies the reusable worker task.
- `firmware-rewrite/SKILL.md` defines the RE stages, deliverables and completion criteria.

The launcher currently reads these files from `/home/vesper/RE/bens_skills/firmware-rewrite/`. Both must be present when preparing a worker: the launcher renders the prompt and snapshots the skill. The worker reads the skill by its local path; its prompt also includes an authenticated single-file download command if that skill file later needs restoring. Access to this private repository requires GitHub authentication. A full clone of `bens_skills` is not required; the dependency is on these instruction files.

[benekreng/fw_re](https://github.com/benekreng/fw_re) is a separate shared-code dependency used by workers under their device project's `resources/` directory. It does not contain this factory or replace the skill dependency.

The factory lives at `/home/vesper/fw_re_factory`. The installed `re-launch` command and existing `firmware-watch-*` service names remain the operational entry points. `/home/vesper/firmware-re-launcher` is a compatibility symlink for previously saved instruction paths.

## How to use

### Ask an agent to use the CLI

Paste this into an agent chat on the machine where the factory is installed, then fill in the firmware paths and requested settings:

```text
Use the fw_re_factory CLI on this machine.

Project: /home/vesper/fw_re_factory
Command: re-launch

Read README.md and docs/PROMPTS_AND_WORKFLOW.md in that project.
Run `re-launch --help` and the relevant subcommand's `--help`
before using it.

Use `re-launch enqueue REQUEST.json` for noninteractive submissions.
Use the existing watcher and preserve the saved queue/history.
Keep my current launch mode and pause settings unless I explicitly change them.
In manual mode, only add jobs to the queue; do not approve or launch them.

Firmware and requested run settings:
Firmware paths: [Insert paths]
Device names: [Insert names]
Worker model/settings: [Insert provider, model and options]
Worker subagents: [none | integration | capped]
Worker subagent cap: [1–8 for capped; otherwise 0]
```

An agent submitting jobs uses the CLI; an agent acting as the watcher must also read [CONTROLLER.md](CONTROLLER.md) and follow its ownership checks and launch/review procedure. Submitting another job reuses the registered watcher. If watcher messages are paused, submission does not resume them.

### Choose manual or automatic launches

Manual mode is the default. Agents can add jobs with `re-launch enqueue REQUEST.json`; adding a queued job does not approve it to start. Open the [dashboard](http://100.91.196.107:8787), keep **Launch mode: Manual**, and click **Launch** beside the job you want to start. Approval applies to that job only and survives controller restarts. Usage freshness, limits, forecasts, scheduling pause and concurrency checks still apply; an approved job can wait for capacity. Its row shows why it is waiting.

The dashboard mode selector switches between manual and automatic scheduling. Equivalent terminal commands are:

```bash
re-launch config launchMode manual
re-launch config launchMode automatic
re-launch launch DEVICE_OR_JOB_ID  # explicit approval for one job
```

An agent submitting jobs should preserve the mode and must not use `launch` or change to automatic without your explicit instruction. Existing workers still receive normal monitoring, continuation and usage-limit resumes. Recovery replacements follow the separately enabled recovery policy in both modes; they do not require a manual Launch approval. Usage, concurrency, scheduling pause and recovery limits still apply.

Clicking Launch requests one watcher turn even when periodic watcher messages are paused. If the watcher is busy, that notification waits until it is idle. This does not resume periodic messages. If the one-shot turn is blocked by usage, concurrency or provider availability, the approval remains saved; use **Retry launch** to request another turn while periodic messages remain paused. A lost delivery acknowledgment is retained as uncertain rather than automatically resending. The dashboard requires the registered watcher; if none is ready, create/adopt it using the watcher commands first.

Dashboard launch and recovery-policy controls are available to clients permitted to reach its Tailscale listener. Mutation requests require the dashboard's same-origin page and request token. Queue submission and firmware selection remain in the CLI. Model/settings can be edited for unlaunched jobs in the dashboard.

### Choose worker subagents

Subagent policy is already a saved parameter for each device group. For example, tell the agent:

```text
Allow each RE worker to use multiple subagents when useful.
Set worker subagents to capped, with a maximum of 3 concurrent
child agents per worker, including nested children.
```

The corresponding fields inside each `groups` entry of `REQUEST.json` are:

```json
{
  "subagents": "capped",
  "subagentCap": 3
}
```

Available policies are `none` (no delegation), `integration` (one fresh integration agent at a time, solely for integration checks), and `capped` (delegation allowed, with a cap from 1 to 8). For `none` or `integration`, use `subagentCap: 0`; integration's one-agent limit comes from its policy. The interactive wizard offers the same choices.

These parameters apply to the RE workers being launched. They do not authorize the agent operating the CLI or the watcher to delegate its own work. `maxParallel` separately limits concurrent top-level RE workers. The subagent cap is included in each worker's instructions; the factory does not enforce it by counting live T3 child agents. It permits useful delegation up to the cap rather than requiring that many agents to run.

### Run the CLI yourself

```bash
re-launch                         # interactive queue wizard
re-launch watcher show
re-launch watcher set THREAD_ID   # transfer the watch; keep workers and history
re-launch worker replace DEVICE NEW_THREAD_ID # adopt an existing replacement worker
re-launch plan                    # why the next job will start or wait
re-launch config schedulingPaused true  # hold launches/resumes; keep monitoring
re-launch config schedulingPaused false # allow scheduling again
re-launch cancel JOB_ID           # cancel an unstarted queued job
re-launch sample                  # refresh account usage now
re-launch tokens                  # refresh local ccusage totals
re-launch config                  # inspect local configuration
re-launch config policy weekly-first
re-launch config wakeSeconds 240
```

Dashboard: http://100.91.196.107:8787 over this machine's Tailscale adapter. It refreshes every 15 seconds and is reachable from devices on the tailnet permitted by its access rules. The user service binds specifically to that Tailscale IPv4 address. Manual dashboard runs default to loopback; use `re-launch dashboard --host 100.91.196.107` for the Tailscale listener. No remote assets are needed. A compact Factory host line shows aggregate CPU utilization and RAM used/total in GiB. Readings come from the Linux environment running the factory (including its allocated memory when hosted in WSL), not individual workers or remote machines. CPU measures the change between samples; the first reading may say measuring. Missing readings remain unavailable.

The wizard chooses provider, exact model settings, actual quota pool, subagent policy and cap, device names/directories, firmware/reference paths, target host, concurrency, scheduling policy, optional cooperative checkpoint pauses and wake interval. It presents the concrete request before submission. Subsequent submissions reuse the same watcher. A single supplied version is trusted latest; multiple versions, including those inside archives, require compatible-latest selection. Firmware stays local.

Firmware root: `/home/vesper/firmware_downloads` (the verified MacBook copy). New projects: `/home/vesper/RE/<device>`. No RE jobs are preloaded. Existing legacy agents are not automatically imported or resumed.

## How it works

The controller stores queue records, ownership generations, observations, check logs, reviews, quota snapshots and token reports in `data/launcher.sqlite`. It reads account usage using the existing `proxy-usage` wrapper; the wrapper uses its key file without putting credentials in prompts or command arguments. Local account identifiers are hashed in the dashboard.

On the first submitted request, the launcher starts one watcher through the T3 CLI using the dedicated project's configured default profile: **Codex / gpt-6.1-sol / high / default service tier**. The watcher verifies inherited settings before launching workers. T3's CLI cannot set every model option, so worker creation is performed by the watcher using native `t3_thread_launch` with exact saved settings and an explicit project/root workspace. The CLI does not silently replace settings. Catalog options come from the live T3 capabilities, cached with a timestamp and refreshed by the watcher.

The dedicated watcher project is registered in T3. If its default profile is edited, update `data/config.json` watcherSelection and the project's actual T3 setting together. A user handover to an existing thread authorizes that thread's current model. Any uncertain watcher/worker creation requires reconciling T3 thread lists rather than automatically trying another creation.

The daemon wakes an idle watcher every **240 seconds by default**, coalesces notifications, and avoids sending status prompts while the watcher is already running or has a queued request. The interval is configurable; a four-minute wake does **not** guarantee cache retention. Subscription cache TTL is not established by an API cache document. `ccusage` reports actual available cached-token fields, not a claimed TTL. The watcher and quota samples use separate timers; token reports default to 15 minutes.

`watcher set` validates the new thread, increments ownership atomically and routes future notifications there. Every helper command checks the owner ID/epoch under a lock, and old wakes must stop. Existing workers continue. Native T3 external calls cannot share the SQLite lock: a launch already in flight during handover must be reconciled by its returned ID/job marker. This is not a distributed exactly-once transaction.

## Usage scheduling and boundaries

- Start at most one new job per sample, up to the global and per-batch concurrency limits. Before adding concurrency, require at least three useful intervals and 15 minutes of measurements.
- Retain each account and limit window separately. Use percentage points per hour and projected time to exhaustion, with workload/profile snapshots for estimated rate per active worker-hour. Rates reset across quota reset boundaries and changes in the active model/settings/subagent mix. Other account activity can affect the estimates; these are not causal per-job billing measurements.
- The default `short-window` policy gives short windows precedence (Claude's five-hour window); if only a weekly window exists, use that. `balanced` checks every relevant forecast; `weekly-first` gives weekly forecasts precedence. Hard exhaustion always blocks, including weekly limits under the short-window policy. Clearly named Opus/Sonnet/Haiku scoped windows are matched to the chosen model; unknown scopes are treated conservatively.
- Hold additions when estimated demand (including an additional worker and a margin) would exhaust the selected limit before reset. Different worker profiles need their own calibration. This is deliberately conservative while learning; it cannot guarantee exact depletion at a reset.
- Available account headroom is not summed. Rotation is **not implemented**: this usage API is a reader, and proxy routing controls have not been verified. An exhausted enabled account may therefore hold that pool's queue until routing or usage recovers.
- Voucher counts and expiry are displayed; approaching expiry produces recommendations. Redemption is **not implemented**. Unexpected usage decreases are labeled scheduled resets only when the old boundary was crossed; otherwise rolling recovery and provider replenishment remain possible.
- Missing/failed quota data never becomes zero. Stale or incomplete data holds new launches. Worker retries happen in the same thread after actual recovery evidence. The watcher recommends cooperative checkpoint pauses when two or more workers in a pool overshoot the measured budget. Execution is opt-in (`re-launch config checkpointPauses true` or the wizard); it requests a saved checkpoint, waits for acknowledgment, and parks one worker until the selected reset. At least one worker continues. Paused and usage-limited workers release concurrency slots; resumes reserve capacity through the same admission guard. No worker is abruptly killed.
- `ccusage@20.0.43` is pinned in `vendor/package-lock.json`. Claude/Codex local session reports and their totals remain separate from subscription limits. Repeated cumulative reports are not added together. Missing logs and failed readers remain unknown. No API-dollar estimate is used as subscription balance. A worker can be attributed only if its provider-session mapping is established; otherwise reports remain local/account totals.

## Completion review

Workers use the current `/home/vesper/RE/bens_skills/firmware-rewrite/START_PROMPT.md`, with a hashed snapshot and explicit user overrides appended. A no-subagent request overrides the skill's fresh-agent integration requirement, and that exception must be documented.

`CONTROLLER.md` defines ownership, launch/recovery and short-check behavior. `docs/REVIEW.md` defines independent artifact/source/behavior/browser review. A run under two hours triggers more scrutiny, never an arbitrary minimum duration. Completion requires evidence for assignment, originals, specification, exact, faithful, main and browser. The recorder requires a reviewed current commit and a successful captured check at that commit; a model's report alone does not pass the gate. This mechanical guard cannot prove semantic code quality: the watcher must actually perform the specified audit.

## Stop and resume a worker

Click **Stop** in the working job’s Actions column. The controller immediately records your intent and prevents the watcher from resuming or replacing that worker. It sends the watcher an update even when periodic messages are paused. The watcher uses T3’s native interruption, cancels queued turns and checks owned child work, then confirms the worker is idle.

The row shows **Stop requested** until that confirmation, then **Stopped**. If the watcher is busy or usage-limited, interruption waits for an available watcher turn; saving the request already blocks automatic restarts. **Retry stop** sends another explicit notification if needed. This state survives controller restarts and watcher changes. Stopping does not roll back files or delete the project.

Click **Resume** on a stopped job to authorize continuing its existing thread. Usage and capacity checks still apply; the row can wait for admission. Stop/Resume works in either queue launch mode and does not change that mode or periodic message settings. A stop during an uncertain replacement launch requires reconciliation first so an unregistered replacement cannot be overlooked.

## Change a queued worker’s settings

Click **Edit** beside its model in the Worker queue. Choose the provider, model, supported model settings, actual quota pool and subagent policy, then **Save settings**. Subagents can be **Not allowed**, **Allowed with limit** (1–8 concurrent children, including nested children), or **Integration checks only** (one fresh integration agent at a time). You can change these settings on any existing unlaunched queue item. The controls come from T3’s live capability catalog, including custom models; each provider keeps its exact option names and supported values. GPT 6.1 Sol, GPT 6 Astra and Opus 5.5 appear when offered by the configured T3 providers.

Editing applies only to jobs that have not begun launching. Existing workers and recovery replacements keep their saved settings. A saved change returns even a prepared job to the queue, clears its previous manual Launch approval, and requires fresh admission checks under the selected quota pool. In manual mode, click Launch again. Firmware, workspace and browser port remain as selected; previous settings and prepared evidence are retained.

The editor shows the catalog timestamp and whether it is stale. **Refresh models from T3** persists a refresh request for the watcher; it does not contact provider APIs or invent a separate model list. A busy watcher handles it when idle. An explicit refresh can request one watcher turn while periodic messages remain paused. The watcher also refreshes stale catalogs (after one hour) during normal checks and always refreshes before launching. If the watcher cannot run, cached models stay visible with their timestamp and the refresh remains pending. A changed model or model option is validated against the catalog. Subagent-only edits preserve the saved model and can be made even when that model’s catalog entry is unavailable; launch still requires a valid live selection. Subagent changes regenerate the prepared worker prompt. Concurrent edits and stale launch reservations are rejected.

## Edit recovery rules in the dashboard

Expand **Thread recovery** in the dashboard. Set **Enable thread recovery**, replacement limits and cooldown, then add rules describing what counts as broken. Each rule can replace the thread immediately or attempt the supplied repair steps first. Repair rules require a verification procedure and a bounded attempt count. No criteria are prefilled or inferred for you.

Click **Save and notify watcher**. The validated policy is saved locally and posted to the registered watcher immediately, even when it is busy or periodic messages are paused. Busy watchers queue the message. This does not change manual/automatic queue mode or resume periodic notifications. The recovery policy governs replacements independently of queue launch mode.

The form retains unsaved drafts through refreshes. If another editor changes the saved policy, a stale save is rejected; **Reload saved rules** loads the latest version. Notification status distinguishes saved rules from confirmed delivery. With no ready watcher, the notification is persisted until one is available. An uncertain delivery is not retried automatically; **Retry watcher notification** explicitly resends the saved policy. Changing rules requires the watcher to reassess any open incident before starting further actions under a stale policy snapshot.

## Replace a broken worker

Automatic detection/repair/replacement is separately controlled by a persisted, initially disabled user policy. See [THREAD_RECOVERY.md](docs/THREAD_RECOVERY.md) for the durable procedure and [RECOVERY_MESSAGE.txt](docs/RECOVERY_MESSAGE.txt) for a message to fill in later. No broken-thread criteria or provider repair instructions have been assumed. `re-launch recovery policy` shows the current policy; `re-launch recovery disable` stops further automatic recovery starts. Watcher message pause is independent.

Create the continuation thread in the same T3 project/directory, then stop and archive the broken thread in T3. Run `re-launch worker replace DEVICE NEW_THREAD_ID`, where DEVICE is the device directory name (such as `ohmforce_bohm`) or job ID. Optional `--expected-old OLD_THREAD_ID` checks that the old binding has not changed. The replacement must match the project's workspace and the job's provider/model; its existing model options are not changed or silently claimed verified.

The command adopts the existing thread without launching, restarting or messaging it. It preserves the project's files and firmware provenance, clears the watcher's cached worker observations and review, excludes prior checks from the new completion gate, and resumes monitoring of the current thread. The watcher ignores the retired conversation and sends no old messages or handover summary to the replacement. Operator records and quota history remain in the controller. The watcher generation increments so stale helper calls fail. Repeating the same replacement is idempotent.

## Services and recovery

```bash
systemctl --user status firmware-watch-dashboard firmware-watch-controller
journalctl --user -u firmware-watch-controller -n 30
systemctl --user restart firmware-watch-dashboard firmware-watch-controller
systemctl --user stop firmware-watch-controller  # stop notifications/sampling; workers continue
```

These are user services, active with the user's service manager. They do not enable system-wide lingering. The controller has nothing to launch until the user submits a queue request and its launch mode permits it. State and secrets are outside source control; back up `data/` locally. The SQLite database uses normal committed transactions, and each connection is closed after use.

For scripted requests: `re-launch enqueue REQUEST.json` uses the same validation, idempotent request ID and watcher notification as the wizard. `--no-dispatch` saves the approved queue without creating/notifying a watcher. This flag is not a simulation: a later active watcher can process those queued records once the launch mode and per-job approval permit it. Test with an isolated `--root` instead.

If watcher bootstrap delivery is uncertain, inspect `t3 thread list --project /home/vesper/fw_re_factory --json`, identify the saved bootstrap title from events, then use `re-launch watcher set ID`. Do not clear the bootstrap guard and create a duplicate.

## Verification

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Tests use isolated firmware and databases, mocked T3 launches and quota data. Browser checks cover the real local page, chart/filter rendering and responsive layout. No production firmware workers are launched by tests. The first real worker is the remaining end-to-end exercise of the live T3 launch/review workflow.
