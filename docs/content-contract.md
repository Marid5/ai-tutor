# Content contract

A course is plain YAML in `content/`: one `program.yaml` and one file per chapter in `content/chapters/`. The rules below are enforced by `app/content.py`. `make validate` checks them, the Docker build runs the same check, and the app refuses to start on invalid content, listing every problem. If this page and the code ever disagree, the code wins.

```
content/
├── program.yaml              # course settings and chapter order
└── chapters/
    ├── foundations.yaml      # one file per chapter; file name = chapter id
    └── training.yaml
```

General rules for every file:

- Unknown fields are errors, at every level. A typo such as `distractor:` fails validation instead of being ignored.
- A key repeated in the same mapping is an error. YAML would normally keep the last value without a word.
- Text fields that are required must not be blank.
- Chapter files use the `.yaml` extension. Every file in `chapters/` must be listed in `program.yaml`, and every listed chapter must have a file.

## Identifiers

Learner progress is stored against ids, so ids are the one thing you must never change.

- Chapter, lesson and card ids match `^[a-z0-9][a-z0-9-]{0,63}$`: lowercase letters, digits and hyphens, starting with a letter or digit, at most 64 characters.
- Lesson ids are unique across the whole program, and so are card ids. Chapter ids are unique among chapters.
- A chapter id equals its file name without `.yaml`.
- Lesson ids must not start with `review-` or `practice-`. Review and practice sessions use those prefixes for their own session ids.
- Ids are permanent. Renaming an id counts as deleting the old item and adding a new one, so every learner loses their progress on it. Make ids describe the content (`what-is-an-embedding`, not `card-7`) so that they never need a rename.

## `program.yaml`

| Field | Required | Default | Rule |
|---|---|---|---|
| `schema_version` | yes | | Must be `1`. |
| `title` | yes | | Course title, shown in the app. |
| `description` | no | `""` | One or two sentences, shown in the app. |
| `language` | no | `en` | Language tag of the card text (`en`, `de`, `pt-BR`, ...). It sets the HTML `lang` of the cards and has no effect on grading. |
| `exercises` | no | see below | Exercise kinds for every chapter, unless a chapter overrides them. |
| `schedule` | no | see below | Spaced-repetition settings. |
| `chapters` | yes | | Chapter ids in course order. Not empty, no duplicates. |

`exercises` takes four switches. `flash` is always on and cannot be listed. See [exercises.md](exercises.md) for what each kind does.

| Key | Default | Meaning |
|---|---|---|
| `triage` | `true` | A new card is first shown with its answer, and the learner says "I know" or "Don't know". With `false`, new cards start at their primary check. |
| `choice` | `true` | Pick the right answer among four buttons. |
| `cloze` | `true` | Fill a gap cut out of the answer, choosing from buttons. |
| `assemble` | `false` | Rebuild the answer from its shuffled words. |

`schedule`:

| Key | Default | Rule |
|---|---|---|
| `desired_retention` | `0.9` | 0.7 to 0.99. The probability of recall the scheduler aims for when a review is due. |
| `max_interval_days` | `365` | 1 to 36500. The longest gap between two reviews. |
| `timezone` | `UTC` | An IANA time zone name, such as `Europe/Berlin`. |
| `day_starts_at_hour` | `4` | 0 to 23. Reviews before this hour still count for the previous learning day. |

## Chapter files: `chapters/<chapter-id>.yaml`

| Field | Required | Default | Rule |
|---|---|---|---|
| `id` | yes | | Equals the file name without `.yaml`. |
| `title` | yes | | Shown in the app. |
| `exercises` | no | `{}` | Overrides for this chapter. List only the kinds that differ from `program.yaml`; the rest are inherited. |
| `lessons` | yes | | At least one lesson, in course order. |

Lesson fields:

| Field | Required | Rule |
|---|---|---|
| `id` | yes | Unique across the program; no `review-` or `practice-` prefix. |
| `title` | yes | Shown in the app. |
| `cards` | yes | 1 to 10 cards. A lesson is meant to be one short sitting, so this is a fixed limit, not a setting. |

## Card fields

