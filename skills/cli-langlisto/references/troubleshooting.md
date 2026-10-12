# Troubleshooting & platform notes

Verified against the live platform (October 2026).

## Login

- `login` posts to `/auth/login` once and caches ONLY the token in
  `~/.langlisto/session.json` (mode 600). The password is never written.
- If login times out or is rejected while the password is definitely right
  (bot protection can be touchy on some networks): sign in via the website,
  open DevTools → Application → Local Storage → `langlisto.access_token`,
  copy the value and run `cli-langlisto login --token <TOKEN>`.
- Token TTL is long (≈1000 days). `logout` removes the cached file.
- Env overrides: `LANGLISTO_TOKEN`, `LANGLISTO_SESSION`, `LANGLISTO_API`,
  `LANGLISTO_EMAIL`/`LANGLISTO_PASSWORD` (scripted machines),
  `LANGLISTO_TZ`, `LANGLISTO_EMAIL_DOMAIN`.

## Rate limits / pacing

The platform sits behind DataDome. Plain API calls with a captured token work
fine, but bulk operations should be paced (the CLI defaults to ~0.35 s between
calls — keep or raise it for big batches). Occasional timeouts are transient:
re-run the command; state files make builds resumable.

## Students

- `students add` uses the invite flow, which creates the account for any new
  address (an invitation email is also sent — delivery is not required).
- Placeholder emails (`<translit-name>@yourdomain`) are fine: the login is the
  email, and it can be changed later.
- `students creds <name>` sets and returns the login + password to hand to the
  student; `--link` also issues a one-time sign-in link (preferred when
  sharing over chat).
- Student timezone: the teacher side `/students/{id}/settings` is not
  available on all plans; set it by signing in as the student (that's what
  `students tz` does — it needs a TSV of `name,email,password`).

## Schedule

- Lesson times go to the API as UTC (`...Z`); the CLI converts wall time from
  `--tz` (default `Europe/Istanbul` — set `LANGLISTO_TZ` to the account's real
  timezone shown in `whoami`).
- Conflicts are checked before creating a lesson and reported (the app's own
  behavior); pass `--no-conflict-check` in the rare case the check itself
  misbehaves.

## Materials & platform limits (learned in production)

- **Dropdown gap banks are classroom-only.** On materials the placements
  endpoint rejects with `answer_scope`/“Answers only exist inside a classroom
  lesson.” The builder therefore uses typed gaps + a `Box:` hint line.
- **Video/audio**: the media URL must be saved together with a non-empty clip
  entry — with an empty clip list the URL silently does not persist.
- **Teacher notes**: valid `kind` values are `text`/`audio`/`image`; a null
  `audio_url`/`image_url` fails validation (send empty strings).
- **Uploads**: `POST /uploads/image` for pictures, `POST /uploads/media` for
  audio/video; multipart with a single field named `file`; response `{url}`.
- Native exercise types seen on the platform: `untimed_test`, `timed_test`,
  `true_false`, `drag_word_gap`, `enter_word_gap`, `select_word_gap`,
  `article`, `text`, `essay`, `sentence_order`, `text_order`, `unscramble`,
  `sort_columns`, `match_pairs`, `wordlist`, `voice_recording`, `note`,
  `video`, `audio`, `image_stack`, `link`, `divider`, `interactive_video`.
  The builder maps onto: note / article / essay / match_pairs /
  enter_word_gap / untimed_test / image_stack / audio / video.
- Courses have covers: `courses cover <course> --file img.png` uploads the
  image and sets it; the course then renders as a book card on the shelf.

## Do / don't

- Do: `--dry-run` first, keep state files in the same folder as the spec.
- Do: work with `--json` and the `raw` command for debugging.
- Do: `--json` is a GLOBAL flag — it goes before the subcommand:
  `cli-langlisto --json materials verify "Book A"`.
- Don't: run destructive deletes against content you didn't create;
  `schedule rm`, `exercises delete` and `books purge-starter` are for content
  the teacher explicitly asked to remove.

