# Lesson spec format (`materials build`)

A lesson spec is one JSON file that describes a whole material lesson:
the course (book) it lives in, the lesson title, and sections of items.
`cli-anything-langlisto materials build spec.json` maps each item onto the
platform's native exercise type, uploads local media, sets answer keys and
teacher notes, and records progress in `spec.json.state.json` (resumable —
re-run the same command to continue).

## Top level

```json
{
  "lesson_title": "At the Hotel",
  "course": "B1",
  "sections": [ { "id": "s1", "title": "Warm-Up", "items": [ ... ] } ]
}
```

- `lesson_title` — the lesson (unit) name; created if missing in the course.
- `course` — course title or id (a book on the Materials shelf; created if
  missing). `--course` on the CLI overrides it.
- `sections[].id` — your stable id (used for resume); `title` is shown to
  students.

## Items

Every item has `id` (stable), `type`, and `prompt_html` (the student-facing
copy, basic HTML: `<b> <i> <br> <p> <ul> <table>`).

| type | extra fields | becomes on the platform |
|---|---|---|
| `info` | `media`: `{image, audio, video_url}` | short text → note; long text → article; image → image block; audio/video URL → media player |
| `open` | `model_answer` (optional) | essay prompt; the model answer lands in hidden teacher notes |
| `match` | `left`: `[{id, html}]`, `correct`: `{id: text}` | match-pairs exercise |
| `gap_text` | `gaps`: `{"1": {options?, answer:[...]}, ...}` with `[[1]]` markers in `prompt_html` | typed gaps (a "Box: …" line is added automatically when options exist) |
| `short_answer` | `correct`: `["variant", ...]`, `number` | typed gap after the prompt |
| `choice` | `options: [...]`, `correct: 0` | quiz question (single answer) |
| `multi_choice` | `options: [...]`, `correct: [0, 2]` | quiz question (multiple answers) |

Consecutive `choice`/`multi_choice` items are automatically grouped into ONE
quiz exercise (in original order). Everything else becomes its own exercise.

### Media

`media.image` / `media.audio` accept a public URL OR a local path (absolute,
or relative to the spec file / `--media-dir`). Local files are uploaded to the
platform's own storage and the returned URL is saved into the state file.

### Example

```json
{
  "lesson_title": "Hotel Check-in",
  "course": "B1",
  "sections": [
    {"id": "warm", "title": "Warm-Up", "items": [
      {"id": "w1", "type": "open", "prompt_html": "Tell about your last hotel stay.",
       "model_answer": "Free practice — sample answer for the teacher."},
      {"id": "w2", "type": "choice", "prompt_html": "I booked a ___ room.",
       "options": ["double", "twice", "duo"], "correct": 0}
    ]},
    {"id": "practice", "title": "Practice", "items": [
      {"id": "p1", "type": "gap_text",
       "prompt_html": "She [[1]] the bill in cash.",
       "gaps": {"1": {"options": ["paid", "payed"], "answer": ["paid"]}}},
      {"id": "p2", "type": "short_answer", "number": 2,
       "prompt_html": "How much was the deposit?", "correct": ["50", "fifty"]}
    ]}
  ]
}
```

## Platform behaviors worth knowing

- Gap exercises are built as **typed** gaps: the platform only allows the
  dropdown "word bank" flavor to be configured inside a live classroom, not in
  materials — so specs carry a `"Box: …"` hint line derived from `options`.
- Video/audio items require the media URL to be stored with a clip entry; the
  builder does this correctly (empty clip lists silently drop the URL).
- Answers on materials are stored with `correct_indices: [0]` per gap; that is
  the canonical shape the platform expects for typed gaps.
- Re-running a build NEVER duplicates: already-created sections/exercises are
  looked up in the state file and skipped. To rebuild one item, remove its id
  from the state file first (or delete the exercise in the platform UI).
