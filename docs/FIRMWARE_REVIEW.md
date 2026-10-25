# Night watch

Keeps long-running agent chats going overnight. A T3 Code scheduled task wakes a
"watcher" chat on a timer. The watcher looks at every watched chat, and tells any
chat that stopped before finishing its work to continue. Everything it does is
logged in the watcher chat. For firmware-rewrite chats, the watcher also reviews
the delivered work before accepting a completion claim.

This file is the single source of truth. The scheduled task only points here, so
edits to this file take effect at the next check.

**How to use:** post this file's path in a chat and say "set this up". On the
laptop it's `/Users/vesper/Documents/Projects/workflow/night-watch/NIGHT_WATCH.md`.

This file says what to do, not how to call the tools. Use the T3 Code tools, and
rely on their own descriptions for names and parameters. If the T3 tools aren't
available, stop and tell the user.

## Which section to follow

- The message says "Night watch check": follow **Running a check**.
- You were given this file's path to set it up: follow **Setup**.
- You're asked to start night agents: follow **Starting night agents**.
- You're asked to pause, resume, change or remove the watch: follow **Managing the watch**.

## Settings

Edit these to change how the watch behaves.

- **Check every:** 20 minutes. Only read at setup; after changing it, see
  **Managing the watch**.
- **Active hours:** always. Can be a range such as 22:00-08:00; checks outside it
  do nothing.
- **Watch chats whose title contains:** `[night]` (any capitalization).
- **Also watch every chat in these projects:** none.
- **Idle grace:** 10 minutes. A stopped chat must be idle this long before it's
  nudged, so you have time to step in yourself.
- **Stuck after:** 60 minutes running with nothing new. Reported, never interrupted.
- **Give up after:** 3 nudges in a row with no progress, excluding usage or rate-limit retries.
- **Firmware completion review:** required for watched chats whose original brief invokes
  the firmware-rewrite skill. Completion in under 2 hours is a reason for extra
  scrutiny, never a failure by itself or a minimum time the agent must spend.
- **Nudge message:**

  > Keep going: continue the skill from exactly where you left off and work
  > through every remaining step until it's fully complete. Don't stop to
  > summarize or ask for confirmation in between. Only stop if you're truly
  > blocked, and then say exactly what's blocking you.

## Current watch notes

- Arbhar chat `baea438c-4ced-4534-ad0e-d5ea8b796b68`: keep the normal
  20-minute usage-limit retries with no three-nudge cutoff. Read the current
  status/reset notice; old reset times are not a reason to defer a retry.
  If already running, leave it alone.
- Golden Master chat `60d2c0b2-c563-4daa-8e83-c1eb77d738db`, repository
  `/home/vesper/RE/golden_master`: its completion claim at `6740ada` has
  unresolved review findings. In a temporary copy, replacing the four
  canonicalizing add/sub/mul/divide helper bodies with ordinary C arithmetic
  passed 16 normal/seeded comparisons over 181,248 stereo frames with zero
  audio error, both with original flags and main DSP mask 15. The main-policy
  DSP benchmark was about 3.5 times faster with the same checksum. Evidence
  is local in `/tmp/gm-readability-audit-u3a1on8h/`; rerun if unavailable.
  This supports further simplification; it is not a universal equivalence
  proof or a reason to remove sound-shaping approximations without checks.
  Also review whether `tests/test_library_rate.py` actually verifies the
  attack/release timing bound in `tests/FEATURE-CONTRACT.md`, rather than
  only comparing one envelope with the same native implementation.
  Its browser DOES exist and was verified serving/producing audio on port
  8765; a localhost address on a different machine is an access issue.
  Carry these findings into its completion review; do not erase them merely
  because the agent repeats that all stages are done.

## Setup

The goal: the night's agent chats exist and are watched, and a scheduled task
titled "Night watch" runs a check in the chat you're in every Check every.

**Ask, don't guess.** When something below is missing or vague, ask the user. Put
all your open questions in one message, not one at a time.

1. **Only one watch.** If a "Night watch" task already exists, don't create
   another. Tell the user which chat it runs in, and stop.
2. **Confirm the machine.** Tell the user which machine this chat runs on, and
   confirm that's where the night agents should run. Most of the time it should
   be a remote box. You can only see and create chats on this machine, so if the
   user wants another one, tell them to start the watcher chat there and stop.
   If this machine is a laptop, warn that checks stop whenever it sleeps.
3. **Ask: do the chats to watch already exist, or should I create them?**
   - **They exist:** find out which ones, unless the user already said. Make
     sure each is watched by adding `[night]` to its title.
   - **Create them:** follow **Starting night agents**.
4. **Create the scheduled task.**
   - Checks run in this chat, not in a new chat each time, so the log stays in
     one place.
   - Keep its prompt short and pointing here. It should say "Night watch check",
     give this file's absolute path and this chat's ID (so checks can skip the
     watcher), and say what to reply if the file can't be read. Don't copy the
     rules into the prompt; that would stop edits here from taking effect.
