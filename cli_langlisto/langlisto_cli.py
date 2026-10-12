"""cli-langlisto — CLI for the LangListo teacher workspace.

Commands: login · logout · doctor · whoami · students · classes · schedule ·
units · courses (books) · books (modules / lesson order / starter cleanup) ·
folders · materials (build · run · verify · status · tree) · exercises ·
preview (student-view demo) · raw.
"""

import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import click

from . import __version__
from .core.api import Langlisto, login_public
from .core.client import LanglistoError, LanglistoClient, SESSION_FILE, save_session, load_token
from .core.materials import build_material
from .core.verify import (
    VerifyError,
    expand_state_paths,
    exercise_brief,
    fmt_status,
    fmt_verify,
    split_refs,
    summarize_state_files,
    verify_course,
)
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


def find_unit(api, cid, ref):
    """Resolve a unit by id or title inside a class's unit list.

    The class list includes linked materials (classroom_id null) — so a
    material unit referenced in the room resolves here too. Bare UUIDs that
    the list does not carry are passed through unchanged (recourse for units
    the platform hides); anything else must match an id or title."""
    units = api.units_list(cid) or []
    for u in units:
        if str(u.get("id")) == str(ref):
            return u
    n = str(ref or "").strip().lower()
    pool = [u for u in units if n and n in (u.get("title") or "").lower()]
    if len(pool) == 1:
        return pool[0]
    if len(pool) > 1:
        err(f"ambiguous unit {ref!r}: " + ", ".join(repr(u.get("title")) for u in pool))
    if len(n) >= 32:
        return {"id": str(ref), "title": None}
    err(f"unit not found in this class: {ref!r}. Known: "
        + ", ".join(repr(u.get("title")) for u in units[:20]))


def find_class_student(api, cid, ref):
    """Resolve a student among the CLASS members first (demo students are not
    in the account-wide student list), then fall back to `find_student`."""
    members = api.classroom_students(cid) or []
    for m in members:
        if str(m.get("id")) == str(ref):
            return m
    n = str(ref or "").strip().lower()
    pool = [m for m in members
            if n and (n in (m.get("name") or "").lower() or n in (m.get("email") or "").lower())]
    if len(pool) == 1:
        return pool[0]
    if len(pool) > 1:
        err(f"ambiguous student {ref!r} in this class: "
            + ", ".join(repr(m.get("name")) for m in pool))
    return find_student(api, ref)


def split_exercise_values(values):
    """--exercise values (repeatable, commas ok) → flat list of ids."""
    ids = []
    for v in values:
        ids.extend(split_refs(v))
    return ids


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
@click.version_option(__version__, prog_name="cli-langlisto")
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


@students.command("patch")
@click.argument("student")
@click.option("--email", default=None, help="New email (also the login).")
@click.option("--name", default=None, help="New display name.")
@click.pass_context
def students_patch(ctx, student, email, name):
    """Update a student's email / name (PATCH /students/{id}).

    The taken-email hint: a 409 `student_email_taken` means the address sits
    on an EXISTING user account (e.g. the student self-registered) — detach
    and re-attach, or attach that account — see references/troubleshooting.md.
    """
    if not email and not name:
        err("pass --email and/or --name")
    a = api(ctx)
    s = find_student(a, student)
    fields = {}
    if email:
        fields["email"] = email.strip()
    if name:
        fields["name"] = name.strip()
    try:
        r = a.student_patch(s["id"], **fields)
    except LanglistoError as e:
        if e.status == 409 or e.code == "student_email_taken":
            err(
                f"email {fields.get('email')!r} is already taken (409 student_email_taken).\n"
                "The address belongs to an existing USER account — most likely the student\n"
                "self-registered. Options: (a) detach the current link\n"
                f"  `students unenroll {s.get('name')} --class <class>` + `classes create --student <email>`,\n"
                "or (b) keep that account and attach it by email (no invite). See\n"
                "references/troubleshooting.md → '409 student_email_taken'."
            )
        err(str(e))
    readback = a.student_get(s["id"])
    changed = {k: readback.get(k) for k in fields}
    out(ctx, {"student_id": s["id"], "changed": changed, "student": readback},
        f"→ {readback.get('name')}: " + ", ".join(f"{k}={v}" for k, v in changed.items()))


@students.command("unenroll")
@click.argument("student")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.pass_context
def students_unenroll(ctx, student, klass):
    """Remove a student from ONE class (membership link only, account kept)."""
    a = api(ctx)
    s = find_student(a, student)
    c = find_class(a, klass)
    a.classroom_remove_student(c["id"], s["id"])
    left = [x for x in (a.classroom_students(c["id"]) or []) if x.get("id") == s["id"]]
    ok = not left
    data = {"classroom_id": c["id"], "student_id": s["id"], "removed": ok}
    out(ctx, data, f"{'✓' if ok else '✗'} {s.get('name')} "
                   f"{'removed from' if ok else 'STILL IN'} {c.get('name')}")
    if not ok:
        sys.exit(1)


@students.command("verify")
@click.argument("student")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.pass_context
def students_verify(ctx, student, klass):
    """Check that a student is a member of a class (exit 1 if not)."""
    a = api(ctx)
    s = find_student(a, student)
    c = find_class(a, klass)
    members = a.classroom_students(c["id"]) or []
    member = any(x.get("id") == s["id"] for x in members)
    data = {"student": {"id": s["id"], "name": s.get("name")},
            "classroom": {"id": c["id"], "name": c.get("name")},
            "member": member, "member_count": len(members)}
    out(ctx, data, f"{'✓' if member else '✗'} {s.get('name')} "
                   f"{'is in' if member else 'is NOT in'} {c.get('name')}")
    sys.exit(0 if member else 1)


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


# ---------------- books (course ops: modules, order, starter cleanup) ----------------

@cli.group()
def books():
    """Book operations: modules, lesson order, starter-lesson cleanup.

    (Books ARE courses — `courses` stays the create/dress surface.)"""


@books.group("modules")
def books_modules():
    """Course modules (the book's table of contents)."""


@books_modules.command("list")
@click.argument("course")
@click.pass_context
def books_modules_list(ctx, course):
    a = api(ctx)
    c = find_course(a, course)
    mods = a.course_modules(c["id"]) or []
    mods = mods if isinstance(mods, list) else mods.get("items", [])
    lines = [f"{m.get('title') or '(untitled)'}  id={m.get('id')}" for m in mods]
    out(ctx, {"course_id": c["id"], "modules": mods},
        "\n".join(lines) if lines else "(no modules)")


@books_modules.command("add")
@click.argument("course")
@click.option("--title", required=True, help="Module title (idempotent by title)")
@click.pass_context
def books_modules_add(ctx, course, title):
    a = api(ctx)
    c = find_course(a, course)
    mods = a.course_modules(c["id"]) or []
    mods = mods if isinstance(mods, list) else mods.get("items", [])
    hit = next((m for m in mods if (m.get("title") or "") == title), None)
    if hit:
        out(ctx, {"module": hit, "created": False},
            f"= module exists: {title}  id={hit.get('id')}")
        return
    m = a.module_create(c["id"], title)
    out(ctx, {"module": m, "created": True},
        f"+ module {m.get('title') or title}  id={m.get('id')}  (in {c.get('title')!r})")


