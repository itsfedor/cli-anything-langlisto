# Lesson spec format (`materials build`)

A lesson spec is one JSON file that describes a whole material lesson:
the course (book) it lives in, the lesson title, and sections of items.
`cli-langlisto materials build spec.json` maps each item onto the
platform's native exercise type, uploads local media, sets answer keys and
teacher notes, and records progress in `spec.json.state.json` (resumable —
re-run the same command to continue).

**v2 vocabulary**: every working LangListo material type is now expressible —
the original 11 plus `sort_columns`, the ordering family, the word-image trio,
`image_carousel`/`gif_animation`, `diagram`, `interactive_video`,
`audio_match_pairs`, `wordlist`, `link`, `photo_answer`, `divider`,
`timed_test` (all live-verified — see `cli-lab/type_catalog.json`).

## Top level

```json
{
  "lesson_title": "At the Hotel",
  "course": "B1",
  "lesson_description": "Plain-text description (renders pre-line; tags show as code)",
  "sections": [ { "id": "s1", "title": "Warm-Up", "items": [ ... ] } ]
}
```

- `lesson_title` — the lesson (unit) name; created if missing in the course.
  Keep it **unique within a course**: when the state file is missing, a build
  ADOPTS an existing lesson with the same title (crash recovery) — so two specs
  sharing one title silently merge into a single lesson with duplicated
  sections. Disambiguate sibling variants, e.g. `"... (B1+)"`.
- `course` — course title or id (a book on the Materials shelf; created if
  missing). `--course` on the CLI overrides it.
- `lesson_description` — optional, PLAIN TEXT (the platform renders it
  `pre-line`; HTML tags would show as code — the linter warns).
- `sections[].id` — your stable id (used for resume); `title` is shown to
  students.
- `sections[].homework` — optional flag. A section marked `"homework": true`
  is **not created as a regular section**: its items are authored into the
  unit's *workbook-section* — the editor's «Exercises for homework» bucket
  students never see in the lesson flow (they appear only once you assign
  them). `title` is ignored for such a section; there is exactly ONE bucket
  per unit, so flag at most one section.

## Items — v1 vocabulary

Every item has `id` (stable), `type`, and `prompt_html` (the student-facing
copy, basic HTML: `<b> <i> <br> <p> <table>`).

| type | extra fields | becomes on the platform |
|---|---|---|
| `info` | `media`: `{image, audio, video_url, caption?}` | short text → note; long text → article; image → image block; audio/video URL → media player |
| `open` | `model_answer` (optional) | essay prompt; the model answer lands in hidden teacher notes |
| `match` | `left`: `[{id, html}]`, `correct`: `{id: text}` | match-pairs exercise |
| `gap_text` | `gaps`: `{"1": {answer\|correct, options?, hint?, no_correct?, sample_answer?}}` with `[[1]]` markers | typed gaps (a "Box: …" line is derived from `options`) |
| `short_answer` | `correct`: `["variant", ...]`, `number`, `hint?` | typed gap after the prompt (variants auto-expanded) |
| `choice` | `options: [...]`, `correct: 0` | quiz question (single answer) |
| `multi_choice` | `options: [...]`, `correct: [0, 2]` | quiz question (multiple answers) |
| `voice` | `heading`, `prompt_html`, `time_limit_seconds?`, `model_answer?` | voice recording; model answer → teacher note |
| `true_false` | `statements`: `[{text, answer: "true"/"false"/"not_stated"}]`, `not_stated_enabled` (optional) | true/false quiz; `not_stated` answers need the flag |
| `test` | `questions: [{prompt, options: [str], correct: [idx]}]` | one multi-question quiz |

Consecutive `choice`/`multi_choice` items are automatically grouped into ONE
quiz exercise (in original order). Everything else becomes its own exercise.

## Items — v2 vocabulary (all live-verified)

| type | spec fields (author-friendly) | platform PATCH |
|---|---|---|
| `select_word_gap` | same as `gap_text` | `/drag-word-gap` |
| `sort_columns` | `columns: [str]`, `words: [{text, column}]`, `no_correct?` | `/word-columns` |
| `sentence_order` / `text_order` | `items: [str]`, `hint?` | `/ordering` (listed order = correct answer) |
| `unscramble` | `words: [{hint?, tiles: [str]}]` | `/ordering` |
| `enter_word_image` / `drag_word_image` / `select_word_image` | `items: [{image, word, alternatives?, options?}]` | `/word-image` |
| `image_stack` | `items: [{image, caption?}]` | `/image-block` |
| `image_carousel` | same as `image_stack`, **≥2 items** | `/image-block` |
| `gif_animation` | `items: [{image (.gif), caption?}]` | `/image-block` |
| `diagram` | `image`, `description?`, `points: [{x,y}]`, `cards: [{text, image?, point_idx}]` | `/diagram` |
| `interactive_video` | `video_url`, `description?`, `checkpoints: [{time_seconds, prompt, options: [{text, is_correct}]}]` | `/interactive-video` |
| `audio_match_pairs` | `pairs: [{left, right, audio?}]` | `/match-pairs` |
| `wordlist` | `words: [{word, translation}]` | `/wordlist` |
| `link` | `url`, `heading?`, `description?` | `/link` |
| `photo_answer` | `heading?`, `description` | `/photo-answer` |
| `divider` | *(no fields)* | none (create only) |
| `timed_test` | `questions` (same shape as `test`) + `time_limit_seconds` | `/test` |

