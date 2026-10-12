"""Offline CLI-surface tests: materials run/status/tree, exercises, books,
students patch/unenroll/verify, preview. click.testing.CliRunner + stubs, no
network. Run: python3 -m pytest tests/ -q
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from click.testing import CliRunner  # noqa: E402

import cli_langlisto.langlisto_cli as cli_mod  # noqa: E402
from cli_langlisto.core.client import LanglistoError  # noqa: E402


def run(args, fake, **kw):
    old = cli_mod.Langlisto
    cli_mod.Langlisto = lambda *a, **k: fake
    try:
        return CliRunner().invoke(cli_mod.cli, args, **kw)
    finally:
        cli_mod.Langlisto = old


def out_of(res):
    return res.output + (getattr(res, "stderr", "") or "")


class SurfaceApi:
    """Covers every method the new commands touch."""

    def __init__(self):
        self.notes = {}
        self.deleted = []
        self.order_calls = []
        self.unenrolled = []
        self.patch_calls = []
        self.conflict_email = None
        # books
        self.courses = [{"id": "c-1", "title": "CLI Lab"}]
        self.lessons = [
            {"id": "u1", "title": "", "position": 1},
            {"id": "u2", "title": "ZZ Type Catalog", "position": 2},
            {"id": "u3", "title": "", "position": 3},
        ]
        self.sections = {"u1": [], "u2": [{"id": "s-1", "title": "Cat A", "position": 1},
                                          {"id": "s-2", "title": "Cat B", "position": 2}],
                         "u3": [{"id": "s-9", "title": "content", "position": 1}]}
        self.exercises = {
            "s-1": [{"id": "e-1", "type": "note", "text": "hello world", "position": 1},
                    {"id": "e-2", "type": "untimed_test", "questions": [
                        {"prompt": "q?", "options": [{"idx": 0, "text": "a", "is_correct": True}]}],
                     "position": 2}],
            "s-2": [{"id": "e-3", "type": "enter_word_gap", "gaps": [
                        {"idx": 0, "answers": ["goes"], "no_correct": False}],
                     "text": "She \ufffc.", "position": 1}],
        }
        self.modules = []
        # students
        self.students = [{"id": "st-1", "name": "Надя", "email": "nadya@example.com"}]
        self.classrooms = [{"id": "cl-1", "name": "Надя — English", "kind": "individual"}]
        self.members = {"cl-1": [{"id": "st-1", "name": "Надя"}]}

    # ---- courses/books
    def courses_all(self):
        return self.courses

    def course_lessons(self, cid):
        return sorted(self.lessons, key=lambda x: x["position"])

    def course_modules(self, cid):
        return self.modules

    def module_create(self, cid, title=""):
        m = {"id": f"mod-{len(self.modules) + 1}", "title": title}
        self.modules.append(m)
        return m

    def module_patch(self, mid, title):
        for m in self.modules:
            if m["id"] == mid:
                m["title"] = title
        return {"id": mid, "title": title}

    def course_lessons_order(self, cid, entries):
        order = [e["id"] for e in entries]
        rest = [l["id"] for l in sorted(self.lessons, key=lambda x: x["position"])
                if l["id"] not in order]
        pos = {lid: i + 1 for i, lid in enumerate(order + rest)}
        for l in self.lessons:
            if l["id"] in pos:
                l["position"] = pos[l["id"]]

    def unit_sections(self, uid):
        return self.sections.get(uid, [])

    def unit_delete(self, uid):
        self.deleted.append(uid)

    # ---- sections/exercises
    def section_exercises(self, sid):
        return sorted(self.exercises.get(sid, []), key=lambda x: x.get("position") or 0)

    def exercise_get(self, eid):
        for lst in self.exercises.values():
            for e in lst:
                if e["id"] == eid:
                    return dict(e, teacher_notes=(
                        [{"text": self.notes[eid]}] if eid in self.notes else []))
        raise LanglistoError(404, "not_found", eid)

    def exercise_teacher_notes(self, eid, text, visible_to_student=False):
        self.notes[eid] = text

    def exercise_delete(self, eid):
        for sid, lst in self.exercises.items():
            self.exercises[sid] = [e for e in lst if e["id"] != eid]
        self.deleted.append(eid)

    def exercise_patch(self, eid, suffix, body):
        self.patch_calls.append((eid, suffix, body))
        for lst in self.exercises.values():
            for e in lst:
                if e["id"] == eid:
                    e.update(body)
        return {"id": eid}

    def section_exercises_order(self, exercise_ids, classroom_id=None):
        self.order_calls.append(list(exercise_ids))
        for sid, lst in self.exercises.items():
            if {e["id"] for e in lst} & set(exercise_ids):
                for i, eid in enumerate(exercise_ids):
                    for e in lst:
                        if e["id"] == eid:
                            e["position"] = i + 1

    # ---- students/classes
    def students_all(self):
        return self.students

    def student_get(self, sid):
        return next(s for s in self.students if s["id"] == sid)

    def student_patch(self, sid, **fields):
        if fields.get("email") and fields["email"] == self.conflict_email:
            raise LanglistoError(409, "student_email_taken",
                                 "This email is already used.")
        s = self.student_get(sid)
        s.update(fields)
        return s

    def classrooms_all(self):
        return self.classrooms

    def classroom_students(self, cid):
        return self.members.get(cid, [])

    def classroom_remove_student(self, cid, sid):
        self.members[cid] = [s for s in self.members.get(cid, []) if s["id"] != sid]
        self.unenrolled.append((cid, sid))

    # ---- preview
    def demo_student_open(self, next_path=None):
        return {"url": "https://demoview.langlisto.com" + (next_path or "/my-classes")}


# ---------------------------------------------------------------- materials run
def _write_specs(tmp, calls):
    for slug, title in (("alpha", "Alpha"), ("beta", "Beta")):
        json.dump({"lesson_title": title, "course": "CLI Lab", "sections": []},
                  open(os.path.join(tmp, f"{slug}.json"), "w"))

    def fake_build(api, spec, course=None, state_path=None, pace=0.35, media_dir=None,
                   spec_dir=None, log=print):
        calls.append(spec["lesson_title"])
        if spec["lesson_title"] == "Beta":
            raise RuntimeError("boom")
        if state_path:
            json.dump({"lesson_title": spec["lesson_title"],
                       "lesson_id": "lid-" + spec["lesson_title"].lower()},
                      open(state_path, "w"))
        return {"lesson_title": spec["lesson_title"], "sections": 1, "exercises": 3,
                "warnings": [], "plan": []}

    return fake_build


def test_cli_materials_run_resume_retry_limit(monkeypatch, tmp_path):
    calls = []
    fake_build = _write_specs(str(tmp_path), calls)
    monkeypatch.setattr(cli_mod, "build_material", fake_build)
    state = str(tmp_path / "run-state.json")
    api = SurfaceApi()

    # 1st run: one builds, one fails → exit 1, state recorded
    r = run(["--json", "materials", "run", str(tmp_path), "--state", state, "--pace", "0"], api)
    assert r.exit_code == 1, out_of(r)
    st = json.load(open(state))
    assert st["alpha"]["status"] == "done" and st["alpha"]["lesson_id"] == "lid-alpha"
    assert st["beta"]["status"] == "failed"
    assert sorted(st) == ["alpha", "beta"]

    # 2nd run: done skipped, failed skipped without --retry-failed → exit 0
    n0 = len(calls)
    r = run(["materials", "run", str(tmp_path), "--state", state, "--pace", "0"], api)
    assert r.exit_code == 0
    assert len(calls) == n0                       # nothing rebuilt

    # 3rd run: --retry-failed re-attempts beta
    r = run(["materials", "run", str(tmp_path), "--state", state, "--pace", "0",
             "--retry-failed"], api)
    assert len(calls) == n0 + 1 and calls[-1] == "Beta"

    # dry-run shows the plan
    r = run(["--json", "materials", "run", str(tmp_path), "--state", state, "--dry-run"], api)
    assert r.exit_code == 0
    assert json.loads(r.output)["skipped"]

    # --limit caps the number of builds
    st2 = str(tmp_path / "run2.json")
    r = run(["materials", "run", str(tmp_path), "--state", st2, "--pace", "0",
             "--only", "alpha", "--limit", "1"], api)
    assert r.exit_code == 0
    assert list(json.load(open(st2))) == ["alpha"]

    # unknown --only slug is a usage error
    r = run(["materials", "run", str(tmp_path), "--state", state, "--only", "nope"], api)
    assert r.exit_code == 2


def test_cli_materials_run_lock_refused_is_failed(monkeypatch, tmp_path):
    """A build refused by the spec lock writes nothing — the runner must record
    'failed' (retryable), never 'done_with_warnings' (silently skipped).
    A stale lock warning left in the persisted state warnings must NOT fail a
    later completed run (the engine exposes an explicit `locked` signal)."""
    calls = []

    def fake_build(api, spec, course=None, state_path=None, pace=0.35, media_dir=None,
                   spec_dir=None, log=print):
        calls.append(spec["lesson_title"])
        if spec["lesson_title"] == "Locked":
            return {"lesson_title": "Locked", "sections": 0, "exercises": 0,
                    "locked": True,
                    "warnings": ["build lock: another builder is writing this spec (pid 1, started X)"],
                    "plan": []}
        return {"lesson_title": spec["lesson_title"], "sections": 1, "exercises": 3,
                "locked": False,
                "warnings": ["build lock: stale message from an earlier run"],
                "plan": []}

    (tmp_path / "locked.json").write_text(json.dumps(
        {"lesson_title": "Locked", "course": "CLI Lab", "sections": []}))
    (tmp_path / "stale.json").write_text(json.dumps(
        {"lesson_title": "Stale", "course": "CLI Lab", "sections": []}))
    monkeypatch.setattr(cli_mod, "build_material", fake_build)
    state = str(tmp_path / "run-state.json")
    api = SurfaceApi()

    r = run(["materials", "run", str(tmp_path), "--state", state, "--pace", "0"], api)
    assert r.exit_code == 1, out_of(r)
    st = json.load(open(state))
    assert st["locked"]["status"] == "failed", st
    assert "build lock" in st["locked"]["error"]
    assert st["stale"]["status"] == "done_with_warnings", st  # stale text ≠ refusal

    # plain re-run skips the failed spec; --retry-failed re-attempts it
    n0 = len(calls)
    r = run(["materials", "run", str(tmp_path), "--state", state, "--pace", "0"], api)
    assert r.exit_code == 0
    assert len(calls) == n0
    run(["materials", "run", str(tmp_path), "--state", state, "--pace", "0", "--retry-failed"], api)
    assert len(calls) == n0 + 1


# ---------------------------------------------------------------- materials status
def test_cli_materials_status(tmp_path):
    run_state = {
        "A1": {"l1": {"status": "done", "seconds": 10.0},
               "l2": {"status": "failed", "error": "net", "seconds": 2.0}},
        "l3": {"status": "done_with_warnings", "seconds": 8.0, "warnings": ["w1"]},
    }
    (tmp_path / "run-state.json").write_text(json.dumps(run_state))
    (tmp_path / "spec.state.json").write_text(json.dumps(
        {"lesson_title": "L", "lesson_id": "u1", "sections": {"a": "s1"},
         "exercises": {"x": "e1", "y": "e2"}, "warnings": ["media not found"]}))
    r = run(["--json", "materials", "status", "--state", str(tmp_path / "*.json")], SurfaceApi())
    assert r.exit_code == 0, out_of(r)
    data = json.loads(r.output)
    t = data["totals"]
    assert (t["done"], t["done_with_warnings"], t["failed"]) == (1, 1, 1)
    kinds = {f["kind"] for f in data["files"]}
    assert kinds == {"run-state", "build-state"}
    build = next(f for f in data["files"] if f["kind"] == "build-state")
    assert build["exercises"] == 2 and build["warnings"] == 1
    # human output mentions the failed record
    r2 = run(["materials", "status", "--state", str(tmp_path / "run-state.json")], SurfaceApi())
    assert "failed" in r2.output and "A1/l2" in r2.output


# ---------------------------------------------------------------- materials tree
def test_cli_materials_tree_drilldown_json():
    r = run(["--json", "materials", "tree", "CLI Lab"], SurfaceApi())
    assert r.exit_code == 0, out_of(r)
    data = json.loads(r.output)
    assert data["course"]["title"] == "CLI Lab"
    lessons = {l["title"]: l for l in data["lessons"]}
    zz = lessons["ZZ Type Catalog"]
    assert zz["types"] == {"note": 1, "untimed_test": 1, "enter_word_gap": 1}
    ex_ids = [e["id"] for s in zz["sections"] for e in s["exercises"]]
    assert ex_ids == ["e-1", "e-2", "e-3"]
    briefs = {e["id"]: e["brief"] for s in zz["sections"] for e in s["exercises"]}
    assert briefs["e-1"] == "hello world"
    assert "1 questions" in briefs["e-2"] and briefs["e-3"] == "1 gaps"


# ---------------------------------------------------------------- preview
def test_cli_preview_open():
    r = run(["--json", "preview", "open", "--next", "/my-classes"], SurfaceApi())
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["url"].endswith("/my-classes")
    r2 = run(["preview", "open"], SurfaceApi())
    assert "demoview.langlisto.com" in r2.output


# ---------------------------------------------------------------- exercises
def test_cli_exercises_notes_show_delete():
    api = SurfaceApi()
    r = run(["exercises", "notes", "e-1", "--text", "Model: hello.", "--visible-to-student"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.notes["e-1"] == "Model: hello."
    r = run(["--json", "exercises", "show", "e-1"], api)
    assert json.loads(r.output)["teacher_notes"][0]["text"] == "Model: hello."
    # file variant
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("From a file.")
        fname = f.name
    r = run(["exercises", "notes", "e-2", "--from-file", fname], api)
    assert r.exit_code == 0 and api.notes["e-2"] == "From a file."
    # delete + read-back gone
    r = run(["--json", "exercises", "delete", "e-3"], api)
    assert r.exit_code == 0
    assert json.loads(r.output)["confirmed"] is True


def test_cli_exercises_order_and_patch():
    api = SurfaceApi()
    r = run(["--json", "exercises", "order", "--section", "s-1", "--ids", "e-2,e-1"], api)
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["readback"] == ["e-2", "e-1"]
    assert api.order_calls == [["e-2", "e-1"]]

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"heading": "", "text": "new note body", "color": "blue"}, f)
        body_file = f.name
    r = run(["--json", "exercises", "patch", "e-1", "--suffix", "note",
             "--body-file", body_file], api)
    assert r.exit_code == 0
    assert api.patch_calls[-1] == ("e-1", "note", {"heading": "", "text": "new note body",
                                                   "color": "blue"})
    assert json.loads(r.output)["exercise"]["text"] == "new note body"
    # neither --text nor --from-file → usage error
    r = run(["exercises", "notes", "e-1"], api)
    assert r.exit_code == 2


# ---------------------------------------------------------------- books
def test_cli_books_modules_and_lessons_order(tmp_path):
    api = SurfaceApi()
    r = run(["--json", "books", "modules", "add", "CLI Lab", "--title", "Module 1"], api)
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["created"] is True and api.modules[0]["title"] == "Module 1"
    # idempotent
    r = run(["books", "modules", "add", "CLI Lab", "--title", "Module 1"], api)
    assert "exists" in r.output and len(api.modules) == 1
    r = run(["--json", "books", "modules", "list", "CLI Lab"], api)
    assert json.loads(r.output)["modules"][0]["id"] == "mod-1"
    r = run(["books", "modules", "rename", "CLI Lab", "--module", "mod-1",
             "--title", "Module 1 · Basics"], api)
    assert api.modules[0]["title"] == "Module 1 · Basics"

    # lesson order via --ids with read-back
    r = run(["--json", "books", "lessons", "order", "CLI Lab", "--ids", "u3,u1,u2"], api)
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["position_order"] == ["u3", "u1", "u2"]
    # module-aware order via --file
    f = tmp_path / "order.json"
    f.write_text(json.dumps([{"id": "u2", "module_id": "mod-1"},
                             {"id": "u1", "module_id": "mod-1"}]))
    r = run(["books", "lessons", "order", "CLI Lab", "--file", str(f)], api)
    assert r.exit_code == 0
    # both/neither selectors → usage error
    r = run(["books", "lessons", "order", "CLI Lab"], api)
    assert r.exit_code == 2


def test_cli_books_purge_starter():
    api = SurfaceApi()
    # u1 = untitled + no sections → purge; u3 = untitled but has sections → keep
    r = run(["--json", "books", "purge-starter", "CLI Lab", "--dry-run"], api)
    assert r.exit_code == 0, out_of(r)
    data = json.loads(r.output)
    assert [x["id"] for x in data["removed"]] == ["u1"] and not api.deleted
    r = run(["--json", "books", "purge-starter", "CLI Lab"], api)
    assert api.deleted == ["u1"]
    kept = json.loads(r.output)["kept"]
    assert kept and kept[0]["id"] == "u3"


# ---------------------------------------------------------------- students
def test_cli_students_patch_conflict_hint_and_ok():
    api = SurfaceApi()
    api.conflict_email = "taken@example.com"
    r = run(["students", "patch", "Надя", "--email", "taken@example.com"], api)
    assert r.exit_code == 2
    assert "student_email_taken" in out_of(r)
    r = run(["--json", "students", "patch", "Надя", "--email", "nadya.new@example.com",
             "--name", "Надя Н."], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["changed"]["email"] == "nadya.new@example.com"


def test_cli_students_unenroll_verify():
    api = SurfaceApi()
    r = run(["--json", "students", "verify", "Надя", "--class", "Надя — English"], api)
    assert r.exit_code == 0 and json.loads(r.output)["member"] is True
    r = run(["students", "unenroll", "Надя", "--class", "Надя — English"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.unenrolled == [("cl-1", "st-1")]
    r = run(["--json", "students", "verify", "Надя", "--class", "Надя — English"], api)
    assert r.exit_code == 1 and json.loads(r.output)["member"] is False