@books_modules.command("rename")
@click.argument("course")
@click.option("--module", "module_ref", required=True, help="Module id or exact title")
@click.option("--title", "new_title", required=True)
@click.pass_context
def books_modules_rename(ctx, course, module_ref, new_title):
    a = api(ctx)
    c = find_course(a, course)
    mods = a.course_modules(c["id"]) or []
    mods = mods if isinstance(mods, list) else mods.get("items", [])
    hit = next((m for m in mods if str(m.get("id")) == module_ref
                or (m.get("title") or "") == module_ref), None)
    if not hit:
        err(f"module not found: {module_ref!r} in {c.get('title')!r}")
    r = a.module_patch(hit["id"], new_title)
    out(ctx, {"module": r}, f"→ module {hit.get('id')}: {hit.get('title') or '(untitled)'} → {new_title}")


@books.group("lessons")
def books_lessons():
    """Lesson ordering inside a book."""


@books_lessons.command("order")
@click.argument("course")
@click.option("--ids", default=None, help="Comma-separated lesson ids, in the desired order")
@click.option("--file", "file_path", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="JSON file: [{\"id\":…, \"module_id\":…}] (module-aware ordering)")
@click.pass_context
def books_lessons_order(ctx, course, ids, file_path):
    """Set the book order: PUT /courses/{id}/lessons/order.

    Use --ids for a plain order, or --file with module ids for a BGP-style
    module-aware order."""
    a = api(ctx)
    c = find_course(a, course)
    if bool(ids) == bool(file_path):
        err("pass exactly one of --ids or --file")
    if ids:
        entries = [{"id": i, "module_id": None} for i in split_refs(ids)]
    else:
        data = json.load(open(file_path, encoding="utf-8"))
        if not isinstance(data, list):
            err("--file must contain a JSON list of {\"id\":…, \"module_id\":…}")
        entries = [{"id": e["id"], "module_id": e.get("module_id")} for e in data]
    if not entries:
        err("no lesson ids given")
    a.course_lessons_order(c["id"], entries)
    readback = a.course_lessons(c["id"]) or []
    got = [l["id"] for l in sorted(readback, key=lambda x: x.get("position") or 0)]
    want = [e["id"] for e in entries]
    ok = got == want or (all(i in got for i in want) and got[:len(want)] == want)
    data = {"course_id": c["id"], "ordered": len(entries), "readback_ok": ok,
            "position_order": got}
    out(ctx, data, f"{'✓' if ok else '⚠'} ordered {len(entries)} lessons in {c.get('title')!r}"
                   + ("" if ok else f" — read-back order differs: {got}"))
    if not ok:
        sys.exit(1)


@books.command("purge-starter")
@click.argument("course")
@click.option("--dry-run", is_flag=True, help="Only show what would be removed")
@click.pass_context
def books_purge_starter(ctx, course, dry_run):
    """Remove the platform's starter lesson(s) from a course.

    A starter lesson = empty title AND no sections (exact semantics of the
    BGP mass runner's purge_starter_lesson — anything with content is kept)."""
    a = api(ctx)
    c = find_course(a, course)
    lessons = a.course_lessons(c["id"]) or []
    removed, kept = [], []
    for l in lessons:
        title = (l.get("title") or "").strip()
        if title:
            continue
        secs = a.unit_sections(l["id"]) or []
        if secs:
            kept.append({"id": l["id"], "reason": "untitled but has sections"})
            continue
        if dry_run:
            removed.append({"id": l["id"], "removed": False, "dry_run": True})
            continue
        try:
            a.unit_delete(l["id"])
            removed.append({"id": l["id"], "removed": True})
        except LanglistoError as e:
            removed.append({"id": l["id"], "removed": False, "error": e.message})
    data = {"course_id": c["id"], "dry_run": dry_run, "removed": removed, "kept": kept}
    lines = [f"{'·' if dry_run else '−'} starter lesson {r['id']}"
             + ("" if r.get("removed") or dry_run else f"  ✗ {r.get('error')}")
             for r in removed]
    for k in kept:
        lines.append(f"  (kept {k['id']}: {k['reason']})")
    out(ctx, data, "\n".join(lines) if lines else "(no starter lessons found)")


# ---------------- folders ----------------

@cli.group()
def folders():
    """Folders group courses / boards on the shelf."""


@folders.command("list")
@click.option("--scope", default="material", show_default=True, help="material (shelf) or classroom")
@click.pass_context
def folders_list(ctx, scope):
    items = api(ctx).folders(scope)
    lines = [f"{f.get('title')}  id={f.get('id')}  items={f.get('item_count', '?')}" for f in items]
    out(ctx, items, "\n".join(lines) if lines else "(no folders)")


@folders.command("create")
@click.option("--title", required=True)
@click.option("--scope", default="material", show_default=True, help="material (shelf) or classroom")
@click.option("--color", default=None)
@click.pass_context
def folders_create(ctx, title, scope, color):
    f = api(ctx).folder_create(title, scope=scope, color=color)
    out(ctx, f, f"+ folder {f.get('title')} id={f.get('id')}")


@folders.command("move")
@click.option("--folder", "folder_id", required=True, help="Target folder id")
@click.option("--course", "course_ids", multiple=True, required=True, help="Course id (repeatable)")
@click.option("--scope", default="material", show_default=True, help="material (shelf) or classroom")
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
    """Show a material's structure: lessons → sections → exercises (with types)."""
    a = api(ctx)
    c = find_course(a, course)
    lessons = a.course_lessons(c["id"]) or []
    mods = {}
    if any(l.get("module_id") for l in lessons):
        ms = a.course_modules(c["id"]) or []
        ms = ms if isinstance(ms, list) else ms.get("items", [])
        mods = {m["id"]: m.get("title") for m in ms}
    data = {"course": {"id": c["id"], "title": c.get("title")}, "lessons": []}
    lines = [f"{c.get('title')} ({c.get('id')})"]
    for l in sorted(lessons, key=lambda x: x.get("position") or 0):
        mod = mods.get(l.get("module_id")) if l.get("module_id") else None
        lines.append(f"  • {l.get('title') or '(untitled)'}  [{l.get('id')}]"
                     + (f"  [{mod}]" if mod else ""))
        lrec = {"id": l["id"], "title": l.get("title") or "", "module_id": l.get("module_id"),
                "sections": [], "types": {}}
        for s in sorted(a.unit_sections(l["id"]) or [], key=lambda x: x.get("position") or 0):
            exs = a.section_exercises(s["id"]) or []
            types = {}
            srec = {"id": s["id"], "title": s.get("title") or "(untitled)", "exercises": []}
            for e in sorted(exs, key=lambda x: x.get("position") or 0):
                t = e.get("type") or "?"
                types[t] = types.get(t, 0) + 1
                lrec["types"][t] = lrec["types"].get(t, 0) + 1
                srec["exercises"].append({"id": e["id"], "type": t,
                                          "hidden": bool(e.get("hidden")),
                                          "brief": exercise_brief(e)})
                lines.append(f"      · [{t}] {e['id']}  {exercise_brief(e)}")
            srec["types"] = types
            lrec["sections"].append(srec)
            lines.append(f"      - {srec['title']}: {len(exs)} exercises {types}")
        data["lessons"].append(lrec)
    out(ctx, data, "\n".join(lines) if lessons else "(no lessons)")