Notes:

- `image` / `audio` / `video_url` values accept a public URL OR a
  local path (absolute, or relative to the spec file / `--media-dir`); local
  files are uploaded to the platform's CDN and the URL is stored in state.
- Coordinates (`x`, `y`, `time_seconds`) are numbers as the platform expects;
  `diagram` `x`/`y` are 0..1 fractions.
- Every item of every type may carry `teacher_note` (student-invisible).
- `heading` is supported on all types; give new types a heading — the QA
  verifier uses it to judge "content present".

## Homework sections (the workbook bucket)

Flag one section of the spec with `"homework": true` to author the lesson's
homework. Its items are built into the unit's **workbook-section** — the
editor's «Exercises for homework» bucket ("Prepare the homework exercises
for this lesson here. Students only see them once you assign them as
homework"). No regular section is created, no title is set, and the items
stay out of the lesson flow: they do not appear in the unit's section list
nor in `…/homework/lesson-exercises`.

- The builder fetches the bucket with `GET /units/{unitId}/workbook-section`
  (get-or-create) after the lesson exists; the state file records the routing
  per spec section as `{"kind": "workbook", "section_id": …}` — re-runs stay
  idempotent.
- Dry-run plans show the section as `(homework bucket)`.
- The bucket is per-unit and invisible to students until assigned. Assign it
  in the classroom room with `homework choose` (picker group «Exercises for
  homework» = `GET /units/{id}/homework/workbook-exercises`), preview the
  bucket with `homework bucket --unit <uid>`.
- Migrating a lesson that still keeps homework as a visible section (the
  OLD model): `homework prepare --unit <uid> --section Homework` moves the
  whole section into the bucket and deletes the emptied section (see
  `references/homework.md`).
- One flagged section per spec; a second one would build into the same
  bucket (harmless but confusing — the state keys stay separate).

## Quality features

### Gap hints

`gaps: {"1": {answer: [...], hint: "3rd person singular"}}` — `hint` lands in
the platform's `gaps[].hint` field (previously always empty across the books).

### Answer variants (auto-expansion)

`answer` (or `correct`) is a list. Each authored variant is auto-expanded by
the engine (`core/variants.py`) into the accepted classes: case
(`goes`→`Goes`), trailing punctuation, typographic apostrophes, contractions
(`don't`↔`do not`, `I am`↔`I'm`) and number words (`50`↔`fifty`,
`twenty-one`↔`21`). The first item stays the authored form (`correct_indices:
[0]` stays meaningful). Only meaning-preserving rewrites are generated — a
wrong form is never accepted.

- Opt out per item: `"expand_variants": false`, or per gap: `"variants": false`.
- The linter flags single-variant keys (`gap_single_variant`): `info` when
  auto-expansion covers it, `warn` when expansion is disabled.

### Free gaps (no key) + sample answers

```json
{"1": {"no_correct": true, "sample_answer": "I walk to school."}}
```

Free gaps ship as `no_correct: true` with the required placeholder answers item
`["—"]`; the `sample_answer` is appended to the exercise's teacher note.
(The server normalises `correct_indices` → `[0]`; `no_correct: true` is what
matters — same shape as the finished «Small Talk Training» book.)

### Teacher notes on ALL types

`teacher_note` works on every type (`PUT /exercises/{id}/teacher-notes`);
`model_answer` on `open`/`voice` is merged into the same note. Free-gap
sample answers join the note automatically.

### Media captions

Image items (`image_stack`, `image_carousel`, `gif_animation`) take
`caption` (→ platform `description`). The linter warns when a caption is
missing (`allow_no_caption: true` on the item to silence it).

## Render-safe lint (pre-build gate)

Every build runs `core/spec_lint.py` first, which codifies the verified render
model and authoring-quality rules. Findings are recorded in the state
(`lint`) and echoed in the plan. `error` findings also become build warnings;
pass `lint_fail_on=["error"]` (engine / CLI flag) to ABORT before any write.
Spec-level equivalent: `"lint": {"fail_on": ["error"]}`.