## Verify / run / status (v2 commands)

- **`materials verify` exit codes:** `0` clean, `1` problems found, `2` usage
  error. Problems = empty content, missing/blank answers, render-lint hazards
  (`<ul>`, raw `\n`, `<img>` in text, leftover `[[n]]`, U+FFFC ≠ gaps count),
  duplicate items/sections (normalized-text hashes), the
  `--min-note-coverage` gate. Warnings (exit stays `0`, unless `--strict`):
  image without caption, state↔live drift, unsupported grading spot-checks.
- **Verify is read-only by default** — it reads the lesson via two bulk GETs
  per lesson/section; `GET /sections/{sid}/exercises` returns FULL items
  (gaps with answers, test options with `is_correct`, teacher_notes), so no
  per-exercise drill-down is needed. `--deep` still fetches each exercise.
- **`--grading`** sends `POST /exercises/{id}/trial-check` spot-checks — this
  records a trial attempt (the editor's own "check" mechanism), so it is
  opt-in. Verified shape for test types (2026-10-13):
  `{"answer":[{"question_idx":0,"option_idx":1}],"history":[]}` →
  `attempts.attempts[].correct`. **Gap types return `500 internal_error` on
  every probed shape** — they are reported as `unsupported` (warning), not as
  your bug.
- **Duplicate guard**: item texts are normalized (tags stripped, whitespace
  collapsed, casefolded) and hashed; groups ≥2 long-enough texts are flagged,
  plus section pairs with ≥90% identical item-hash sets. Very short texts and
  images (no text) can't be compared this way — an image-level dupe needs an
  `image_url` comparison (not implemented).
- **`materials run` resume semantics:** records per-slug `done` /
  `done_with_warnings` / `failed` in `--state`; `done*` specs are skipped,
  `failed` only re-run with `--retry-failed`; one bad spec doesn't stop the
  batch but the command exits `1`. Keep the run-state file OUTSIDE the specs
  directory or pass it explicitly — a `run-state.json` inside the specs folder
  is filtered out automatically, but it is easier to keep states separate.
- **`materials status`** accepts run-state AND builder `*.state.json` files
  (globs OK, quote them). Builder states show lesson id / sections / exercises
  / warnings; run-states show per-book status counts, failed reasons and
  summed build time.
- **`books purge-starter`** removes only lessons with an empty title AND zero
  sections (the mass-runner semantics). `--dry-run` lists them first; always
  check before running on a fresh book.
- **`books lessons order --ids`** sends `PUT /courses/{id}/lessons/order`;
  use `--file order.json` (`[{"id":…,"module_id":…}]`) for the module-aware
  BGP-style ordering. The command reads the order back and exits `1` on a
  mismatch.
- **`exercises notes`** replaces the exercise's notes (PUT teacher-notes) and
  reads them back; without `--visible-to-student` the note stays teacher-only.
  `exercises order --section <sid> --ids …` puts the first id at position 0
  (use it to place a banner) and reads the order back.

## Preview (student view)

- **`preview open`** wraps the platform's «Demo preview»: it prints a
  `demoview.langlisto.com` magic-link URL (the same call as «Test student» →
  «Log in as a student»). Open it in YOUR browser; the CLI never stores it.
  The URL is single-use — request a fresh one when it expires.
- No real student is ever touched and nothing is emailed; the demo student is
  the platform's own synthetic account.

## Students patch / unenroll / verify

- **409 `student_email_taken`** — the target address already belongs to a USER
  account (usually the student self-registered). `students patch` prints the
  fix path: either detach + re-attach the right account
  (`students unenroll <name> --class <class>` then `classes create --student
  <email>`), or keep the existing account. A self-registered account keeps its
  own password (creds commands may be forbidden on it).
- **`students unenroll`** removes only the classroom membership link; the
  student account and other classes stay. `students verify --class` exits `1`
  when the student is not a member (good for scripts/CI checks).
