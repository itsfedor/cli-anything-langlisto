---
name: "cli-anything-langlisto"
description: "Use when migrating a teacher's students, classes, schedule or lesson materials onto LangListo (app.langlisto.com) — the ESL platform — or building material lessons there programmatically."
---

# cli-anything-langlisto

CLI + agent workflow for the **LangListo teacher workspace** (app.langlisto.com).
Drive students, classes, schedule, courses ("books") and lesson materials from
the command line by your agent — for a real migration or everyday prep.

Install: `sh scripts/setup.sh` (ships its own wheel) or
`pipx install git+https://github.com/itsfedor/cli-anything-langlisto`.
Docs: repo README + `references/lesson-spec.md` + `references/troubleshooting.md`.

## Hard rules

1. **Login is the human's step.** The user runs `cli-anything-langlisto login`
   in their own terminal (password typed hidden, never stored; the tool caches
   only the token in `~/.langlisto/session.json`, mode 600). Never ask for or
   accept the password in chat; never paste student passwords into chat either
   — use sign-in links (`students creds --link`) or a file the user handles.
2. **Additive only.** The CLI creates and edits; it exposes no deletes for
   students, classes or content. The only destructive command is
   `schedule rm` — use it only when the user explicitly asked to remove that
   lesson.
3. **Dry-run first for materials.** `materials build SPEC --dry-run` renders
   the full plan offline. Show it to the user, adjust, then run the real build.
   Builds are resumable via the state file — if something interrupts, re-run
   the same command; it continues instead of duplicating.
4. **Be a gentle client.** Keep the default pacing (~0.35 s between calls);
   for big batches raise it (slower), don't lower it. The platform is
   rate-limited (DataDome).
5. **Respect the account's own data.** Every request runs under the user's
   login against their account; never touch other teachers' content.
6. **Content rights are the user's call.** If transferring third-party lesson
   content, note the responsibility once; do not editorialize.

## Command map

- `doctor` — setup + session + live check (run first, fail-closed).
- `whoami` — signed-in account.
- `students list | add --name ... [--email|--domain] | creds <name> [--link] | tz <name|--all> --creds-file f.tsv --tz Area/City`
- `classes list | create --name ... --kind individual|group|marathon [--student email]... | show <name>`
- `schedule list [--days N] | add --class ... --at "YYYY-MM-DD HH:MM" --duration 60 [--tz] | move --lesson ID --at ... | rm ID`
- `units list <class> | add --class ... --title ... [--current] | current --class ... --unit ID`
- `courses list | create --title T [--description-file d.html] [--folder ID] | cover <course> --file cover.png | info <course>` — courses are the "books" on the Materials shelf.
- `folders list | create --title ... | move --folder ID --course COURSE_ID...`
- `materials build SPEC.json [--course T] [--dry-run] [--pace N]` — build/resume a lesson; `materials tree <course>` — structure report.
- `raw METHOD /path [--body '{...}']` — escape hatch.
- Global `--json` before the subcommand for machine output.

## Typical flows

**Migrate students + classes + schedule (e.g. from a spreadsheet):**
1. `students add --name "..."` per student (or `--email` when known). Placeholder
   emails: `--domain yourdomain.com` or env `LANGLISTO_EMAIL_DOMAIN` —
   retrievable later with `students creds`.
2. `classes create` (individual/group) adding students by email.
3. `schedule add --class ... --at "2026-11-03 14:00"` — times are entered as
   wall time in `--tz` (default `Europe/Istanbul`; set `LANGLISTO_TZ` env to the
   account's timezone). Conflicts are reported by the API and shown.
4. `students tz` sets each student's personal timezone (needs a creds TSV —
   one row per student: `name<TAB>email<TAB>password`).

**Build a lesson material:** craft a lesson JSON (schema: references/lesson-spec.md)
→ `materials build spec.json --course "B1" --dry-run` → review → run without
`--dry-run`. Media (images, audio) referenced by local paths are uploaded to
the platform automatically.

**Design a shelf of books:** `folders create --title "Series X"` →
`courses create --title "Book A"` (+`folders move`) → `courses cover ...`
→ build lessons inside with `materials build`.

## Files

- `references/lesson-spec.md` — the lesson JSON schema the builder consumes.
- `references/troubleshooting.md` — login quirks, pacing, timezones, media,
  known platform limits.
