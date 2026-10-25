# Broken-thread recovery

Automatic recovery starts disabled. The user supplies the definition of broken, any permitted repair instructions, and the success check. The watcher applies those rules to observed symptoms; it does not need a root-cause explanation and must not invent provider-specific repairs. Ordinary usage exhaustion or an unfinished stage is not sufficient unless an explicit user rule says otherwise.

Use the dashboard's **Thread recovery** editor to add or change conditions, actions, repair steps, verification and limits. **Save and notify watcher** validates and saves the rules, then immediately posts the complete saved policy to the watcher, including while it is busy or periodic messages are paused. The policy applies independently of manual/automatic queue launch mode. Alternatively, use `RECOVERY_MESSAGE.txt` as a message to fill in and send to the watcher. A direct user message can configure the policy even while automatic wake messages are paused. Configuring or enabling recovery never resumes automatic messages by itself.

## Manual one-shot replacement

Use **Replace thread** in the dashboard to explicitly retire a registered unfinished worker and have the system create a continuation. This also works on a stopped worker. It saves an `origin: operator` incident in `retire_pending`, invalidates earlier watcher decisions, supersedes unlaunched repair/Stop/Resume intents and immediately notifies the watcher, including during periodic-message pause. You do not need to define a broken-thread rule or enable automatic recovery for this manual decision, and manual queue mode does not require an additional Launch click.

The watcher uses the same retirement, prompt preparation, launch reservation and result-recording helpers described below. There is no parallel replacement mechanism. It interrupts/cancels the old worker and its queued/owned child work, verifies idle and archives the thread, checks immutable inputs, then creates a fresh top-level thread in the same project with the saved model/options and subagent policy. It uses the existing continuation preamble, full standard task and separate bottom run details. Old chat history, identities and error/repair diagnostics never enter that prompt.

Each operator incident authorizes one replacement. Automatic policy edits, replacement cap and cooldown do not veto that one explicit request; quota, concurrency, scheduling pause, per-sample admission, original-file checks and uncertain-launch protections remain enforced. Retry replacement reuses the saved incident, including launching/attention state and candidate ID. It never authorizes blindly repeating a non-idempotent launch. A later Stop cancels an unlaunched replacement. A different unresolved candidate must first be reconciled.

The interface shows replacement progress or a concrete waiting reason. If the watcher is busy or provider-limited, the saved request waits. Notifications survive daemon restarts and watcher handovers. The user’s automatic policy remains unchanged.

## Persistent policy

`data/recovery-policy.json` stores:

```json
{
  "enabled": false,
  "source": "User message ID or local policy file",
  "rules": [],
  "maxReplacementsPerJob": 3,
  "cooldownSeconds": 900
}
```

These are initial bounds, not broken-thread detection rules. The user may change them when supplying the policy. A rule has an `id`, a natural-language `when`, and `action` (`replace` or `repair_then_replace`). A repair rule additionally requires `repairSteps` (instruction strings), `verify` (the success check) and a positive `maxRepairAttempts` of at most 5. The watcher records evidence identifying the matched rule; no cause diagnosis is required. Enabling an empty policy is rejected.

Human commands:

```bash
re-launch recovery policy
re-launch recovery set-policy /path/to/policy.json
re-launch recovery disable
re-launch recovery list
re-launch recovery show RECOVERY_ID
```

These CLI commands never send messages to workers or resume periodic watcher notifications. Dashboard saves additionally send a dedicated policy-update message. They retain unsaved browser drafts, reject stale saves using a policy revision, persist pending notifications when no watcher is ready, and expose uncertain delivery for explicit retry. Once a send attempt is recorded, it is not repeated automatically after a timeout or crash. Keep the original user wording when translating a supplied message into structured rules. Missing repair/verification instructions must not be filled in from assumptions; leave the policy disabled or ask the user to complete it.

## Watcher procedure

All actions use `controller.py --owner YOUR_ID --epoch N recovery ACTION [ID]`. Mutating actions are fenced and persisted. Read with `policy`, `list`, `show ID`; configure with `policy_set` and JSON stdin only when the user has supplied/authorized the policy.