def _resolve_specs(target):
    """Directory / glob / single file → sorted spec file list (state files skipped)."""
    if os.path.isdir(target):
        paths = sorted(glob.glob(os.path.join(target, "*.json")))
    elif any(ch in target for ch in "*?["):
        paths = sorted(glob.glob(target))
    elif os.path.exists(target):
        paths = [target]
    else:
        err(f"no such specs target: {target!r}")
    specs = [p for p in paths if not p.endswith(".state.json")]
    if not specs:
        err(f"no spec files found at {target!r}")
    return specs


@materials.command("run")
@click.argument("specs")
@click.option("--state", "state_path", default=None,
              help="Run-state file (default: <specs dir>/run-state.json)")
@click.option("--pace", default=1.0, show_default=True, help="Seconds between API calls")
@click.option("--only", "only", multiple=True, help="Only these spec slugs (repeatable)")
@click.option("--retry-failed", is_flag=True, help="Re-run specs whose last status is 'failed'")
@click.option("--limit", type=int, default=None, help="Build at most N specs in this run")
@click.option("--course", default=None, help="Override the spec's 'course'")
@click.option("--media-dir", default=None, help="Directory for local media referenced by specs")
@click.option("--verbose", is_flag=True, help="Stream the builder's per-item log")
@click.option("--dry-run", is_flag=True, help="Only list the specs + their recorded statuses")
@click.pass_context
def materials_run(ctx, specs, state_path, pace, only, retry_failed, limit, course,
                  media_dir, verbose, dry_run):
    """Mass-run a directory (or glob) of lesson specs, resumably.

    Skips specs marked 'done' / 'done_with_warnings' in the state file unless
    --only names them; 'failed' specs are retried with --retry-failed.
    Exit code is non-zero when a build failed in this run."""
    files = _resolve_specs(specs)
    if only:
        wanted = set(only)
        files = [f for f in files if os.path.basename(f)[:-5] in wanted]
        missing = wanted - {os.path.basename(f)[:-5] for f in files}
        if missing:
            err("no spec files for --only " + ", ".join(sorted(missing)))
    if state_path is None:
        base = os.path.dirname(files[0])
        state_path = os.path.join(base or ".", "run-state.json")
    files = [f for f in files if os.path.abspath(f) != os.path.abspath(state_path)]
    if not files:
        err("only the run-state file matched — pass a specs directory/glob")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}

    def save():
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, state_path)

    plan, skipped = [], []
    for spec_file in files:
        slug = os.path.basename(spec_file)[:-5]
        rec = state.get(slug) or {}
        st = rec.get("status")
        if st == "failed" and not retry_failed:
            skipped.append((slug, st, "pass --retry-failed to re-run"))
        elif st in ("done", "done_with_warnings"):
            skipped.append((slug, st, "already built"))
        else:
            plan.append((slug, spec_file))

    if dry_run:
        data = {"specs": [{"slug": s, "path": p} for s, p in plan],
                "skipped": [{"slug": s, "status": st, "why": w} for s, st, w in skipped],
                "state": state_path, "dry_run": True}
        lines = [f"plan: {len(plan)} to build, {len(skipped)} skipped (state: {state_path})"]
        for s, p in plan:
            lines.append(f"  + {s}")
        for s, st, w in skipped:
            lines.append(f"  · {s} [{st}] {w}")
        out(ctx, data, "\n".join(lines))
        return

    a = api(ctx)
    built, failed_now = [], []
    total = len(plan)
    for i, (slug, spec_file) in enumerate(plan, 1):
        if limit is not None and len(built) + len(failed_now) >= limit:
            break
        spec = json.load(open(spec_file, encoding="utf-8"))
        sp = spec_file + ".state.json"
        t0 = datetime.now(timezone.utc)
        try:
            res = build_material(a, spec, course=course, state_path=sp, pace=pace,
                                 media_dir=media_dir,
                                 spec_dir=os.path.dirname(os.path.abspath(spec_file)),
                                 log=(click.echo if verbose else (lambda *_: None)))
            warns = res.get("warnings") or []
            # A build refused by the lock guard writes NOTHING — recording it as
            # done/done_with_warnings would silently skip the spec on re-runs.
            # NOTE: use the explicit engine signal, not warning-text matching:
            # state["warnings"] persists across runs, so an old lock message
            # would otherwise poison every later status.
            if res.get("locked"):
                state[slug] = {"status": "failed",
                               "error": "build lock refused — another builder holds the spec lock (nothing was written this run)",
                               "exercises": res.get("exercises"),
                               "seconds": round((datetime.now(timezone.utc) - t0).total_seconds(), 1),
                               "spec_path": spec_file}
                save()
                failed_now.append(slug)
                click.echo(f"[{i}/{total}] ✗ {slug}: build lock refused — retry with --retry-failed", err=True)
                time.sleep(0.5)
                continue
            status = "done_with_warnings" if warns else "done"
            st = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
            state[slug] = {"status": status, "lesson_id": st.get("lesson_id"),
                           "exercises": res.get("exercises"),
                           "warnings": warns[:10],
                           "seconds": round((datetime.now(timezone.utc) - t0).total_seconds(), 1),
                           "spec_path": spec_file}
            save()
            built.append(slug)
            click.echo(f"[{i}/{total}] {slug} — {res.get('exercises')} ex "
                       f"in {state[slug]['seconds']}s, warnings={len(warns)}")
        except Exception as e:  # noqa: BLE001 — one bad spec must not stop the batch
            state[slug] = {"status": "failed", "error": str(e)[:300],
                           "seconds": round((datetime.now(timezone.utc) - t0).total_seconds(), 1),
                           "spec_path": spec_file}
            save()
            failed_now.append(slug)
            click.echo(f"[{i}/{total}] ✗ {slug}: {e}", err=True)
        time.sleep(0.5)
    summary = {"state": state_path, "built": built, "failed": failed_now,
               "skipped": [{"slug": s, "status": st, "why": w} for s, st, w in skipped]}
    out(ctx, summary, f"built {len(built)}/{total} specs"
                      f"{', failed ' + str(len(failed_now)) if failed_now else ''}"
                      f" (state: {state_path})")
    if failed_now:
        sys.exit(1)


@materials.command("status")
@click.option("--state", "state_paths", multiple=True, required=True,
              help="Run-state / builder-state file(s) or globs (repeatable)")
@click.pass_context
def materials_status(ctx, state_paths):
    """Summarize run-state files: done/failed/warnings counts, build time."""
    files = expand_state_paths(list(state_paths))
    if not files:
        err("no state files matched: " + ", ".join(state_paths))
    res = summarize_state_files(files)
    out(ctx, res, fmt_status(res))


@materials.command("verify")
@click.argument("course")
@click.option("--lesson", default=None, help="Only this lesson (id or title substring)")
@click.option("--state", "state_paths", multiple=True,
              help="Builder state file(s) for the state↔live diff (globs ok)")
@click.option("--min-note-coverage", type=float, default=None,
              help="Fail if notes coverage (essay/voice/free-gap) is below this (0..1)")
@click.option("--deep", is_flag=True, help="Fetch every exercise individually (slower)")
@click.option("--grading", is_flag=True,
              help="Spot-check grading via POST /exercises/{id}/trial-check")
