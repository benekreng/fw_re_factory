# Independent firmware completion audit

## Firmware completion review

Apply this to approved firmware-rewrite jobs, including ones previously marked
"done" solely from their final reply. Evaluate the work and evidence; do not infer effort or intent from
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
     failed checks, or claims unsupported by the required proof. When stopped, resume with specific findings and
     completion criteria. Ask it to do the remaining work, not merely reaffirm
     completion. Preserve its exact/faithful branches and project-local rules.
   - **Review pending:** evidence or access is insufficient, or review needs more
     time. Report exactly what remains unverified; do not label it done or send
     a corrective nudge based only on suspicion. An unavoidable lack of physical
     hardware/recordings is not by itself a failure if the skill permits a stated
     boundary and the available work has been completed.
   Apply CONTROLLER.md ownership, duplicate, usage-limit and escalation rules. Unrelated helper approvals do not establish that a usage-limited parent
   itself needs approval. Never interrupt a running parent to deliver a review.

**Keep reviews bounded and durable.** Check all watched chats for urgent resume or
usage-limit actions before starting a deep audit. Record the reviewed thread/run,
commit, relevant worktree changes, brief/skill/instruction hashes, evidence paths,
findings, verdict and corrective-message ID under
the launcher database via the fenced review helper. Evidence files may live under
`data/jobs/<job-id>/` on the controller machine. Keep this a local record, without vendor source copies.
Do not change the watch instructions during an ordinary check. Reuse a completed
review for unchanged inputs; do not rerun the whole suite on every short wake.
Recheck changed work and unresolved findings after a corrective run. If a review
spans checks, save its remaining work and continue it without duplicate nudges.
No progress after three corrective nudges means report the unresolved findings
as stuck; usage-limit retries remain exempt. Previously unreviewed completion
claims start as review pending, not verified done.


Use the new local CONTROLLER.md for ownership, scheduling and job recording. Legacy watch lists and schedules are not inputs.