Rule IDs: `raw_newline`, `ul_ol_stripped`, `h_tag_dropped`, `img_stripped`,
`style_stripped`, `font_color_stripped`, `nbsp_entity`, `gap_marker_mismatch`,
`leftover_marker`, `gap_no_key`, `gap_single_variant`, `free_gap_no_sample`,
`box_mentioned_no_options`, `image_missing`, `image_missing_caption`,
`carousel_too_few_items`, `gif_not_gif`, `sort_column_range`,
`diagram_point_range`, `unscramble_no_tiles`, `iv_no_checkpoints`,
`iv_no_correct`, `timed_test_no_limit`, `voice_no_heading`, `link_no_url`,
`unsupported_type`, `duplicate_item`, `duplicate_item_id`,
`lesson_description_html`.

### Box: line (H2 verdict — word bank is classroom-only)

Word-bank `placements` cannot be authored on materials (`PUT …/placements` →
`400 answer_scope`, inline placements are accepted but not persisted — H2
REFUTED). The canonical workaround stays: when a gap item has `options`, the
builder appends a derived `<p><b>Box:</b> a · b</p>` line. The linter's
`box_mentioned_no_options` rule is deliberately conservative (phrases like
"from the box"/"word bank" only): a bare "box" inside a sentence — e.g.
«￼ box do you prefer» — must NOT trigger it.

## Duplicate guard

- **Build lockfile**: while a wet build runs, `<state>.lock` holds its PID; a
  second builder on the same spec refuses to start (stale locks from dead PIDs
  are taken over automatically). `"build_lock": false` in the spec disables it.
- **Duplicate detector**: normalized-text hashes (`core/dedup.py`,
  `find_duplicates`) — the builder records item hashes in the state and warns
  on `duplicate content` (catches orphan-run double builds); the verify side
  can import the same functions for live payloads (`entries_from_live`).

## Example

```json
{
  "lesson_title": "Hotel Check-in",
  "course": "B1",
  "sections": [
    {"id": "warm", "title": "Warm-Up", "items": [
      {"id": "w1", "type": "open", "prompt_html": "Tell about your last hotel stay.",
       "model_answer": "Free practice — sample answer for the teacher."},
      {"id": "w2", "type": "choice", "prompt_html": "I booked a ___ room.",
       "options": ["double", "twice", "duo"], "correct": 0},
      {"id": "w3", "type": "sentence_order", "heading": "Order", "hint": "Put in order",
       "items": ["We arrived at noon.", "The room was ready.", "We unpacked."]}
    ]},
    {"id": "practice", "title": "Practice", "items": [
      {"id": "p1", "type": "gap_text",
       "prompt_html": "She [[1]] the bill in cash. Also [[2]] here.",
       "gaps": {"1": {"options": ["paid", "payed"], "answer": ["paid"],
                      "hint": "past simple"},
                "2": {"no_correct": true, "sample_answer": "I paid by card."}}},
      {"id": "p2", "type": "select_word_gap", "heading": "Choose",
       "prompt_html": "He [[1]] the bus every day.",
       "gaps": {"1": {"answer": ["misses"], "options": ["miss", "misses"], "hint": "3rd person"}}},
      {"id": "p3", "type": "sort_columns", "heading": "Sort",
       "columns": ["Room", "Reception"],
       "words": [{"text": "pillow", "column": 0}, {"text": "key card", "column": 1}]},
      {"id": "p4", "type": "timed_test", "heading": "Speed quiz", "time_limit_seconds": 90,
       "questions": [{"prompt": "2+2=?", "options": ["3", "4"], "correct": 1}]}
    ]},
    {"id": "hw", "homework": true, "items": [
      {"id": "h1", "type": "short_answer", "number": 1, "prompt_html": "Where did you stay?",
       "correct": ["At a hotel."]},
      {"id": "h2", "type": "voice", "heading": "Record", "prompt_html": "Tell about your stay.",
       "time_limit_seconds": 90}
    ]}
  ]
}
```

## Platform behaviors worth knowing

- Gap exercises are built as **typed** gaps; the derived `Box:` line is the
  canonical hint for a word bank on materials (see the H2 note above).
- Video/audio items require the media URL to be stored with a clip entry; the
  builder does this correctly (empty clip lists silently drop the URL). YouTube
  links become an embedded player on the lesson page.
- Answers on materials are stored with `correct_indices: [0]` per gap; that is
  the canonical shape the platform expects for typed gaps.
- Re-running a build NEVER duplicates: already-created sections/exercises are
  looked up in the state file and skipped. To rebuild one item, remove its id
  from the state file first (or delete the exercise in the platform UI).
- A build whose state file is missing but whose `lesson_title` already exists
  in the course will ADOPT that lesson and append its own sections to it
  (safe for crash resumes — but if two specs share a title you get duplicated
  sections; the build log prints an "adopting it" note). Keep titles unique.
- `divider` is created without a PATCH (it has no content); the QA verifier
  flags it as "unknown type" because it cannot judge an empty item — that is a
  verifier limitation, not a build fault.
- PDF items (`pdf_document`) and `game` remain partial on materials (server
  stores a note stub) — not builders' targets in v2.
