"""cli-anything-langlisto — CLI for the LangListo teacher workspace.

Commands: login · logout · doctor · whoami · students · classes · schedule ·
units · courses (books) · folders · materials (build lesson specs) · raw.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import click

from . import __version__
from .core.api import Langlisto, login_public
from .core.client import LanglistoError, LanglistoClient, SESSION_FILE, save_session, load_token
from .core.materials import build_material
from .utils.timeparse import parse_when, to_local, default_tz, now_utc_iso

DEFAULT_TZ = default_tz()


def err(msg, code=2):
    click.echo(f"✗ {msg}", err=True)
    sys.exit(code)


def out(ctx, data, human):
    if ctx.obj.get("json"):
        click.echo(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        click.echo(human)


def find_class(api, needle):
    classes = api.classrooms_all()
    if not classes:
        err("no classes on the account")
    for c in classes:
        if str(c.get("id")) == str(needle):
            return c
    n = needle.strip().lower()
    exact = [c for c in classes if (c.get("name") or "").lower() == n]
    part = [c for c in classes if n in (c.get("name") or "").lower()]
    pool = exact or part
    if not pool:
        err(f"class not found: {needle!r}. Known: " + ", ".join(repr(c.get("name")) for c in classes[:20]))
    if len(pool) > 1:
        err(f"ambiguous class {needle!r}: " + ", ".join(repr(c.get("name")) for c in pool))
    return pool[0]


def find_student(api, needle):
    students = api.students_all()
    for s in students:
        if str(s.get("id")) == str(needle):
            return s
    n = needle.strip().lower()
    pool = [s for s in students
            if n in (s.get("name") or "").lower() or n in (s.get("email") or "").lower()]
    if not pool:
        err(f"student not found: {needle!r}. Known: " + ", ".join(repr(s.get("name")) for s in students[:30]))
    if len(pool) > 1:
        err(f"ambiguous student {needle!r}: " + ", ".join(repr(s.get("name")) for s in pool))
    return pool[0]


def find_course(api, needle):
    courses = api.courses_all()
    for c in courses:
        if str(c.get("id")) == str(needle):
            return c
    n = needle.strip().lower()
    pool = [c for c in courses if n in (c.get("title") or "").lower()]
    if not pool:
        err(f"course not found: {needle!r}. Known: " + ", ".join(repr(c.get("title")) for c in courses[:30]))
    if len(pool) > 1:
        err(f"ambiguous course {needle!r}: " + ", ".join(repr(c.get("title")) for c in pool))
    return pool[0]


def lesson_line(l):
    st = l.get("startAt") or l.get("start_at")
    try:
        dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
        human = to_local(dt).strftime("%a %d %b %H:%M")
    except Exception:
        human = str(st)
    return (f"{human}  {l.get('durationMinutes') or l.get('duration_minutes')}m  "
            f"status={l.get('status')}  class={l.get('classroomId') or l.get('classroom_id')}  id={l.get('id')}")


# ---------------------------------------------------------------- cli

@click.group(help="Unofficial CLI for the LangListo teacher workspace.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON.")
@click.version_option(__version__, prog_name="cli-anything-langlisto")
@click.pass_context
def cli(ctx, as_json):
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json
    ctx.obj["api"] = None


def api(ctx):
    if ctx.obj["api"] is None:
        try:
            ctx.obj["api"] = Langlisto()
        except LanglistoError as e:
            err(str(e))
    return ctx.obj["api"]


# ---------------- auth ----------------

@cli.command()
@click.option("--email", default=None, help="Account email (prompted if omitted).")
@click.option("--token", default=None, help="Skip login and cache an existing bearer token.")
@click.pass_context
def login(ctx, email, token):
    """Sign in and cache the session token (password is never stored).

    The password is read interactively by default. For scripted machines you
    can export LANGLISTO_EMAIL / LANGLISTO_PASSWORD instead of passing flags.
    """
    if token:
        save_session(token.strip(), email)
        client = _client_with(token.strip())
        me = client.get("/auth/me")
        click.echo(f"✓ token cached for {me.get('email')} ({SESSION_FILE})")
        return
    email = email or os.environ.get("LANGLISTO_EMAIL") or click.prompt("Email")
    password = os.environ.get("LANGLISTO_PASSWORD")
    if not password:
        password = click.prompt("Password", hide_input=True)
    try:
        tok, user = login_public(email.strip(), password)
    except LanglistoError as e:
        if e.status in (401, 403):
            err(f"login rejected ({e.message}). If the password is right but the "
                "request is blocked, sign in via the website and use "
                "`login --token <TOKEN>` (see references/troubleshooting.md)")
        err(str(e))
    save_session(tok, user.get("email") if user else email)
    name = (user or {}).get("name") or email
    click.echo(f"✓ signed in as {name} — session cached at {SESSION_FILE}")


def _client_with(token):
    return LanglistoClient(token=token)


@cli.command()
def logout():
    """Remove the cached session token."""
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
        click.echo(f"✓ session removed ({SESSION_FILE})")
    else:
        click.echo("(no cached session)")


@cli.command()
def doctor():
    """Check the local setup and the live session."""
    ok = True
    py_ok = sys.version_info >= (3, 9)
    click.echo(f"{'✓' if py_ok else '✗'} python {sys.version.split()[0]} (need >= 3.9)")
    ok &= py_ok
    has_env = bool(os.environ.get("LANGLISTO_TOKEN"))
    click.echo(f"{'✓' if has_env else '·'} LANGLISTO_TOKEN {'set' if has_env else 'not set'}")
    click.echo(f"{'✓' if os.path.exists(SESSION_FILE) else '·'} session file: {SESSION_FILE}"
               f"{'' if os.path.exists(SESSION_FILE) else ' (run `login`)'}")
    try:
        me = LanglistoClient().get("/auth/me")
        click.echo(f"✓ signed in: {me.get('name')} <{me.get('email')}> "
                   f"[{me.get('account_type')}] tz={me.get('timezone')}")
    except LanglistoError as e:
        click.echo(f"✗ live check failed: {e}")
        ok = False
    sys.exit(0 if ok else 1)


@cli.command()
@click.pass_context
def whoami(ctx):
    """Show the signed-in account."""
    me = api(ctx).me()
    out(ctx, me, f"{me.get('name')} <{me.get('email')}> · {me.get('account_type')} · "
                 f"tz {me.get('timezone')} · id {me.get('id')}")


# ---------------- students ----------------

@cli.group()
def students():
    """Manage students."""


@students.command("list")
@click.pass_context
def students_list(ctx):
    items = api(ctx).students_all()
    lines = [f"{s.get('name')} <{s.get('email')}>  id={s.get('id')}  "
             f"classes={[c.get('name') for c in s.get('classrooms', [])]}" for s in items]
    out(ctx, items, "\n".join(lines) if lines else "(no students)")


@students.command("add")
@click.option("--name", required=True, help="Student name")
@click.option("--email", default=None, help="Student email (login).")
@click.option("--domain", default=None, help="Placeholder domain: <translit-name>@<domain> "
                                             "(or env LANGLISTO_EMAIL_DOMAIN).")
@click.option("--lang", default="ru", show_default=True, help="Invitation language (ru/en/es)")
@click.pass_context
def students_add(ctx, name, email, domain, lang):
    a = api(ctx)
    if not email:
        from .utils.names import email_for, default_domain
        domain = domain or default_domain()
        if not domain:
            err("no email given — pass --email, or --domain / LANGLISTO_EMAIL_DOMAIN "
                "to generate a placeholder address")
        email = email_for(name, domain)
    existing = [s for s in a.students_all() if (s.get("email") or "").lower() == email.lower()]
    if existing:
        click.echo(f"= exists: {existing[0].get('name')} <{email}> id={existing[0].get('id')}")
        return
    s = a.student_create(email, name, lang=lang)
    out(ctx, s, f"+ {s.get('name')} <{s.get('email')}> id={s.get('id')}")


@students.command("creds")
@click.argument("student")
@click.option("--link", is_flag=True, help="Also generate a sign-in link")
@click.pass_context
def students_creds(ctx, student, link):
    """Set/show student login + password (and optionally a sign-in link).

    Hand the result to the student directly; avoid pasting passwords into
    shared chats, use --link where possible.
    """
    a = api(ctx)
    s = find_student(a, student)
    creds = a.student_reset_password(s["id"])
    data = {"student": s.get("name"), "id": s["id"], **creds}
    human = f"{s.get('name')}: login={creds.get('login')}  password={creds.get('password')}"
    if link:
        ll = a.student_login_link(s["id"])
        data["login_link"] = ll.get("url")
        data["expires_at"] = ll.get("expires_at")
        human += f"\n  link: {ll.get('url')}"
    out(ctx, data, human)


@students.command("tz")
@click.argument("student", required=False)
@click.option("--tz", "tz_name", default="Europe/Moscow", show_default=True)
@click.option("--all", "all_", is_flag=True, help="Apply to every student listed in --creds-file")
@click.option("--creds-file", default=None, help="TSV with rows: name<TAB>email<TAB>password")
@click.pass_context
def students_tz(ctx, student, tz_name, all_, creds_file):
    """Set a student's timezone (signs in as the student using their password)."""
    a = api(ctx)
    if not creds_file:
        err("--creds-file is required (rows: name<TAB>email<TAB>password)")
    if not os.path.exists(creds_file):
        err(f"no such file: {creds_file}")
    rows = []
    for line in open(creds_file, encoding="utf-8").read().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(tuple(parts))
    if all_:
        todo = rows
    elif student:
        n = student.strip().lower()
        todo = [r for r in rows if n in r[0].lower()]
        if not todo:
            err(f"not in creds file: {student!r}")
    else:
        err("pass a student name or --all")
    for n, lg, pwd in todo:
        try:
            res = a.student_set_timezone(lg, pwd, tz_name)
            got = (res or {}).get("timezone", tz_name)
            click.echo(f"+ {n}: tz={got}")
        except LanglistoError as e:
            click.echo(f"✗ {n}: {e}", err=True)