@click.option("--strict", is_flag=True, help="Treat warnings as failures too")
@click.option("--pace", default=0.5, show_default=True, help="Seconds between API reads")
@click.pass_context
def materials_verify(ctx, course, lesson, state_paths, min_note_coverage, deep,
                     grading, strict, pace):
    """LIVE verification of a material course/lesson (read-only by default).

    Checks: counts, type distribution, empty content/answers, image captions,
    teacher-notes coverage (gate with --min-note-coverage), duplicate
    sections/items (normalized hashes), state↔live drift (--state) and the
    render-safety lint. Exit codes: 0 = clean, 1 = problems, 2 = usage."""
    a = api(ctx)
    state_files = expand_state_paths(list(state_paths))
    missing = [f for f in state_files if not os.path.exists(f)]
    if missing:
        err("state file not found: " + ", ".join(missing))
    try:
        res = verify_course(a, course, lesson_ref=lesson,
                            state_paths=state_files,
                            min_note_coverage=min_note_coverage, deep=deep,
                            grading=grading, strict=strict, pace=pace)
    except VerifyError as e:
        err(str(e))
    out(ctx, res, fmt_verify(res))
    sys.exit(0 if res["ok"] else 1)


# ---------------- exercises ----------------

@cli.group()
def exercises():
    """Single-exercise operations (show / delete / notes / order / patch)."""


@exercises.command("show")
@click.argument("exercise_id")
@click.pass_context
def exercises_show(ctx, exercise_id):
    """Fetch one exercise's full payload (GET /exercises/{id})."""
    ex = api(ctx).exercise_get(exercise_id)
    t = ex.get("type") or "?"
    lines = [f"[{t}] {ex.get('id')}",
             f"  section: {ex.get('section_id')}  position: {ex.get('position')}  "
             f"hidden: {ex.get('hidden')}",
             f"  {exercise_brief(ex)}"]
    notes = [n for n in (ex.get("teacher_notes") or []) if isinstance(n, dict)]
    if notes:
        lines.append(f"  teacher notes: {len(notes)}")
    out(ctx, ex, "\n".join(lines))


@exercises.command("delete")
@click.argument("exercise_id")
@click.pass_context
def exercises_delete(ctx, exercise_id):
    """Delete an exercise (DELETE /exercises/{id} → 204).

    Destructive: only for content you created (e.g. cleanup after a rebuild)."""
    api(ctx).exercise_delete(exercise_id)
    gone = None
    try:
        gone = api(ctx).exercise_get(exercise_id)
    except LanglistoError as e:
        if e.status not in (404, 403):
            raise
    data = {"deleted": exercise_id, "confirmed": gone is None}
    out(ctx, data, f"− exercise {exercise_id} deleted"
                   + ("" if gone is None else " (⚠ still readable)"))
    if gone is not None:
        sys.exit(1)


@exercises.command("notes")
@click.argument("exercise_id")
@click.option("--text", "text_", default=None, help="Note text (teacher-only by default)")
@click.option("--from-file", "from_file", default=None,
              type=click.Path(exists=True, dir_okay=False), help="Read the note from a file")
@click.option("--visible-to-student", is_flag=True, help="Show the note to the student too")
@click.pass_context
def exercises_notes(ctx, exercise_id, text_, from_file, visible_to_student):
    """Set/replace teacher notes on ANY exercise (PUT /exercises/{id}/teacher-notes)."""
    if bool(text_) == bool(from_file):
        err("pass exactly one of --text or --from-file")
    body = text_ if text_ else open(from_file, encoding="utf-8").read()
    if not body.strip():
        err("note text is empty")
    a = api(ctx)
    a.exercise_teacher_notes(exercise_id, body, visible_to_student=visible_to_student)
    ex = a.exercise_get(exercise_id)
    got = [n.get("text") for n in (ex.get("teacher_notes") or []) if isinstance(n, dict)]
    persisted = body in got
    data = {"exercise_id": exercise_id, "visible_to_student": visible_to_student,
            "persisted": persisted, "notes_count": len(got)}
    out(ctx, data, f"{'✓' if persisted else '✗'} note set on {exercise_id} "
                   f"({len(got)} note(s), visible_to_student={visible_to_student})")
    if not persisted:
        sys.exit(1)


@exercises.command("order")
@click.option("--section", "section_id", required=True, help="Section id")
@click.option("--ids", required=True, help="Comma-separated exercise ids, in the desired order")
@click.pass_context
def exercises_order(ctx, section_id, ids):
    """Reorder exercises inside a section (POST /sections/exercises/order).

    First id goes to position 0 (the banner slot)."""
    a = api(ctx)
    wanted = split_refs(ids)
    if not wanted:
        err("--ids is empty")
    a.section_exercises_order(wanted)
    exs = a.section_exercises(section_id) or []
    got = [e["id"] for e in sorted(exs, key=lambda x: x.get("position") or 0)]
    ok = got == wanted or (set(wanted) <= set(got) and got[:len(wanted)] == wanted)
    data = {"section_id": section_id, "requested": wanted, "readback": got, "ok": ok}
    out(ctx, data, f"{'✓' if ok else '⚠'} ordered {len(wanted)} exercise(s) in section "
                   f"{section_id}" + ("" if ok else f" — read-back differs: {got}"))
    if not ok:
        sys.exit(1)


@exercises.command("patch")
@click.argument("exercise_id")
@click.option("--suffix", required=True, help="Type suffix, e.g. note / text-block / drag-word-gap")
@click.option("--body-file", required=True, type=click.Path(exists=True, dir_okay=False),
              help="JSON body for PATCH /exercises/{id}/<suffix>")
@click.pass_context
def exercises_patch(ctx, exercise_id, suffix, body_file):
    """Generic escape hatch: PATCH /exercises/{id}/<suffix> with a JSON body file."""
    body = json.load(open(body_file, encoding="utf-8"))
    a = api(ctx)
    a.exercise_patch(exercise_id, suffix, body)
    ex = a.exercise_get(exercise_id)
    data = {"exercise_id": exercise_id, "suffix": suffix, "patched": True,
            "exercise": ex}
    out(ctx, data, f"✓ patched {exercise_id} /{suffix.lstrip('/')} "
                   f"(now [{ex.get('type')}] {exercise_brief(ex)})")


# ---------------- preview (student view) ----------------

@cli.group()
def preview():
    """Open the platform's own demo-student preview (student view)."""


@preview.command("open")
@click.option("--next", "next_path", default=None,
              help="Page to open in the demo cabinet (default /my-classes)")
@click.option("--class", "klass", default=None, help="Open this class's room (name or id)")
@click.option("--unit", "unit_ref", default=None, help="Deep-link to this unit (id or title)")
@click.option("--tab", "tab", default=None, type=click.Choice(["lesson", "homework"]),
              help="Room tab to open")
@click.pass_context
def preview_open(ctx, next_path, klass, unit_ref, tab):
    """Get a demoview.langlisto.com magic-link URL (platform Demo preview).

    Wraps POST /demo-student/open — the same mechanism as the «Test student»
    row → «Log in as a student» on /students. The URL is single-use and is
    never stored by the CLI: open it yourself, then it expires.

    With --class/--unit/--tab the demo student lands inside that class's room
    («Homework» deep-link per RECON2 §3.4):
    /classrooms/{cid}/room?unit={unit}&tab=homework."""
    a = api(ctx)
    cid = uid = None
    if klass:
        if next_path:
            err("pass either --next or --class, not both")
        c = find_class(a, klass)
        cid = c["id"]
        path = f"/classrooms/{cid}/room"
        q = []
        if unit_ref:
            u = find_unit(a, cid, unit_ref)
            uid = u["id"]
            q.append(f"unit={uid}")
        if tab:
            q.append(f"tab={tab}")
        if q:
            path += "?" + "&".join(q)
    else:
        if unit_ref or tab:
            err("--unit/--tab need --class")
        path = next_path or "/my-classes"
    res = a.demo_student_open(path)
    url = (res or {}).get("url")
    if not url:
        err(f"no url in response: {res!r}")
    data = {"url": url, "next": path}
    if cid:
        data.update({"classroom_id": cid, "unit_id": uid, "tab": tab})
    out(ctx, data, f"demo-student preview ready (open in a browser):\n{url}\n"
                   f"→ next={path}" + (f"  (class {cid}, tab={tab})" if cid else ""))