5. **Report back** with the next check time, a link to each watched chat, and
   how to add another: put `[night]` in its title.

## Running a check

1. **Time.** Note the local time. If Active hours is a range and now is outside
   it, reply `<time> · Outside active hours.` and stop.
2. **Find watched chats** across every project on this machine. Match titles
   yourself instead of relying on a search filter. Skip the watcher chat and
   helper chats that belong to another chat.
3. **Look at each one:** its status, its recent runs, and its last assistant
   reply. Then decide what state it's in:
   - **Working:** still running. Leave it alone. If nothing new has appeared for
     longer than Stuck after, report it as possibly stuck, but don't interrupt it.
   - **Waiting on a question:** if the question only asks whether to continue or
     proceed, answer it with the choice that keeps going. If it's a real
     decision, or the chat is waiting on a permission approval, leave it and
     report it.
   - **Never started:** no runs at all. Leave it alone.
   - **Stopped:** idle, finished, interrupted or failed, and the last run ended
     longer ago than Idle grace. Read the last assistant reply and judge:
     - **Claims done:** for a firmware-rewrite chat, follow **Firmware completion
       review** below before classifying it as done. A final summary, a stage
       checklist, test counts or a repeated "yes, all stages" are claims to
       verify. Other chats can be judged from their final deliverable/reply.
     - **Stopped early:** it summarized partial progress, listed next steps
       without doing them, asked whether to continue, ended mid-step, or the run
       was interrupted. Nudge it.
     - **Blocked:** it can't go on without something only the user can provide,
       such as a login, a key or a decision. Don't nudge. Report it.
     - **Usage or rate limit:** retry once at each scheduled check (every
       Check every), even if a later reset time is shown. Keep retrying until
       usage is available; these retries do not count toward Give up after.
       Never interrupt or queue another nudge onto a running or pending retry.
     - **Other error:** nudge once. If it failed again after an earlier nudge,
       stop nudging and report it.
4. **Nudge** by sending the Nudge message, word for word. When a firmware
   completion review finds concrete missing work, append a short review brief
   with the requirement, file/test evidence, and what must be finished or
   demonstrated. A generic "keep going" alone does not communicate an audit
   finding. Never nudge the same
   stopped run twice, even if two checks overlap, except for usage or rate-limit
   retries on separate scheduled checks. Never retry more than once per check.
   If the chat's last nudges, up to Give up after, produced no new progress,
   don't nudge. Report it as stuck. Usage or rate-limit retries are exempt
   from this give-up rule.
5. **Log** a short reply in the watcher chat that starts with the time:
   - Nothing needed action: one line, such as `14:20 · 3 watched: 2 working, 1 done.`
   - Nothing watched: `14:20 · No watched chats.`
   - You nudged, answered or reported something: the one-line summary, then one
     line per affected chat with a link to it, what you did, and why.

During a check, limit messages in watched chats to the actions above. Completion
reviews may inspect repositories and run local checks, but must not edit the
watched project's source or finish the implementation for the agent. Never
change the scheduled task during a check.

## Firmware completion review

Apply this to watched firmware-rewrite chats, including ones previously marked
"done" solely from their final reply. Keep generic night-watch behavior for
other tasks. Evaluate the work and evidence; do not infer effort or intent from
the model's identity, confident wording, or runtime.

1. **Recover the actual assignment.** Read the first user message and later scope
   changes, repository AGENTS.md, and the actual firmware-rewrite SKILL.md it
   names. Locate the real checkout and preservation branches. User instructions
   take precedence. Do not substitute a generic stage checklist for that brief.
2. **Check each required deliverable against artifacts.** Read STATUS.md, SPEC.md,
   final reports, relevant source and tests. Trace stage claims to rerunnable
   evidence: correct latest image and integrity checks; unmodified original in
   the emulator/browser; feature specification and experiments; original-versus-C
   exact proof and its exceptions; readable faithful with tested quirks; portable
   main with independent instances, caller memory, rate/block/event handling,
   device/platform separation, adapters, integrations and final comparisons.
   Branch names and checked boxes alone do not demonstrate any of these.
3. **Inspect readability and reuse directly.** Look for decompiler control flow,
   address-based RAM images, broad whole-state access, opaque constants, unused
   paths, and arithmetic emulation or operation order carried over solely for
   exactness. Distinguish sound-shaping character from avoidable exactness cost.
   Require a failing behavioral comparison to justify retaining a simplifiable
   shortcut; one grouped math switch failing does not justify every shortcut in
   that group. A working wrapper/API around opaque code does not finish Stage 6.
   Use temporary copies for simplification experiments and report their tested
   domain. Do not demand removal of a deliberate algorithm with proven impact.