# ---------------- classes ----------------

@cli.group()
def classes():
    """Manage classes (individual / group / marathon)."""


@classes.command("list")
@click.pass_context
def classes_list(ctx):
    items = api(ctx).classrooms_all()
    lines = [f"{c.get('name')}  [{c.get('kind')}]  id={c.get('id')}  "
             f"students={c.get('student_count', c.get('studentsCount', '?'))}  "
             f"lessons={c.get('lesson_count', '?')}" for c in items]
    out(ctx, items, "\n".join(lines) if lines else "(no classes)")


@classes.command("create")
@click.option("--name", required=True)
@click.option("--kind", type=click.Choice(["individual", "group", "marathon"]), default="individual")
@click.option("--language", default="en")
@click.option("--student", "student_emails", multiple=True, help="Add student by email (repeatable)")
@click.option("--student-name", "student_pairs", multiple=True, help="NAME:EMAIL pair (repeatable)")
@click.pass_context
def classes_create(ctx, name, kind, language, student_emails, student_pairs):
    a = api(ctx)
    existing = [c for c in a.classrooms_all() if (c.get("name") or "").lower() == name.lower()]
    if existing:
        c = existing[0]
        click.echo(f"= class exists: {c.get('name')} id={c.get('id')}")
    else:
        c = a.classroom_create(name=name, kind=kind, language=language)
        click.echo(f"+ class {c.get('name')} [{kind}] id={c.get('id')}")
    added = []
    for em in student_emails:
        try:
            r = a.classroom_add_student(c["id"], em)
            added.append({"email": em, "result": r})
            click.echo(f"  + student {em} → {r.get('name', '')}")
        except LanglistoError as e:
            click.echo(f"  ✗ student {em}: {e}", err=True)
    for pair in student_pairs:
        n, _, em = pair.partition(":")
        try:
            r = a.classroom_add_student(c["id"], em.strip(), n.strip())
            added.append({"email": em, "result": r})
            click.echo(f"  + student {n} <{em}> → {r.get('name', '')}")
        except LanglistoError as e:
            click.echo(f"  ✗ student {n}: {e}", err=True)
    if ctx.obj.get("json"):
        click.echo(json.dumps({"classroom": c, "students_added": added}, ensure_ascii=False, indent=1))


