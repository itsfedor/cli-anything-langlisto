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
- Don't: run destructive deletes against content you didn't create;
  `schedule rm` is for lessons the teacher explicitly asked to remove.