# ---------------- homework ----------------

@cli.group()
def homework():
    """Homework of a class's units — the hidden per-unit homework section.

    Homework CONTENT is authored in the unit's workbook bucket («Exercises for
    homework», `bucket` / `prepare`): a section of the lesson spec flagged
    `"homework": true`, or `homework prepare` for existing lessons. Only when
    you assign it (`choose`) does it reach students. The room's «Homework» tab
    feels like this group: everything is keyed by the pair (classroom, unit)
    and EVERY call carries classroom_id — on a material unit the management
    endpoints answer 400 `homework_not_classroom_unit` without it. Exercises
    are addressed by their SOURCE ids (the ids you see in the lesson /
    material) — never by the copy ids inside the homework section (those are
    what `show` displays). `choose` is idempotent: re-choosing an assigned
    exercise is a no-op; students see the homework only once assigned."""


@homework.command("bucket")
@click.option("--unit", "unit_id", required=True, help="Unit id")
@click.pass_context
def homework_bucket(ctx, unit_id):
    """List the unit's prepared homework bucket («Exercises for homework»).

    Reads GET /units/{unit}/homework/workbook-exercises — the exercises
    prepared in the unit's workbook-section. They never show in the lesson
    flow and stay invisible to students until assigned (`homework choose`)."""
    a = api(ctx)
    items = a.unit_homework_workbook_exercises(unit_id) or []
    data = {"unit_id": unit_id, "count": len(items), "workbook": items}
    lines = [f"workbook bucket of unit {unit_id} — {len(items)} exercise(s):"]
    for x in items:
        label = " ".join(p for p in (x.get("label"), x.get("heading")) if p)
        lines.append(f"  · {x.get('id')}  [{x.get('type')}] {label}")
    if not items:
        lines.append("  (empty — prepare homework with `homework prepare`, or build a "
                     "spec section flagged \"homework\": true)")
    out(ctx, data, "\n".join(lines))


@homework.command("prepare")
@click.option("--unit", "unit_ref", required=True,
              help="Unit id (a title needs --class to resolve)")
@click.option("--class", "klass", default=None,
              help="Class name or id — resolves --unit by title")
@click.option("--section", "section_ref", required=True,
              help="Section id or title to move into the workbook bucket (e.g. Homework)")
@click.option("--keep-section", is_flag=True,
              help="Keep the emptied section. WARNING: without this flag the section is "
                   "DELETED — that also drops the students' answers on its exercises.")
@click.pass_context
def homework_prepare(ctx, unit_ref, klass, section_ref, keep_section):
    """Prepare homework: move a whole section into the unit's workbook bucket.

    The workbook bucket («Exercises for homework», GET /units/{uid}/workbook-section)
    is where homework content belongs — students never see it in the lesson
    flow; it becomes visible only once assigned (`homework choose`). This moves
    ALL exercises of the given regular section into the bucket
    (POST /exercises/move-to-section), reads the bucket back
    (GET …/homework/workbook-exercises) and — unless --keep-section — deletes
    the emptied section. Deletion cascades: the section's exercises and any
    student answers on them are dropped; nothing is deleted when the move or
    the read-back failed."""
    a = api(ctx)
    cid = find_class(a, klass)["id"] if klass else None
    ref = str(unit_ref).strip()
    if cid:
        u = find_unit(a, cid, unit_ref)
    elif len(ref) >= 32:
        u = {"id": ref, "title": None}
    else:
        err("a unit title needs --class to resolve (or pass the unit id)")
    uid = u["id"]

    secs = sorted(a.unit_sections(uid, classroom_id=cid) or [],
                  key=lambda s: s.get("position") or 0)
    n = str(section_ref).strip().lower()
    hit = [s for s in secs if str(s.get("id")) == str(section_ref)
           or (s.get("title") or "").strip().lower() == n]
    if len(hit) == 1:
        sec = hit[0]
    elif len(hit) > 1:
        err(f"ambiguous section {section_ref!r}: "
            + ", ".join(f"{s['id']} ({s.get('title')})" for s in hit))
    else:
        part = [s for s in secs if n and n in (s.get("title") or "").lower()]
        if len(part) != 1:
            err(f"section not found in unit {uid}: {section_ref!r}. Sections: "
                + ", ".join(f"{s.get('title') or '(untitled)'} [{s.get('id')}]" for s in secs))
        sec = part[0]
    sid = sec["id"]

    ids = [str(e.get("id")) for e in (a.section_exercises(sid) or []) if e.get("id")]
    dest = None
    moved = skipped = 0
    if ids:
        ws = a.unit_workbook_section(uid) or {}
        dest = ws.get("id") or ws.get("section_id")
        if not dest:
            err(f"no workbook-section id in the response: {ws!r}")
        batch = 100
        for i in range(0, len(ids), batch):
            res = a.exercises_move_to_section(dest, ids[i:i + batch]) or {}
            moved += int(res.get("moved") or 0)
            skipped += int(res.get("skipped") or 0)
            if i + batch < len(ids):
                time.sleep(1.0)  # be a gentle client on multi-batch moves
    wb_items = a.unit_homework_workbook_exercises(uid, classroom_id=cid) or []
    wb_ids = {str(x.get("id")) for x in wb_items}
    missing = [i for i in ids if i not in wb_ids]
    remaining = a.section_exercises(sid) or []
    ok = (skipped == 0 and not missing)
    deleted = False
    if not keep_section:
        if not remaining and ok:
            a.section_delete(sid)
            secs2 = a.unit_sections(uid, classroom_id=cid) or []
            deleted = all(str(s.get("id")) != sid for s in secs2)
            if not deleted:
                ok = False
        else:
            ok = False
    data = {"classroom_id": cid, "unit_id": uid, "section_id": sid,
            "section_title": sec.get("title"), "workbook_section_id": dest,
            "total": len(ids), "moved": moved, "skipped": skipped, "moved_ids": ids,
            "workbook_count": len(wb_items), "readback_missing": missing,
            "keep_section": keep_section, "section_deleted": deleted,
            "section_remaining": len(remaining), "confirmed": ok}
    lines = [f"{'✓' if ok else '⚠'} homework prepare on unit {uid}: moved {moved}/{len(ids)} "
             f"exercise(s) from {sec.get('title') or sid!r} to the workbook bucket"
             + (f" ({dest})" if dest else " (no exercises — nothing to move)")]
    lines.append(f"  read-back: workbook-exercises holds {len(wb_items)} item(s)"
                 + (f" — missing: {missing}" if missing else " — all moved ids present"))
    if keep_section:
        lines.append(f"= section kept (--keep-section; {len(remaining)} item(s) still in it)")
    elif deleted:
        lines.append(f"✓ section {sid} deleted (students' answers on it are gone)")
    else:
        lines.append(f"⚠ section {sid} NOT deleted ({len(remaining)} item(s) still in it "
                     f"or a failure — kept for safety)")
    out(ctx, data, "\n".join(lines))
    if not ok:
        sys.exit(1)


