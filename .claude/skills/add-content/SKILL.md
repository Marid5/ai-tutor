---
name: add-content
description: Turns a learner's material (photos, PDF, pasted text, notes) into AI Tutor cards and lessons in content/, validates them with make validate and commits the result. Use when the user asks to add, create, import or extend cards, a lesson or a chapter from their material.
---

# Add content

Turn material into cards that pass `make validate`, without touching anyone's existing progress. The field reference is `docs/content-contract.md`; the exercise rules are `docs/exercises.md`. Read both once before your first card.

**Interactive or not.** By default you show the user a table of proposed cards and wait for their edits (step 6). Skip that wait when the user said "proceed without asking" (or equivalent), or when nobody can answer: a headless run, CI, or another agent running you as a batch job. Everything else, including the final report, stays the same.

**Scope.** Change only files under `content/`. Do not change `exercises` switches here: if the material needs different exercise kinds, finish the cards and recommend the `choose-exercises` skill.

## 1. Get a green baseline

1. Run `make validate`. If `.venv` is missing, run `make setup` first; if that fails on the client dependencies, `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` is enough.
2. The baseline must exit 0. Keep its coverage table and warnings: you compare against them later. If the baseline already fails, stop and report the errors. Do not fix content you were not asked to touch.
3. Read `content/program.yaml` and every file in `content/chapters/`. Note the chapters, lesson ids, card ids, the exercise switches in effect per chapter, and which facts are already covered.

## 2. Read the material

- **Photos and screenshots:** read them with vision and transcribe to plain text first, in page order. Where a word, number or symbol is unreadable or ambiguous, mark it `[unreadable]` and never guess. Re-check names, numbers, dates and units against the image before using them.
- **PDF:** use the text layer (`pdftotext -layout file.pdf -` if it is installed, or your own PDF reader). A scanned PDF without text is a set of images: handle it as photos. Keep page numbers for `source`. If the user named pages or a chapter, use only those.
- **Plain text and notes:** use them as given. Notes often skip context; complete a statement only from the material itself, never from what you believe the author meant.
- **Vocabulary lists:** one card per entry. The prompt asks for the meaning (or the word), `option` is the short translation, and the distractors are other entries of the same part of speech from the same list.

## 3. Extract facts

1. List atomic statements: one checkable fact each (a definition, a cause, a number, a name, a rule, a step in a sequence).
2. Keep a fact only if the material states it. Do not add facts from your own knowledge, do not "correct" the source silently, and do not fill gaps with plausible numbers. If the source looks wrong or contradicts itself, leave that fact out and report it.
3. Drop opinions, anecdotes, examples that carry no fact of their own, and facts already covered by an existing card (report them as skipped).
4. Write everything in your own words. Never copy sentences from copyrighted material; terms, names, numbers and formulas are fine.
5. Write cards in the language the learner studies in. If it differs from `language` in `program.yaml`, say so in the report.

## 4. Plan lessons and ids

- **Lessons:** 1 to 10 cards each (a hard limit); aim for 4 to 8, grouped by sub-topic, in a sensible learning order. More material means more lessons.
- **Placement:** append a new lesson to the end of the best-matching chapter's `lessons`. For a new topic, create `content/chapters/<chapter-id>.yaml` (`id` equal to the file name, a `title`, `lessons`) and add the id to `chapters:` in `content/program.yaml` where it belongs in the course.
- **Ids:** lowercase letters, digits and hyphens, at most 64 characters, describing the content: `what-is-an-embedding`, `embeddings-basics`. Card ids are unique across the whole program, and so are lesson ids. Before using one, check it is free: `grep -rn "id: <candidate>$" content/`.
- **Existing ids are permanent.** Never rename, reuse or delete one, and do not edit existing cards unless asked: changing an existing card's `answer`, `option`, `distractors`, `accepted` or `key_mode` resets that card for every learner.

## 5. Draft the cards

Check every card against this list:

- [ ] **One fact.** The card asks one thing. No "and", no two-part answers unless the fact is an enumeration (then `key_mode: all_of` with `accepted` listing the parts; such a card has no cloze).
- [ ] **Verifiable.** The answer is stated in the material. `source` says where (`Course notes, week 3`, `Textbook ch. 4, p. 81`; use `Original text` only for text you wrote from scratch).
- [ ] **Prompt** is a question that can be answered without seeing any options, and does not contain the answer.
- [ ] **Answer** is one full, short sentence. If the chapter enables `assemble`, give it 4 to 8 words where it reads naturally.
- [ ] **Option** is short (1 to 5 words) and appears **word for word** inside `answer`. That makes it the cloze key.
- [ ] **Exactly 3 distractors**, each with **the same number of words as `option`**, the same grammatical form and a similar length. Each is plausible to someone who has not learned the card and clearly wrong according to the material. No synonyms or partial truths of the right answer, no "all of the above", no jokes. Distractors are the only text you invent.
- [ ] **Cloze works:** besides the option, at least 3 words of `answer` remain, and the option appears in `answer` only once.
- [ ] **Hint** (optional) nudges without giving the answer away. **Note** (recommended) adds the why or a common confusion in one or two sentences.

Words are counted by whitespace, punctuation included: `Paris.` is one word.

A card that passes every check, from the demo course (`content/chapters/foundations.yaml`). The option is inside the answer, and every distractor has the option's four words, so the card supports `choice`, `cloze` and, at 8 words, `assemble`:

```yaml
      - id: what-is-an-embedding
        prompt: What is a token's embedding?
        answer: A token's embedding is a list of numbers.
        option: a list of numbers
        distractors: [a short written definition, its position in text, a compressed image file]
        hint: Think of coordinates in a space with many dimensions.
        note: >-
          Each token id is mapped to a learned vector, and these numbers are what the rest of
          the network actually computes with.
        source: Original text
```

YAML pitfalls: quote a value that contains `: ` or ` #`, starts with `'`, `"`, `[`, `{`, `*`, `&`, `!`, `|`, `>`, `%` or `@`, or reads as a boolean, number or null (`yes`, `no`, `on`, `1e5`, `null`). Use `>-` for long notes. A key written twice in one card is an error.

## 6. Confirm with the user

Show one table per lesson and stop until the user replies:

| # | Lesson | id | Prompt | Answer | Option | Distractors |
|---|---|---|---|---|---|---|

Under it, list facts you skipped and why, and any `[unreadable]` spots. Apply the user's edits, then continue. In a non-interactive run, or after "proceed without asking", skip the wait and include the same table in the final report.

## 7. Write and validate

1. Write the YAML.
2. Run `make validate` and repeat until it exits 0.
3. Compare with the baseline. Output looks like this:

   ```
   chapter      cards  choice  cloze  assemble
   foundations     17      17     17        12
   ```

   Each number counts the chapter's cards whose data supports that kind, whether or not the chapter enables it. Your new cards should raise `cards` by their number, `choice` by the same number and, by the same amount, `cloze`. A smaller rise in `cloze` means some option is not word for word in its answer, or the distractors' word counts differ from the option. `assemble` rises only for answers of 4 to 8 words.
4. Check the rungs of each new card (closed kinds it will actually get, in order):

   ```bash
   .venv/bin/python -c "from app.content import load_program; p = load_program('content'); [print(l.id, c.id, *p.rungs(ch.id, c)) for ch in p.chapters for l in ch.lessons for c in l.cards]"
   ```
5. No new `warning:` lines compared with the baseline. Fix the cards, not the switches.

| Error or warning | Fix |
|---|---|
| `invalid YAML (...)`, `duplicate key '...'` | Fix indentation or quoting at the reported line; remove the repeated key. |
| `unknown field 'x'` | Remove it or correct the spelling. The allowed fields are in `docs/content-contract.md`. |
| `missing required field 'x'` | Add it (`id`, `prompt`, `answer` for cards; `id`, `title`, `cards` for lessons). |
| `id: String should match pattern ...` | Lowercase letters, digits and hyphens only, at most 64 characters. |
| `uses a reserved prefix (review-, practice-)` | Choose a lesson id that does not start with them. |
| `duplicate card id` / `duplicate lesson id` | Choose a new id for **your** item; never rename the existing one. |
| `distractors: provide exactly 3 or omit the field` | Write exactly three. |
| `distractors: '...' is also a correct answer to this card` | Replace that distractor with a wrong one. |
| `options must be distinct` | Two buttons read the same after lowercasing and removing accents; reword one distractor. |
| `key_mode all_of requires every accepted key in the answer` | Put every key in `answer`, or correct `accepted`. |
| `a lesson needs 1 to 10 cards` | Split the lesson. |
| `must not be blank` | Fill the field or remove it if optional. |
| `no enabled closed exercise is possible — ...` | Do what the message says for that card: add 3 distractors, or give the answer 4 to 8 words when the chapter enables assemble. |
| `chapter '...' is listed but chapters/....yaml does not exist`, `not listed in program.yaml chapters`, `must match the file name` | Keep `program.yaml` `chapters:`, the file names and each chapter's `id` in agreement. |
| `warning: option has N words but the longest distractor has M` | Shorten the option or lengthen the distractors to the same word count. |
| `warning: <kind> is enabled but no card in this chapter supports it` | Make the cards support it (see the checklist), or recommend `choose-exercises` to the user. |

`make validate` is the gate for content. `make test` also passes on any valid course: `tests/test_demo_content.py` checks that whatever is in `content/` loads without errors or warnings, and skips its demo-specific checks once the course is no longer the bundled demo. Never edit tests to make content pass.

## 8. Report and commit

1. `git status --short` must list only paths under `content/`.
2. If the user asked for a pull request, first `git switch -c content/<lesson-id>`. Then `git add content/` and `git commit -m "content: add <lesson title> lesson"` (or `chapter`). For a pull request, then `git push -u origin content/<lesson-id>` and `gh pr create --fill`. Otherwise do not push.
3. Report to the user:
   - the cards added, as the table from step 6, with the file and lesson they went to;
   - the final `make validate` output, verbatim, and what changed against the baseline;
   - the facts skipped and why, unreadable spots, and doubts about the source;
   - anything to decide next (exercise switches, a language mismatch, the demo-course test).

## Never

- Never change, reuse or delete an existing chapter, lesson or card id.
- Never edit the answer material of existing cards (`answer`, `option`, `distractors`, `accepted`, `key_mode`) unless the user asked for that exact change.
- Never copy copyrighted text verbatim into cards.
- Never invent facts, numbers or sources. If the material does not say it, it is not a card.
- Never weaken the validator, the contract or their tests (`app/content.py`, `scripts/validate_content.py`, `tests/`) to make content pass.
- Never change files outside `content/` in this workflow.