@classes.command("show")
@click.argument("klass")
@click.pass_context
def classes_show(ctx, klass):
    a = api(ctx)
    c = find_class(a, klass)
    st = a.classroom_students(c["id"])
    out(ctx, {"classroom": c, "students": st},
        f"{c.get('name')} [{c.get('kind')}] id={c.get('id')}\n" +
        "\n".join(f"  - {s.get('name')} <{s.get('email')}> id={s.get('id')}" for s in st))


# ---------------- schedule ----------------

@cli.group()
def schedule():
    """Lessons calendar."""


@schedule.command("list")
@click.option("--days", default=3, help="Window length in days from --from")
@click.option("--from", "from_", default=None, help="Start 'YYYY-MM-DD' (default: today)")
@click.pass_context
def schedule_list(ctx, days, from_):
    a = api(ctx)
    start = datetime.strptime(from_, "%Y-%m-%d") if from_ else datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    fj = start.strftime("%Y-%m-%dT00:00:00Z")
    tj = end.strftime("%Y-%m-%dT00:00:00Z")
    items = a.lessons_upcoming(fj, tj)
    lines = [lesson_line(l) for l in items]
    out(ctx, items, "\n".join(lines) if lines else "(no lessons)")


@schedule.command("add")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--at", "when", required=True, help="'YYYY-MM-DD HH:MM' in --tz")
@click.option("--duration", default=60, show_default=True, type=int, help="Minutes")
@click.option("--tz", "tz_name", default=DEFAULT_TZ, show_default=True)
@click.option("--no-conflict-check", is_flag=True)
@click.pass_context
def schedule_add(ctx, klass, when, duration, tz_name, no_conflict_check):
    a = api(ctx)
    c = find_class(a, klass)
    start_iso = parse_when(when, tz_name)
    res = a.schedule_lesson(c["id"], start_iso, duration, check_conflicts=not no_conflict_check)
    lesson = res["lesson"]
    conf = res.get("conflicts") or []
    msg = f"+ lesson {start_iso} ({duration}m) in {c.get('name')!r} id={lesson.get('id')}"
    if conf:
        msg += "\n  ⚠ conflicts: " + json.dumps(conf, ensure_ascii=False)
    out(ctx, res, msg)