1. **Report a match:** `recovery report JOB_ID`, JSON stdin `{"ruleId":"USER_RULE_ID","expectedThreadId":"CURRENT_WORKER","evidence":"Observed symptoms satisfying that rule"}`. The helper opens one incident for that job and snapshots the policy. A `repair_then_replace` rule enters `repair_pending`; a `replace` rule enters `retire_pending`. The job becomes `recovery_pending` and retains its slot.
2. **Repair, when prescribed:** `recovery repair_begin ID` records the attempt before external actions. Perform only the saved rule's `repairSteps`, then its `verify` procedure. Record `recovery repair_result ID`, JSON `{"working":false,"evidence":"Actual outcome"}`. A successful verified repair closes the incident as `recovered`. Failed attempts advance to `retire_pending` when the limit is exhausted. Do not repeat an attempt whose outcome was lost; inspect and record its actual result first. A changed/disabled policy blocks further starts.
3. **Retire:** in `retire_pending`, fence with `recovery retire_begin ID` to obtain the exact worker/run and stable interrupt clientRequestId, then interrupt the broken worker through native T3, cancel queued turns/owned child work and archive it. Confirm it has no active run or queued messages. Never stop another worker or change/delete project files to fix a conversation.
4. **Prepare:** `recovery prepare ID` verifies retirement and uses the shared worker prompt renderer with a continuation preamble: another agent worked on the project; its files are in the directory; inspect and verify them, then continue. The complete current canonical START_PROMPT (including approved prompt edits) is enclosed between task-prompt boundaries. Saved device, file/version, host, browser and subagent settings remain separate in one Run details section at the very bottom. Replacement prompts are always rendered from the standard task, so repeated replacements do not nest earlier continuation preambles. It reuses the existing workspace and originals. Artifacts live under `data/jobs/JOB_ID/recoveries/RECOVERY_ID/`. No conversation, error or repair history goes into this prompt. The incident and job enter `replacement_ready`.
5. **Reserve the launch:** an enabled automatic policy or explicit origin=operator ticket authorizes its replacement in either queue launch mode; no additional manual Launch approval is needed. Refresh usage and capabilities, then `recovery launch ID`. It applies quota freshness, exhaustion, scheduling pause, concurrency, per-sample admission, replacement cap and cooldown checks for automatic incidents. Explicit operator incidents bypass only those automatic bounds and automatic-policy enablement. It returns the exact project/model/workspace binding, unique title and prompt path, and persists `launching` before the external call. Do not call launch twice. A missing response requires T3 reconciliation using the saved unique title/project and launch time.
6. **Create:** call native `t3_thread_launch` with that returned title, projectId, modelSelection, workspaceStrategy root, and the complete generated prompt as message. Do not use a conversation fork, attach an old transcript, or include thread identities or recovery diagnostics. The generated preamble acknowledges prior project work only.
7. **Record:** `recovery record ID`, complete structured launch result as JSON stdin. The helper retains the candidate ID before validation, checks exact returned model/options/project, adopts the new thread, resets old observations/review/calibration, and closes the incident as `replaced`. Owner epoch increments; read the returned epoch before any subsequent actions. A mismatch becomes `attention` with the candidate retained, never permission to create another thread. A lost record acknowledgment is reconciled and can be recorded idempotently.

No full-provider restart, credential change, account rotation or destructive repair is implicitly authorized by this mechanism. Such an action needs explicit applicable user instructions. A maximum or uncertain outcome requires user intervention rather than an endless replacement loop. The helper enforces the state/attempt/launch bounds; whether symptoms match a natural-language rule and a prescribed repair worked is the watcher's evidence-based judgment.

## Operator recovery

The dashboard **Replace thread** creates the fresh continuation for you. The existing `re-launch worker replace DEVICE EXISTING_THREAD` remains available when the user manually creates a continuation. It closes any superseded open recovery incident and adopts that existing thread without sending it history. An uncertain automatic launch must be reconciled before creating another candidate.

`recovery cancel ID` is available through the fenced helper before an external replacement launch. An uncertain launch without an identified candidate cannot be cancelled and forgotten. Once a candidate is identified, stop/archive it before cancelling its recovery record. Another manual adoption is blocked while a different unresolved candidate remains. Disabling policy blocks further starts but still permits recording an in-flight repair/launch outcome so created threads cannot be lost.
