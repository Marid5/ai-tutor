---
name: choose-exercises
description: Recommends which AI Tutor exercise kinds (triage, choice, cloze, assemble) a program or chapter should enable, from the kind of material, the learner's level and the validator's coverage table, and edits the exercises switches after the user confirms. Use when the user asks which exercises to use, says lessons feel too easy or too hard, after a new chapter is added, or when make validate warns that an enabled kind is not supported.
---

# Choose exercises

Exercise switches live in `content/program.yaml` (`exercises:`, the default for every chapter) and in a chapter file (`exercises:`, overriding single kinds). How each kind works, and which cards support it, is in `docs/exercises.md`. Read it first.

Facts that drive every recommendation:

- A card's rungs are the kinds its chapter enables **and** its data supports, in the order `choice` → `cloze` → `assemble`. The first rung is the primary check, the one that decides whether a card is known.
- Every card must keep at least one rung, or validation fails.
- `flash` is always on and has no switch. `triage` only adds a first look at new cards; it never replaces the check.
- Changing switches resets nobody's progress. Steps of open sessions that the new settings no longer allow are skipped as stale.

## 1. Gather the evidence

1. Run `make validate` and keep the coverage table and warnings. For each chapter compute the share of cards supporting each kind: `choice / cards`, `cloze / cards`, `assemble / cards`.
2. Read the current switches: `exercises` in `content/program.yaml`, and the `exercises` line of each `content/chapters/<id>.yaml`. A chapter without one inherits the program defaults.
3. Look at the cards of each chapter: answer length, whether answers are terms, sentences or enumerations.
4. Know the learner: new to the subject, or revising for an exam? If the user has not said and you can ask, ask. Otherwise assume new to the subject.

## 2. Decide

| Material | Enable | Why |
|---|---|---|
| Terms and definitions, names, dates, numbers, vocabulary | `choice` + `cloze` | Short keys are recognised among lures (`choice`), then recalled inside their sentence (`cloze`). |
| Exact wording that matters: rules, formulas stated in words, phrases in a foreign language, steps in order | `choice` + `cloze` + `assemble` | Rebuilding the sentence trains wording and order. Only answers of 4 to 8 words support it. |
| Long explanations (answers over 8 words) | `choice` + `cloze`, not `assemble` | `assemble` would apply to almost no card. |
| Enumerations (`key_mode: all_of`) | `choice` (+ `assemble` if the answers are short) | `all_of` cards never support `cloze`. |
| Learner new to the subject | `triage: true` | New cards are shown with their answer before the first check, and "Don't know" adds a flash card later in the lesson. |
| Learner revising known material, exam preparation | `triage: false` | New cards go straight to their primary check, so known cards cost one step. |
| Learners say `choice` is too easy | `choice: false` in that chapter | The primary check becomes `cloze` (or `assemble`). Only if every card keeps a rung. |

Coverage rules:

- Recommend a kind for a chapter only if **at least half** of its cards support it. Below that, the switch changes little; say how many cards would need new data instead (for example: "4 of 9 answers are 4 to 8 words").
- Never recommend a setting that leaves a card with no rung. Before recommending to turn a kind off, apply the switch locally and run `make validate`; the gate error names every card left without a rung; revert if any. Either way, leave the files as they were until the user confirms.
- Set the program default for what most chapters need, and use a chapter override only for the chapters that differ. List only the kinds that differ: `exercises: {assemble: true}`.

Improving coverage means editing cards (distractors with the option's word count, the option word for word in the answer, shorter answers). On cards learners have already studied, that resets their schedule for everyone, so propose it as a separate step and leave it to the `add-content` checklist.

## 3. Confirm

Show the proposal and wait for the user's answer:

| Scope | Now | Proposed | Coverage | Reason |
|---|---|---|---|---|
| program | triage, choice, cloze | unchanged | | |
| `foundations` | inherits | `+ assemble` | assemble 9/12 | short definitions; rebuilding them trains the wording |

Edit `exercises` only after the user confirms, or if they said in advance "proceed without asking". If nobody can answer (a headless run), do not edit: report the proposal table and stop.

## 4. Apply and verify

1. Edit the switches in `content/program.yaml` or the chapter file.
2. Run `make validate`: it must exit 0, with no `<kind> is enabled but no card in this chapter supports it` warning for a kind you enabled.
3. Commit only the switch change: `git add content/` and `git commit -m "content: exercises for <scope>"`.
4. Report the final settings per chapter, the coverage table, and what learners will notice: a different primary check, or triage on or off for cards they have not met yet.

## Never

- Never turn a kind off when that leaves a card without a rung.
- Never edit card answer material to raise coverage without the user's explicit consent.
- Never try to list `flash` under `exercises`: it has no switch and the validator rejects it.