@schedule.command("move")
@click.option("--lesson", "lesson_id", required=True, help="Lesson id")
@click.option("--at", "when", required=True, help="New start 'YYYY-MM-DD HH:MM' in --tz")
@click.option("--duration", default=None, type=int, help="Optional new duration in minutes")
@click.option("--tz", "tz_name", default=DEFAULT_TZ, show_default=True)
@click.pass_context
def schedule_move(ctx, lesson_id, when, duration, tz_name):
    """Reschedule a lesson (PATCH /lessons/{id})."""
    a = api(ctx)
    start_iso = parse_when(when, tz_name)
    r = a.lesson_patch(lesson_id, start_at_iso=start_iso, duration_minutes=duration)
    out(ctx, r, f"→ lesson {lesson_id} moved to {start_iso}" + (f" ({duration}m)" if duration else ""))


@schedule.command("rm")
@click.argument("lesson_id")
@click.option("--scope", type=click.Choice(["single", "series"]), default=None)
@click.pass_context
def schedule_rm(ctx, lesson_id, scope):
    """Delete a scheduled lesson (only for lessons the user asked to remove!)."""
    api(ctx).lesson_delete(lesson_id, scope)
    out(ctx, {"deleted": lesson_id, "scope": scope},
        f"- deleted lesson {lesson_id}" + (f" (scope={scope})" if scope else ""))


# ---------------- units ----------------

@cli.group()
def units():
    """Units — the lessons inside a classroom's learning room."""


@units.command("list")
@click.argument("klass")
@click.pass_context
def units_list(ctx, klass):
    a = api(ctx)
    c = find_class(a, klass)
    us = a.units_list(c["id"])
    lines = [f"{u.get('title') or '(untitled)'}  id={u.get('id')}  pos={u.get('position')}  "
             f"open={u.get('access_open', u.get('accessOpen'))}" for u in us]
    out(ctx, us, "\n".join(lines) if lines else "(no units)")