@homework.command("list")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", default=None, help="Only this unit (id or title)")
@click.option("--student", "student_ref", default=None,
              help="Personal statuses for this student (name or id)")
@click.pass_context
def homework_list(ctx, klass, unit_ref, student_ref):
    """List the class's homework units: GET /classrooms/{cid}/homeworks.

    Without --student the platform reports status=null (the list only holds
    units with live assignments); pass --student for their personal statuses
    (assigned / submitted / revision / completed)."""
    a = api(ctx)
    c = find_class(a, klass)
    sid = find_class_student(a, c["id"], student_ref)["id"] if student_ref else None
    items = a.classroom_homeworks(c["id"], student_id=sid) or []
    if unit_ref:
        u = find_unit(a, c["id"], unit_ref)
        items = [h for h in items if str(h.get("unit_id")) == str(u["id"])]
    if items:
        titles = {str(u.get("id")): (u.get("title") or "(untitled)")
                  for u in (a.units_list(c["id"]) or [])}
        for h in items:
            h["unit_title"] = titles.get(str(h.get("unit_id")))
    data = {"classroom_id": c["id"], "student_id": sid, "homeworks": items}
    lines = [f"{h.get('unit_title') or '(unknown unit)'}  unit={h.get('unit_id')}  "
             f"exercises={h.get('has_exercises')}  status={h.get('status')}"
             for h in items]
    out(ctx, data, "\n".join(lines) if lines else "(no homework in this class)")


@homework.command("show")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.pass_context
def homework_show(ctx, klass, unit_ref):
    """Show the homework as the STUDENT sees it (the copies in the hw section).

    GET /units/{unit}/homework → section_id, then that section's exercises.
    The copies carry fresh ids — management commands still use source ids."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    sec = a.unit_homework_section(u["id"], classroom_id=c["id"]) or {}
    hwsec = sec.get("section_id")
    copies = sorted((a.section_exercises(hwsec) if hwsec else []) or [],
                    key=lambda x: x.get("position") or 0)
    data = {"classroom_id": c["id"], "unit_id": u["id"], "section_id": hwsec,
            "exercises": copies}
    lines = [f"homework section {hwsec} of unit {u['id']} — {len(copies)} exercise(s):"]
    for e in copies:
        lines.append(f"  · [{e.get('type')}] {e.get('id')}  {exercise_brief(e)}")
    if not copies:
        lines.append("  (empty — nothing assigned on this unit)")
    out(ctx, data, "\n".join(lines))


@homework.command("available")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Target unit (id or title)")
@click.option("--from-material", "from_material", default=None,
              help="Read the «Lesson» group from this other unit (a material's)")
@click.pass_context
def homework_available(ctx, klass, unit_ref, from_material):
    """Candidate exercises: the unit's «Lesson» group + «Exercises for homework».

    With --from-material the «Lesson» group is read from that unit instead —
    the picker's «Choose from other materials» path (source material, target =
    the classroom unit). All reads carry this class's classroom_id."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    src = from_material or u["id"]
    lesson = a.unit_homework_lesson_exercises(src, classroom_id=c["id"]) or []
    workbook = a.unit_homework_workbook_exercises(u["id"], classroom_id=c["id"]) or []
    assigned = set(a.unit_homework_assigned(u["id"], c["id"]) or [])

    def mark(items):
        return [dict(x, already_assigned=str(x.get("id")) in assigned) for x in items]

    lesson, workbook = mark(lesson), mark(workbook)
    data = {"classroom_id": c["id"], "unit_id": u["id"], "source_unit_id": src,
            "from_material": from_material, "lesson": lesson, "workbook": workbook,
            "assigned_sources": sorted(assigned)}
    lines = []
    for name, items in (("Lesson", lesson), ("Exercises for homework", workbook)):
        lines.append(f"{name}:")
        if not items:
            lines.append("  (none)")
        for x in items:
            label = " ".join(p for p in (x.get("label"), x.get("heading")) if p)
            lines.append(f"  · {x.get('id')}  [{x.get('type')}] {label}"
                         + ("  — already in homework" if x.get("already_assigned") else ""))
    out(ctx, data, "\n".join(lines))


@homework.command("assigned")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.pass_context
def homework_assigned(ctx, klass, unit_ref):
    """What is currently set for homework: GET …/homework/assigned-sources.

    Returns SOURCE ids (the ids `choose`/`remove` operate with). Labels are
    resolved best-effort from the unit's own «Lesson» group — ids that came
    from another material simply stay bare."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    ids = a.unit_homework_assigned(u["id"], c["id"]) or []
    meta = {}
    try:
        meta = {str(x.get("id")): x for x in
                (a.unit_homework_lesson_exercises(u["id"], classroom_id=c["id"]) or [])}
    except LanglistoError:
        meta = {}
    resolved = [{"id": i, "type": (meta.get(i) or {}).get("type"),
                 "label": (meta.get(i) or {}).get("label"),
                 "heading": (meta.get(i) or {}).get("heading")} for i in ids]
    data = {"classroom_id": c["id"], "unit_id": u["id"], "assigned_sources": ids,
            "resolved": resolved}
    lines = [f"assigned to unit {u['id']} ({len(ids)}):"]
    for r in resolved:
        lines.append(f"  · {r['id']}  [{r.get('type') or '?'}] "
                     f"{r.get('label') or r.get('heading') or ''}")
    if not ids:
        lines.append("  (none — nothing set for homework on this unit)")
    out(ctx, data, "\n".join(lines))


@homework.command("choose")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Target unit (id or title)")
@click.option("--exercise", "exercise_refs", multiple=True, required=True,
              help="SOURCE exercise id (repeatable; commas ok)")
@click.option("--from-material", "from_material", default=None,
              help="The sources live in this unit (a material's)")
@click.option("--dry-run", is_flag=True,
              help="Resolve labels + print the exact payload; nothing is written")
@click.pass_context
def homework_choose(ctx, klass, unit_ref, exercise_refs, from_material, dry_run):
    """Assign exercises: POST /units/{unit}/homework/choose {exercise_ids}.

    The server COPIES the chosen exercises into the unit's hidden homework
    section; the student then sees a «Homework» tab in the room. Idempotent:
    re-choosing an already assigned exercise is a 204 no-op."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    ids = split_exercise_values(exercise_refs)
    if not ids:
        err("no exercise ids given")
    src = from_material or u["id"]
    payload = {"exercise_ids": ids}
    if dry_run:
        lesson = a.unit_homework_lesson_exercises(src, classroom_id=c["id"]) or []
        meta = {str(x.get("id")): x for x in lesson}
        resolved = [{"id": i, "found": i in meta,
                     "type": (meta.get(i) or {}).get("type"),
                     "label": (meta.get(i) or {}).get("label"),
                     "heading": (meta.get(i) or {}).get("heading")} for i in ids]
        assigned = a.unit_homework_assigned(u["id"], c["id"]) or []
        data = {"dry_run": True, "classroom_id": c["id"], "unit_id": u["id"],
                "source_unit_id": src, "payload": payload,
                "url": f"/units/{u['id']}/homework/choose?classroom_id={c['id']}",
                "resolved": resolved,
                "already_assigned": [i for i in ids if i in assigned]}
        lines = [f"DRY RUN — POST /units/{u['id']}/homework/choose?classroom_id={c['id']}",
                 f"body: {json.dumps(payload, ensure_ascii=False)}",
                 f"sources ({src}):"]
        for r in resolved:
            lines.append(f"  · {r['id']}  [{r.get('type')}] "
                         f"{r.get('label') or r.get('heading') or ''}  "
                         + ("✓ found" if r["found"] else "⚠ not in this unit's Lesson group"))
        if data["already_assigned"]:
            lines.append("(already in homework — choose would be a no-op for: "
                         + ", ".join(data["already_assigned"]) + ")")
        out(ctx, data, "\n".join(lines))
        return
    a.homework_choose(u["id"], c["id"], ids)
    assigned = a.unit_homework_assigned(u["id"], c["id"]) or []
    missing = [i for i in ids if i not in assigned]
    ok = not missing
    data = {"classroom_id": c["id"], "unit_id": u["id"], "chosen": ids,
            "assigned_sources": assigned, "confirmed": ok, "missing": missing}
    out(ctx, data, f"{'✓' if ok else '⚠'} chose {len(ids)} exercise(s) on unit {u['id']} "
                   f"(assigned now: {len(assigned)})"
                   + ("" if ok else f" — read-back missed: {missing}"))
    if not ok:
        sys.exit(1)