| Field | Required | Default | Rule |
|---|---|---|---|
| `id` | yes | | See [Identifiers](#identifiers). |
| `prompt` | yes | | The question on the front of the card. |
| `prompt_variants` | no | `[]` | Other wordings of the same question. Each step shows the prompt or one of its variants, chosen by the step id, so a reload shows the same wording. |
| `answer` | yes | | The full answer on the back of the card. `cloze` cuts its gap out of this text, and `assemble` asks for exactly this text. |
| `option` | no | `answer` | The text of the correct `choice` button, and the default answer key. Keep it short. |
| `distractors` | no | `[]` | Wrong answers for `choice` (and, when the word counts fit, for `cloze`). Exactly 3, or leave the field out. |
| `accepted` | no | `[option]` | Answer keys. `cloze` cuts the first key that qualifies out of `answer`; with `key_mode: all_of`, every key must appear in `answer`. If present, it must not be empty. |
| `key_mode` | no | `any_of` | `any_of`: any key is the answer. `all_of`: the answer is the whole set of keys (an enumeration). Such a card never gets a `cloze`, because a single gap would leave the other elements on screen. |
| `hint` | no | `""` | A nudge shown on the question side when the learner asks for it, or always if they turn hints on in Settings. Must not give the answer away. |
| `note` | no | `""` | Context shown on the back of the card next to the answer: why it is true, a common confusion, an example. |
| `tags` | no | `[]` | Free labels for your own organisation. Stored with the card; the app does not use them yet. |
| `source` | no | `""` | Where the fact comes from (`Original text`, `Course notes, week 3`, `Textbook ch. 4, p. 81`). Stored with the card; not shown to learners. |

Cross-field rules, checked for every card:

- Distractors are compared after normalization: lowercase, accents removed, whitespace collapsed. They must be distinct from each other and from `option`, and none may equal `answer`, `option` or any `accepted` key.
- With `key_mode: all_of`, every key in `accepted` must appear in `answer` (on word boundaries, ignoring case and accents).
- Every card must have at least one closed exercise its chapter enables and its data supports (the exercise gate below).

Whitespace-separated pieces count as words, with punctuation attached: `Paris.` is one word and `state-of-the-art` is one word.

## Which exercises a card supports

| Kind | The card supports it when |
|---|---|
| `triage`, `flash` | always |
| `choice` | it has 3 `distractors` |
| `cloze` | `key_mode` is `any_of`; a key from `accepted` (by default `option`) appears in `answer` on word boundaries; at least 3 words of `answer` remain outside the key; the rest of the answer does not contain the key again; and at least 2 distractors have the same number of words as the key |
| `assemble` | `answer` has 4 to 8 words |

The buttons of a cloze gap are the key plus the card's own distractors that have the key's word count. In practice: **put `option` word for word inside `answer`, and give every distractor the same number of words as `option`.** The card then supports both `choice` and `cloze`.

A card's *rungs* are the closed kinds (`choice`, `cloze`, `assemble`, in that order) that its chapter enables and the card supports. The first rung is the card's primary check. [exercises.md](exercises.md) describes how a lesson uses them.

## What `make validate` checks and prints

Errors (exit code 1) start with `error:` and name the file, lesson and card:

```
error: chapters/basics.yaml: first/capital-of-france: distractors: provide exactly 3 or omit the field (got 2)
```

Every file is checked on its own, so one run lists the problems of all files. Program-wide checks (unique lesson and card ids across chapters, the exercise gate) run once `program.yaml` is valid, over the chapters that loaded; fixing one error can therefore reveal more:

- **Exercise gate.** Every chapter enables at least one of `choice`, `cloze`, `assemble`, and every card has at least one rung. A card with no rung fails with a fix to apply:
  `error: foundations/tokens/what-is-a-token: no enabled closed exercise is possible — add 3 distractors to enable choice, or enable assemble`

On success (exit code 0) it prints a coverage table: per chapter, how many cards support each closed exercise, whether or not the chapter enables it. Use it to decide which kinds to enable.

```
chapter      cards  choice  cloze  assemble
foundations     12      12     12         9
training         9       9      9         3
using-llms      12      12     12         1
```

Warnings start with `warning:` and do not fail the run:

- `option has N words but the longest distractor has M; the length gives the right answer away`: the option has more than twice as many words as the longest distractor.
- `<kind> is enabled but no card in this chapter supports it`: the switch has no effect in that chapter.

`--quiet` prints only warnings and errors. A content directory other than `content/` can be passed as an argument: `.venv/bin/python scripts/validate_content.py path/to/content`.

## Lifecycle: what happens when content changes

The app reads `content/` when it starts, validates it and then updates the stored course in place, keyed by id. `make dev` restarts the backend whenever a YAML file in `content/` changes. `make serve` needs a restart. A Docker image contains its content, so production picks up a change with the next deploy.

| Change | Effect on learners |
|---|---|
| A new chapter, lesson or card | Added. New cards are new for everyone. |
| `prompt`, `prompt_variants`, `hint`, `note`, `tags`, `source`; titles; the order of chapters, lessons or cards; moving a card to another lesson | Progress is kept. |
| The order of `distractors` or `accepted` | Progress is kept. Only the set of values counts. |
| `answer`, `option`, the set of `distractors`, the set of `accepted`, `key_mode` | The card becomes a different check. Its schedule is dropped for every learner, and it comes back in its lesson as a new card, so Home shows that lesson as having open work again. Answers to steps issued before the edit are rejected as stale, and older answers no longer count towards readiness. The history is kept. Reverting the edit later also starts the card over. If `option` is left out, editing `answer` changes the option too. |
| `exercises` in `program.yaml` or a chapter | Applies to the steps served from now on; no schedule is reset. An open session continues, and a step that is no longer valid is rejected as stale and skipped. `triage` affects only cards a learner has not met yet. |
| A card, lesson or chapter removed from the files | Retired: no longer shown in lessons, reviews or practice. History is kept. Adding the same id back restores it with the learner's progress. |
| An id renamed | The old item is retired and a new one is added. Progress on it is lost for everyone. |

The fingerprint of the loaded program (`program_version`, 12 hex characters) is reported by `GET /api/health`.

## Complete example

A minimal course that passes `make validate`. It shows a card with every field, an enumeration with `all_of`, and a card that supports only `assemble`.

```yaml
# content/program.yaml
schema_version: 1
title: The solar system
description: The planets and what makes each of them different.
language: en
exercises:
  triage: true
  choice: true
  cloze: true
  assemble: false
schedule:
  desired_retention: 0.9
  max_interval_days: 365
  timezone: UTC
  day_starts_at_hour: 4
chapters: [planets]
```

```yaml
# content/chapters/planets.yaml
id: planets
title: Planets
# The answers here are short sentences, so learners can also rebuild them.
exercises: {assemble: true}
lessons:
  - id: planet-basics
    title: Planet basics
    cards:
      # Supports choice, cloze ("Jupiter" cut out, three one-word lures) and assemble (5 words).
      - id: largest-planet
        prompt: Which planet is the largest in the solar system?
        prompt_variants:
          - Which planet has the greatest size and mass?
        answer: Jupiter is the largest planet.
        option: Jupiter
        distractors: [Saturn, Neptune, Earth]
        hint: It is a gas giant with a storm called the Great Red Spot.
        note: Jupiter has more than twice the mass of all the other planets combined.
        tags: [planets]
        source: Original text
      # An enumeration: all four keys must appear in the answer, so there is no cloze.
      # At 9 words the answer is too long for assemble, so choice is the only rung.
      - id: inner-planets
        prompt: Which four planets are the rocky inner planets?
        answer: The inner planets are Mercury, Venus, Earth and Mars.
        option: Mercury, Venus, Earth and Mars
        distractors:
          - Mercury, Venus, Mars and Jupiter
          - Venus, Earth, Mars and Ceres
          - Earth, Mars, Jupiter and Saturn
        accepted: [Mercury, Venus, Earth, Mars]
        key_mode: all_of
        source: Original text
      # No distractors: only assemble is possible, so the chapter must enable it.
      - id: planets-orbit-the-sun
        prompt: What do the planets of the solar system orbit?
        answer: The planets orbit the Sun.
        source: Original text
```

For this course `make validate` prints:

```
chapter  cards  choice  cloze  assemble
planets      3       2      1         2
```