@units.command("add")
@click.option("--class", "klass", required=True)
@click.option("--title", default=None)
@click.option("--current", is_flag=True, help="Set as the class's current lesson")
@click.option("--open/--closed", "open_", default=True, help="Give / hide from the student")
@click.option("--no-open", is_flag=True, help="Alias for --closed")
@click.pass_context
def units_add(ctx, klass, title, current, open_, no_open):
    a = api(ctx)
    c = find_class(a, klass)
    u = a.unit_create(c["id"])
    if title:
        u = a.unit_patch(u["id"], title=title)
    if no_open:
        open_ = False
    u = a.unit_access(u["id"], c["id"], open_)
    msg = f"+ unit {u.get('title') or '(untitled)'} id={u.get('id')} open={u.get('accessOpen')} in {c.get('name')!r}"
    if current:
        a.unit_set_current(c["id"], u["id"])
        msg += "  ★ set as current lesson"
    out(ctx, u, msg)


@units.command("current")
@click.option("--class", "klass", required=True)
@click.option("--unit", "unit_id", required=True)
@click.pass_context
def units_current(ctx, klass, unit_id):
    a = api(ctx)
    c = find_class(a, klass)
    r = a.unit_set_current(c["id"], unit_id)
    out(ctx, r, f"★ {c.get('name')} → current lesson {unit_id}")


# ---------------- courses (books) ----------------

@cli.group()
def courses():
    """Courses = the books on your Materials shelf."""


@courses.command("list")
@click.pass_context
def courses_list(ctx):
    items = api(ctx).courses_all()
    lines = [f"{c.get('title')}  id={c.get('id')}  "
             f"cover={'yes' if c.get('cover_url') else '—'}  "
             f"folder={c.get('folder_id') or '—'}" for c in items]
    out(ctx, items, "\n".join(lines) if lines else "(no courses)")


@courses.command("create")
@click.option("--title", required=True)
@click.option("--description", default=None)
@click.option("--description-file", default=None, help="Read the description from a file (html ok)")
@click.option("--language", default="en")
@click.option("--folder", "folder_id", default=None, help="Folder id to place the book in")
@click.pass_context
def courses_create(ctx, title, description, description_file, language, folder_id):
    a = api(ctx)
    existing = [c for c in a.courses_all() if (c.get("title") or "").lower() == title.lower()]
    if existing:
        click.echo(f"= course exists: {existing[0].get('title')} id={existing[0].get('id')}")
        return
    if description_file:
        description = open(description_file, encoding="utf-8").read()
    c = a.course_create(title, description=description, language=language, folder_id=folder_id)
    out(ctx, c, f"+ course {c.get('title')} id={c.get('id')}")


@courses.command("cover")
@click.argument("course")
@click.option("--file", "filepath", required=True, help="Cover image (png/jpg)")
@click.pass_context
def courses_cover(ctx, course, filepath):
    """Set a book cover: uploads the image and patches the course cover_url."""
    a = api(ctx)
    c = find_course(a, course)
    url = a.upload(filepath, kind="image")
    r = a.course_patch(c["id"], cover_url=url)
    readback = a.course_get(c["id"]).get("cover_url")
    if readback != url:
        err(f"cover_url did not persist (got {readback!r})")
    out(ctx, {"course_id": c["id"], "cover_url": url},
        f"✓ cover set for {c.get('title')}: {url}")


@courses.command("info")
@click.argument("course")
@click.pass_context
def courses_info(ctx, course):
    """Course card + its lessons."""
    a = api(ctx)
    c = find_course(a, course)
    lessons = a.course_lessons(c["id"]) or []
    lines = [f"{c.get('title')}  id={c.get('id')}  cover={c.get('cover_url') or '—'}",
             f"description: {(c.get('description') or '')[:200]}", "lessons:"]
    for l in lessons:
        lines.append(f"  - {l.get('title') or '(untitled)'}  id={l.get('id')}")
    out(ctx, {"course": c, "lessons": lessons}, "\n".join(lines))


# ---------------- folders ----------------

@cli.group()
def folders():
    """Folders group courses / boards on the shelf."""


@folders.command("list")
@click.option("--scope", default="personal", show_default=True)
@click.pass_context
def folders_list(ctx, scope):
    items = api(ctx).folders(scope)
    lines = [f"{f.get('title')}  id={f.get('id')}  items={f.get('item_count', '?')}" for f in items]
    out(ctx, items, "\n".join(lines) if lines else "(no folders)")