@homework.command("remove")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.option("--exercise", "exercise_ref", required=True,
              help="SOURCE exercise id (the id you chose with)")
@click.pass_context
def homework_remove(ctx, klass, unit_ref, exercise_ref):
    """Unassign one exercise: DELETE /units/{unit}/homework/assigned/{source-id}.

    The argument is the SOURCE id (the same id `choose` took), not a copy id.
    Already-absent ids are a no-op (no call)."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    eid = str(exercise_ref).strip()
    before = a.unit_homework_assigned(u["id"], c["id"]) or []
    if eid not in before:
        data = {"classroom_id": c["id"], "unit_id": u["id"], "exercise_id": eid,
                "removed": False, "reason": "not in homework (no-op)",
                "assigned_sources": before}
        out(ctx, data, f"= {eid} is not in this unit's homework — nothing to do")
        return
    a.homework_remove(u["id"], c["id"], eid)
    after = a.unit_homework_assigned(u["id"], c["id"]) or []
    ok = eid not in after
    data = {"classroom_id": c["id"], "unit_id": u["id"], "exercise_id": eid,
            "removed": ok, "assigned_sources": after}
    out(ctx, data, f"{'✓' if ok else '✗'} removed {eid} from unit {u['id']} homework "
                   f"(assigned now: {len(after)})")
    if not ok:
        sys.exit(1)


@homework.command("clear")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.option("--exercise", "exercise_refs", multiple=True, required=True,
              help="SOURCE exercise ids (repeatable; commas ok)")
@click.pass_context
def homework_clear(ctx, klass, unit_ref, exercise_refs):
    """Unassign several exercises at once: POST /units/{unit}/homework/unassign.

    Only currently assigned ids are sent; others are reported as skipped."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    ids = split_exercise_values(exercise_refs)
    if not ids:
        err("no exercise ids given")
    before = a.unit_homework_assigned(u["id"], c["id"]) or []
    todo = [i for i in ids if i in before]
    skipped = [i for i in ids if i not in before]
    if todo:
        a.homework_unassign(u["id"], c["id"], todo)
    after = a.unit_homework_assigned(u["id"], c["id"]) or []
    still = [i for i in todo if i in after]
    ok = not still
    data = {"classroom_id": c["id"], "unit_id": u["id"], "unassigned": todo,
            "skipped_not_assigned": skipped, "assigned_sources": after, "confirmed": ok}
    out(ctx, data, f"{'✓' if ok else '⚠'} unassigned {len(todo)} exercise(s)"
                   + (f", skipped {len(skipped)} (not assigned)" if skipped else "")
                   + ("" if ok else f" — still assigned: {still}"))
    if not ok:
        sys.exit(1)


@homework.command("review")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.pass_context
def homework_review(ctx, klass, unit_ref):
    """Student submissions for a unit: GET /units/{unit}/homework/submissions."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    subs = a.homework_submissions(u["id"], c["id"]) or []
    names = {str(m.get("id")): m.get("name") for m in (a.classroom_students(c["id"]) or [])}
    for s in subs:
        s["student_name"] = names.get(str(s.get("student_id")))
    data = {"classroom_id": c["id"], "unit_id": u["id"], "submissions": subs}
    lines = []
    for s in subs:
        lines.append(f"{s.get('student_name') or s.get('student_id')}: status={s.get('status')}"
                     f"  submitted_at={s.get('submitted_at')}  completed_at={s.get('completed_at')}"
                     + (f"  comment={s.get('review_comment')!r}" if s.get("review_comment") else ""))
    out(ctx, data, "\n".join(lines) if lines else "(no submissions yet)")


def _submission_of(a, uid, cid, sid):
    subs = a.homework_submissions(uid, cid) or []
    return next((s for s in subs if str(s.get("student_id")) == str(sid)), None)


@homework.command("accept")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.option("--student", "student_ref", required=True, help="Student name or id")
@click.pass_context
def homework_accept(ctx, klass, unit_ref, student_ref):
    """Accept a submission (status → completed): POST …/students/{sid}/complete."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    s = find_class_student(a, c["id"], student_ref)
    a.homework_complete(u["id"], c["id"], s["id"])
    rec = _submission_of(a, u["id"], c["id"], s["id"]) or {}
    ok = rec.get("status") == "completed"
    data = {"classroom_id": c["id"], "unit_id": u["id"], "student_id": s["id"],
            "student_name": s.get("name"), "status": rec.get("status"), "accepted": ok}
    out(ctx, data, f"{'✓' if ok else '✗'} {s.get('name')}: homework "
                   + ("accepted (completed)" if ok else f"status is {rec.get('status')!r}"))
    if not ok:
        sys.exit(1)


@homework.command("return")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.option("--student", "student_ref", required=True, help="Student name or id")
@click.option("--comment", required=True, help="What to fix (the student sees it)")
@click.pass_context
def homework_return(ctx, klass, unit_ref, student_ref, comment):
    """Send the homework back for revision: POST …/students/{sid}/revision."""
    a = api(ctx)
    c = find_class(a, klass)
    u = find_unit(a, c["id"], unit_ref)
    s = find_class_student(a, c["id"], student_ref)
    a.homework_revision(u["id"], c["id"], s["id"], comment)
    rec = _submission_of(a, u["id"], c["id"], s["id"]) or {}
    status_ok = rec.get("status") == "revision"
    comment_ok = (rec.get("review_comment") or "") == comment
    ok = status_ok and comment_ok
    data = {"classroom_id": c["id"], "unit_id": u["id"], "student_id": s["id"],
            "student_name": s.get("name"), "status": rec.get("status"),
            "review_comment": rec.get("review_comment"), "comment_match": comment_ok,
            "returned": ok}
    out(ctx, data, f"{'✓' if ok else '✗'} {s.get('name')}: homework returned for revision"
                   + ("" if comment_ok else f" (comment read-back differs: {rec.get('review_comment')!r})"))
    if not ok:
        sys.exit(1)


