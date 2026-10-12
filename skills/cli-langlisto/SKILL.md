---
name: "cli-langlisto"
description: "Use when migrating a teacher's students, classes, schedule or lesson materials onto LangListo (app.langlisto.com) — the ESL platform — or building material lessons there programmatically."
---

# cli-langlisto

CLI + agent workflow for the **LangListo teacher workspace** (app.langlisto.com).
Drive students, classes, schedule, courses ("books") and lesson materials from
the command line by your agent — for a real migration or everyday prep.

Install: `sh scripts/setup.sh` (ships its own wheel) or
`pipx install git+https://github.com/itsfedor/cli-langlisto`.
Docs: repo README + `references/lesson-spec.md` + `references/homework.md` +
`references/troubleshooting.md`.

## Hard rules

1. **Login is the human's step.** The user runs `cli-langlisto login`
   in their own terminal (password typed hidden, never stored; the tool caches
   only the token in `~/.langlisto/session.json`, mode 600). Never ask for or
   accept the password in chat; never paste student passwords into chat either
   — use sign-in links (`students creds --link`) or a file the user handles.
2. **Destructive commands are few and explicit.** `schedule rm`,
   `exercises delete`, `sections delete` (also drops students' answers),
   `books purge-starter` (untitled + empty lessons only) and
   `students unenroll` (drops the membership link, not the account) — run them
   only when the user explicitly asked. Homework writes are reversible
   (`choose`/`remove`/`clear`, addressed by source id). Everything else is
   additive.
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
- `students list | add --name ... [--email|--domain] | creds <name> [--link] | tz <name|--all> --creds-file f.tsv --tz Area/City | patch <name> [--email ...] | unenroll <name> --class ... | verify <name> --class ...` — `verify` exits 1 when the student is not a member.
- `classes list | create --name ... --kind individual|group|marathon [--student email]... | show <name>`
- `schedule list [--days N] | add --class ... --at "YYYY-MM-DD HH:MM" --duration 60 [--tz] | move --lesson ID --at ... | rm ID`
- `units list <class> | add --class ... --title ... [--current] | current --class ... --unit ID | cover set --unit UID [--file cover.png|--url URL] | cover clear --unit UID` — unit cover is a lesson's (unit's) image; book covers are `courses cover`.
- `sections list --unit UID [--class ...] | reorder --unit UID --ids sec2,sec1 | delete --section SID [--unit UID]` — reorder follows array order; place the banner first with `exercises order`.
- `courses list | create --title T [--description-file d.html] [--folder ID] | cover <course> --file cover.png | info <course>` — courses are the "books" on the Materials shelf.
- `books modules list|add|rename <course> ... | lessons order <course> --ids id1,id2 [--file order.json] | purge-starter <course> [--dry-run]` — a module is the book's table of contents.
- `folders list | create --title ... | move --folder ID --course COURSE_ID...`
- `materials build SPEC.json [--course T] [--dry-run] [--pace N]` — build/resume a lesson; `materials verify COURSE [--lesson L] [--min-note-coverage N] [--state f] [--strict] [--grading]` — the LIVE quality gate (read-only; exit 1 on problems); `materials run SPECS [--state f] [--only slug] [--retry-failed]` — mass-run a catalog, resumable; `materials status --state f.json ...` — run-state summary; `materials tree COURSE` — lessons → sections → exercises (with types).
- `exercises show ID | notes ID --text ... [--visible-to-student|--from-file f] | order --section SID --ids a,b,c | patch ID --suffix note --body-file f.json | delete ID`
- `homework list --class ... [--student ...] | available --class ... --unit ... [--from-material MU] | choose --class ... --unit ... --exercise SRC... [--dry-run] | assigned | show | remove | clear | review | accept | return --comment ...` — the room's «Homework» tab; exercises by SOURCE id, every call keyed by (classroom, unit).
- `preview open [--class C] [--unit U] [--tab lesson|homework]` — demo-student (student view) magic-link URL; single-use, never stored.
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
the platform automatically. After the build, `materials verify "B1"` is the
quality gate — run it before students see the lesson.

**Design a shelf of books:** `folders create --title "Series X"` →
`courses create --title "Book A"` (+`folders move`) → `courses cover ...`
→ build lessons inside with `materials build`.

## Files

- `references/lesson-spec.md` — the lesson JSON schema the builder consumes
  (v2 vocabulary: 18 extra types, quality features, lint rules).
- `references/homework.md` — the classroom homework model (choose / remove /
  accept / return, source ids vs copy ids).
- `references/troubleshooting.md` — login quirks, pacing, timezones, media,
  known platform limits.