@folders.command("create")
@click.option("--title", required=True)
@click.option("--scope", default="personal", show_default=True)
@click.option("--color", default=None)
@click.pass_context
def folders_create(ctx, title, scope, color):
    f = api(ctx).folder_create(title, scope=scope, color=color)
    out(ctx, f, f"+ folder {f.get('title')} id={f.get('id')}")


@folders.command("move")
@click.option("--folder", "folder_id", required=True, help="Target folder id")
@click.option("--course", "course_ids", multiple=True, required=True, help="Course id (repeatable)")
@click.option("--scope", default="personal", show_default=True)
@click.pass_context
def folders_move(ctx, folder_id, course_ids, scope):
    r = api(ctx).folder_move_items(list(course_ids), folder_id, scope=scope)
    out(ctx, {"moved": r, "folder_id": folder_id}, f"✓ moved {r} item(s) into folder {folder_id}")


# ---------------- materials ----------------

@cli.group()
def materials():
    """Build lesson materials from JSON specs (courses → lessons → exercises)."""


@materials.command("build")
@click.argument("spec_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--course", default=None, help="Course title or id (overrides the spec's 'course')")
@click.option("--state", "state_path", default=None,
              help="Resume/record file (default: <spec>.state.json — one is always used)")
@click.option("--media-dir", default=None, help="Directory for local media referenced by the spec")
@click.option("--pace", default=0.35, show_default=True, help="Seconds between API calls")
@click.option("--dry-run", is_flag=True, help="Print the plan only; no API calls at all")
@click.pass_context
def materials_build(ctx, spec_file, course, state_path, media_dir, pace, dry_run):
    """Build (or resume) a material lesson from SPEC_FILE.

    Run --dry-run first to review the plan, then build. The state file makes
    the build resumable and prevents duplicates on re-runs.
    """
    spec = json.load(open(spec_file, encoding="utf-8"))
    state_path = state_path or spec_file + ".state.json"
    a = None if dry_run else api(ctx)

    def log(line):
        click.echo(line)

    result = build_material(
        a, spec, course=course, state_path=state_path, dry_run=dry_run,
        pace=pace, media_dir=media_dir,
        spec_dir=os.path.dirname(os.path.abspath(spec_file)), log=log,
    )
    if dry_run:
        click.echo(f"PLAN (dry run) — '{result['lesson_title']}': "
                   f"{result['sections']} sections, plan lines above")
        click.echo("Re-run without --dry-run to build (state file: %s)" % state_path)
    else:
        click.echo(f"=== done: {result['lesson_title']} — sections={result['sections']}, "
                   f"exercises={result['exercises']}, warnings={len(result['warnings'])}")
        for w in result["warnings"][-10:]:
            click.echo(f"  ! {w}")


@materials.command("tree")
@click.argument("course")
@click.pass_context
def materials_tree(ctx, course):
    """Show a material's structure (lessons → sections → exercise counts)."""
    a = api(ctx)
    c = find_course(a, course)
    lines = [f"{c.get('title')} ({c.get('id')})"]
    for l in a.course_lessons(c["id"]) or []:
        lines.append(f"  • {l.get('title') or '(untitled)'}  [{l.get('id')}]")
        for s in a.unit_sections(l["id"]) or []:
            exs = a.section_exercises(s["id"]) or []
            types = {}
            for e in exs:
                types[e.get("type")] = types.get(e.get("type"), 0) + 1
            lines.append(f"      - {s.get('title') or '(untitled)'}: {len(exs)} exercises {types}")
    click.echo("\n".join(lines))


# ---------------- raw ----------------

@cli.command("raw")
@click.argument("method")
@click.argument("path")
@click.option("--body", default=None, help="JSON body")
@click.pass_context
def raw(ctx, method, path, body):
    """Raw API call: cli-anything-langlisto raw GET /classrooms"""
    jb = json.loads(body) if body else None
    a = api(ctx)
    st, d = a.c.request(method, path, json_body=jb)
    click.echo(json.dumps(d, ensure_ascii=False, indent=1))


def main():
    try:
        cli(obj=None)
    except LanglistoError as e:
        err(str(e))


if __name__ == "__main__":
    main()