@homework.command("submit")
@click.option("--class", "klass", required=True, help="Class name or id")
@click.option("--unit", "unit_ref", required=True, help="Unit id or title")
@click.pass_context
def homework_submit(ctx, klass, unit_ref):
    """DEFERRED — student-side submit needs a STUDENT session, not your token.

    POST /units/{unit}/homework/submit is the student's own action; the CLI
    holds only your teacher token, so nothing is written here. To walk the
    student's flow, open the demo-student room (single-use magic link):

        cli-langlisto preview open --class <cid> --unit <uid> --tab homework

    A student-token submit path (demo magic-link exchange → POST submit) is a
    planned follow-up — see references/homework.md → Deferred."""
    err("homework submit is deferred (student-side): the CLI has no student "
        "session. Use `preview open --class <cid> --unit <uid> --tab homework` "
        "to drive the demo student instead — see references/homework.md.")


# ---------------- sections (order & delete) ----------------

@cli.group()
def sections():
    """Lesson sections: list / reorder / delete.

    Native platform facts (RECON3 §2): section duplicate and section
    visibility DO NOT exist (404 / silently ignored); reorder exists
    (PUT /units/{id}/sections/order, order = array order)."""


@sections.command("list")
@click.option("--unit", "unit_id", required=True, help="Unit id")
@click.option("--class", "klass", default=None, help="Class name/id (classroom-scoped read)")
@click.pass_context
def sections_list(ctx, unit_id, klass):
    """List a unit's sections: GET /units/{uid}/sections."""
    a = api(ctx)
    cid = find_class(a, klass)["id"] if klass else None
    items = sorted(a.unit_sections(unit_id, classroom_id=cid) or [],
                   key=lambda x: x.get("position") or 0)
    data = {"unit_id": unit_id, "classroom_id": cid, "sections": items}
    lines = [f"{s.get('position')}. {s.get('title') or '(untitled)'}  id={s.get('id')}"
             for s in items]
    out(ctx, data, "\n".join(lines) if lines else "(no sections)")


@sections.command("reorder")
@click.option("--unit", "unit_id", required=True, help="Unit id")
@click.option("--ids", required=True, help="Comma-separated section ids, in the desired order")
@click.pass_context
def sections_reorder(ctx, unit_id, ids):
    """Set section order: PUT /units/{uid}/sections/order {section_ids} → 204.

    The order is exactly the array order (positions may stay sparse). Reads
    the order back and exits 1 on a mismatch."""
    a = api(ctx)
    wanted = split_refs(ids)
    if not wanted:
        err("--ids is empty")
    a.unit_sections_order(unit_id, wanted)
    raw = a.unit_sections(unit_id) or []
    by_pos = [s["id"] for s in sorted(raw, key=lambda x: x.get("position") or 0)]
    raw_ids = [s["id"] for s in raw]
    ok = by_pos == wanted or raw_ids == wanted
    data = {"unit_id": unit_id, "requested": wanted, "readback": by_pos, "ok": ok}
    out(ctx, data, f"{'✓' if ok else '⚠'} ordered {len(wanted)} section(s) in unit {unit_id}"
                   + ("" if ok else f" — read-back differs: {by_pos}"))
    if not ok:
        sys.exit(1)


@sections.command("delete")
@click.option("--section", "section_id", required=True, help="Section id")
@click.option("--unit", "unit_id", default=None, help="Read back against this unit's sections")
@click.pass_context
def sections_delete(ctx, section_id, unit_id):
    """Delete a section: DELETE /sections/{id} → 204.

    Destructive: removes the section's exercises AND students' answers to
    them. Pass --unit to read the unit's section list back and confirm."""
    a = api(ctx)
    a.section_delete(section_id)
    if unit_id:
        left = [s for s in (a.unit_sections(unit_id) or [])
                if str(s.get("id")) == str(section_id)]
        ok = not left
        data = {"deleted": section_id, "unit_id": unit_id, "confirmed": ok}
        out(ctx, data, f"{'✓' if ok else '✗'} section {section_id} deleted"
                       + ("" if ok else " (still listed!)"))
        if not ok:
            sys.exit(1)
    else:
        out(ctx, {"deleted": section_id, "confirmed": None},
            f"− section {section_id} deleted (pass --unit to read back)")


# ---------------- units cover (lesson visual) ----------------

@units.group("cover")
def units_cover():
    """Lesson (unit) cover image — PATCH /units/{id} {cover_url}.

    The only native visual of a lesson (RECON3 §3): shows in lesson pickers
    and book lists. Covers of BOOKS are `courses cover`; class avatars and
    room backgrounds are not covered here."""


def _cover_readback(a, unit_id, klass, want):
    """True/False read-back of unit cover_url via the class's unit list; None
    when no --class was given (nothing to read it back from)."""
    if not klass:
        return None
    cid = find_class(a, klass)["id"]
    us = a.units_list(cid) or []
    hit = next((u for u in us if str(u.get("id")) == str(unit_id)), None)
    if hit is None:
        return None
    got = hit.get("cover_url")
    return got in (None, "") if want is None else got == want


@units_cover.command("set")
@click.option("--unit", "unit_id", required=True, help="Unit id")
@click.option("--url", "url", default=None, help="Image URL")
@click.option("--file", "filepath", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Local image: uploaded first, then set")
@click.option("--class", "klass", default=None, help="Read back through this class's unit list")
@click.pass_context
def units_cover_set(ctx, unit_id, url, filepath, klass):
    """Set a lesson cover: upload --file (or pass --url) and PATCH cover_url."""
    if bool(url) == bool(filepath):
        err("pass exactly one of --url or --file")
    a = api(ctx)
    if filepath:
        url = a.upload(filepath, kind="image")
    a.unit_patch(unit_id, cover_url=url)
    confirmed = _cover_readback(a, unit_id, klass, want=url)
    data = {"unit_id": unit_id, "cover_url": url, "confirmed": confirmed}
    if confirmed is None:
        msg = f"· cover_url sent for unit {unit_id}: {url} (pass --class to read it back)"
    elif confirmed:
        msg = f"✓ cover set for unit {unit_id}: {url}"
    else:
        msg = f"✗ cover_url did not persist on unit {unit_id} (sent {url})"
    out(ctx, data, msg)
    if confirmed is False:
        sys.exit(1)


@units_cover.command("clear")
@click.option("--unit", "unit_id", required=True, help="Unit id")
@click.option("--class", "klass", default=None, help="Read back through this class's unit list")
@click.pass_context
def units_cover_clear(ctx, unit_id, klass):
    """Remove a lesson cover (PATCH cover_url: '' → null)."""
    a = api(ctx)
    a.unit_patch(unit_id, cover_url="")
    confirmed = _cover_readback(a, unit_id, klass, want=None)
    data = {"unit_id": unit_id, "cover_url": None, "confirmed": confirmed}
    out(ctx, data, f"✓ cover cleared for unit {unit_id}"
                   if confirmed is not False
                   else f"⚠ cover_url sent as '' but the read-back still shows one")


# ---------------- raw ----------------

@cli.command("raw")
@click.argument("method")
@click.argument("path")
@click.option("--body", default=None, help="JSON body")
@click.pass_context
def raw(ctx, method, path, body):
    """Raw API call: cli-langlisto raw GET /classrooms"""
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