4. **Verify the proof, not just the totals.** Run appropriate existing checks and
   representative independent probes. Inspect whether the oracle really executes
   the original/frozen reference, whether planted errors are caught, and whether
   scenes exercise the claimed feature. Avoid self-comparisons that can share the
   same bug. Trace the feature checklist to actual assertions, including physical
   Hz/ms/dB behavior across supported rates, dynamics, controls, storage and audio
   quality. A loose envelope/sample bound is not automatically a measured timing
   bound. Integration success does not replace behavior or readability proof.
5. **Check the browser handoff.** Verify the server, required JS/WASM assets and
   actual page initialization; exercise audio and controls when practical.
   Confirm the page offers the promised versions and original loading behavior.
   Explain which machine serves it and how the user can reach it. Do not mark a
   browser missing just because remote localhost is not the user's localhost.
6. **Give short runs extra scrutiny.** Under 2 hours to claim the whole workflow
   should prompt closer source inspection, proof coverage and a targeted attempt
   to simplify retained exactness cost. Reuse and parallel work can legitimately
   make a run fast. Never ask an agent merely to spend longer or pad its reports.
7. **Decide and act on evidence.**
   - **Verified done:** required artifacts and relevant checks support completion,
     with limitations consistent with the skill and the user's scope. Log the
     reviewed commit and checks. No finite audit proves all possible behavior.
   - **Incomplete:** concrete missing deliverables, avoidable readability costs,
     failed checks, or claims unsupported by the required proof. When stopped and
     past Idle grace, resume with the Nudge message plus specific findings and
     completion criteria. Ask it to do the remaining work, not merely reaffirm
     completion. Preserve its exact/faithful branches and project-local rules.
   - **Review pending:** evidence or access is insufficient, or review needs more
     time. Report exactly what remains unverified; do not label it done or send
     a corrective nudge based only on suspicion. An unavoidable lack of physical
     hardware/recordings is not by itself a failure if the skill permits a stated
     boundary and the available work has been completed.
   Apply the existing question/approval, grace, duplicate, usage-limit and give-up
   rules. Unrelated helper approvals do not establish that a usage-limited parent
   itself needs approval. Never interrupt a running parent to deliver a review.

**Keep reviews bounded and durable.** Check all watched chats for urgent resume or
usage-limit actions before starting a deep audit. Record the reviewed thread/run,
commit, relevant worktree changes, brief/skill/instruction hashes, evidence paths,
findings, verdict and corrective-message ID under
`/home/vesper/.local/state/night-watch/<watcher-chat-id>/firmware-audits.json`
on the watcher machine. Keep this a local record, without vendor source copies.
Do not change the watch instructions during an ordinary check. Reuse a completed
review for unchanged inputs; do not rerun the whole suite every 20 minutes.
Recheck changed work and unresolved findings after a corrective run. If a review
spans checks, save its remaining work and continue it without duplicate nudges.
No progress after three corrective nudges means report the unresolved findings
as stuck; usage-limit retries remain exempt. Previously unreviewed completion
claims start as review pending, not verified done.

## Starting night agents

Used during setup, and on later nights when the watch is already running.

1. **Collect what each agent needs.** For every agent:
   - **What it should do:** its first message, such as the task or the skill to
     follow from start to end.
   - **Agent and model:** for example Codex with GPT-6.1-Sol on high, or Claude
     Opus 5.5. Offer the ones available on this machine.
   - **Where it works:** the project or folder.

   If any of this is missing or vague, ask the user (all questions in one
   message). Don't fill gaps with defaults unless the user tells you to.
2. **Confirm the plan.** List the agents back to the user, one line each (title,
   agent and model, folder, task), and wait for their OK.
3. **Start them.** One new top-level chat per agent. Its title starts with
   `[night]`, and its first message is the task as agreed.
4. **Check the titles.** The app may retitle new chats. Make sure each title
   still contains `[night]`, and rename any that lost it.
5. **Reply** with a link to each new chat.

## Managing the watch

- **Pause, resume or remove:** do it on the "Night watch" scheduled task.
- **Change how often it checks:** update Check every in Settings, then change the
  task's schedule to match.
- **Change anything else**, such as which chats are watched or the nudge text:
  edit this file. The next check uses it.

## Known limits

- The watch runs on the machine that holds the watcher chat, and only sees chats
  on that machine. Put the watcher and the night agents on the same always-on
  machine, such as a remote box running T3 as a background service
  (`t3 service install`), so it survives reboots.
- On a laptop, checks stop while it sleeps or the lid is closed.
- Missed checks don't catch up. A check cut off by a server restart is marked
  failed and isn't retried; the next one runs as normal.
- The agents (Claude Code, Codex) must be logged in on that machine.
- This file must exist at the path written in the task's prompt. If you move it,
  update the task's prompt.
- Each check is a full agent turn in the watcher chat: 72 a day at 20 minutes.
