"""Offline tests for `materials verify` + the core/verify.py engine. No network.

Fixtures mirror the live payload shapes from cli-lab/verify/qa_check.py
(section list items carry FULL exercise payloads on the bulk endpoint).
Run: python3 -m pytest tests/ -q
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from click.testing import CliRunner  # noqa: E402

import cli_langlisto.langlisto_cli as cli_mod  # noqa: E402
from cli_langlisto.core.client import LanglistoError  # noqa: E402
from cli_langlisto.core.verify import (  # noqa: E402
    VerifyError, blob_of, duplicate_scan, verify_course, verify_lesson,
)


# ---------------------------------------------------------------- fixtures
def ex_note(eid, text="A short teaching note.", notes=0):
    return {"id": eid, "type": "note", "text": text,
            "teacher_notes": [{"text": "model"} for _ in range(notes)]}


def ex_article(eid, body="A longer article body about hotels and travel.", notes=0):
    return {"id": eid, "type": "article", "body": body, "heading": "",
            "teacher_notes": [{"text": "model"}] if notes else []}


def ex_gap(eid, answers=("goes",), no_correct=False, n_gaps=1, notes=0):
    gaps = [{"idx": i, "answers": list(answers) if not no_correct else ["—"],
             "no_correct": no_correct, "hint": "", "correct_indices": [0]}
            for i in range(n_gaps)]
    return {"id": eid, "type": "enter_word_gap",
            "text": ("She \ufffc to school. " * n_gaps).strip(),
            "gaps": gaps, "heading": "",
            "teacher_notes": [{"text": "sample"}] if notes else []}


def ex_test(eid, correct=0, n_q=1, notes=0):
    qs = [{"prompt": f"Question {i}?", "options": [
        {"idx": j, "text": f"opt{j}", "is_correct": (j == correct)}
        for j in range(3)]} for i in range(n_q)]
    return {"id": eid, "type": "untimed_test", "questions": qs, "heading": "",
            "teacher_notes": [{"text": "model"}] if notes else []}


def ex_essay(eid, notes=0, body="Talk about your last trip."):
    return {"id": eid, "type": "essay", "body": body, "heading": "",
            "teacher_notes": [{"text": "model answer"}] if notes else []}


def ex_voice(eid, notes=0):
    return {"id": eid, "type": "voice_recording", "heading": "Record yourself",
            "description": "Speak for 90 seconds.", "time_limit_seconds": 90,
            "teacher_notes": [{"text": "sample"}] if notes else []}


def ex_image(eid, caption=True):
    return {"id": eid, "type": "image_stack",
            "items": [{"image_url": "https://cdn.example/i.png",
                       "heading": "Chart" if caption else "",
                       "description": "" if not caption else ""}],
            "heading": ""}


def ex_tf(eid, answer="true"):
    return {"id": eid, "type": "true_false",
            "statements": [{"text": "The sky is blue.", "answer": answer}], "heading": ""}


FIXTURE = {
    "course": {"id": "course-1", "title": "Verify Fixture"},
    "lessons": [
        {"id": "les-1", "title": "Lesson One", "position": 1},
    ],
    "sections": {
        "les-1": [
            {"id": "sec-1", "title": "Warm-Up", "position": 1},
            {"id": "sec-2", "title": "Practice", "position": 2},
        ],
    },
    "exercises": {
        "sec-1": [ex_note("e-note"), ex_image("e-img"), ex_essay("e-essay", notes=1)],
        "sec-2": [ex_gap("e-gap"), ex_test("e-test"), ex_voice("e-voice")],
    },
}


class FakeApi:
    """Duck-typed Langlisto for verify()."""

    def __init__(self, courses=None, lessons=None, sections=None, exercises=None,
                 trial=None):
        self._courses = courses if courses is not None else [FIXTURE["course"]]
        self._lessons = lessons if lessons is not None else FIXTURE["lessons"]
        self._sections = sections if sections is not None else FIXTURE["sections"]
        self._exercises = exercises if exercises is not None else FIXTURE["exercises"]
        self._trial = trial or (lambda eid, answer: {
            "attempts": {"attempts": [{"correct": True}]}})

    def courses_all(self):
        return self._courses

    def course_lessons(self, cid):
        return self._lessons

    def unit_sections(self, uid):
        return self._sections.get(uid, [])

    def section_exercises(self, sid):
        exs = self._exercises.get(sid, [])
        return [dict(e, position=i + 1) for i, e in enumerate(exs)]

    def exercise_get(self, eid):
        for lst in self._exercises.values():
            for e in lst:
                if e["id"] == eid:
                    return e
        raise LanglistoError(404, "not_found", f"no exercise {eid}")

    def exercise_trial_check(self, eid, answer, history=None):
        return self._trial(eid, answer)


def verify(api, **kw):
    kw.setdefault("pace", 0)
    return verify_lesson(api, "les-1", lesson_title="Lesson One",
                         course_id="course-1", **kw)


# ---------------------------------------------------------------- engine tests
def test_verify_clean_lesson_ok():
    res = verify(FakeApi())
    assert res["ok"] is True
    assert res["problems"] == []
    assert res["totals"]["sections"] == 2
    assert res["totals"]["exercises"] == 6
    assert res["totals"]["types"]["enter_word_gap"] == 1
    assert res["totals"]["with_teacher_notes"] == 1    # only the essay carries a note


def test_verify_empty_and_answer_flags():
    exs = {
        "sec-1": [ex_essay("e1", body=""), ex_tf("e2", answer="")],
        "sec-2": [ex_gap("e3", answers=(), no_correct=False),   # no key, not free
                  ex_test("e4", correct=None),                  # no correct option
                  {"id": "e5", "type": "match_pairs", "pairs": [{"left": "a", "right": ""}],
                   "heading": ""}],
    }
    res = verify(FakeApi(exercises=exs))
    assert res["ok"] is False
    kinds = {p["kind"] for p in res["problems"]}
    assert "empty_content" in kinds
    assert "answer_flag" in kinds
    flags = [p["flag"] for p in res["problems"] if p["kind"] == "answer_flag"]
    assert any("no answer" in f for f in flags)
    assert any("NO correct option" in f for f in flags)
    assert any("missing answer" in f for f in flags)
    assert any("empty side" in f for f in flags)


def test_verify_lint_detects_render_hazards():
    exs = {
        "sec-1": [
            {"id": "l1", "type": "note", "text": "<ul><li>one</li></ul>", "heading": ""},
            {"id": "l2", "type": "article", "body": "line1\nline2", "heading": ""},
            {"id": "l3", "type": "note", "text": "see [[1]] marker", "heading": ""},
            {"id": "l4", "type": "note", "text": '<img src="x"> plus color',
             "heading": ""},
        ],
        "sec-2": [
            {"id": "l5", "type": "enter_word_gap", "text": "no marker here",
             "gaps": [{"idx": 0, "answers": ["x"], "no_correct": False,
                       "hint": "", "correct_indices": [0]}], "heading": ""},
        ],
    }
    res = verify(FakeApi(exercises=exs))
    rules = {p["rule"] for p in res["problems"] if p["kind"] == "render_lint"}
    assert {"ul_ol_stripped", "raw_newline", "leftover_marker", "img_stripped",
            "gap_marker_mismatch"} <= rules
    assert res["ok"] is False


def test_verify_captions_are_warnings_not_failures():
    exs = {"sec-1": [ex_image("cap1", caption=False), ex_image("cap2", caption=True)]}
    res = verify(FakeApi(exercises=exs))
    assert res["ok"] is True                       # warnings only
    assert res["totals"]["images"] == 2
    assert res["totals"]["images_without_caption"] == 1
    warns = [w for w in res["warnings"] if w["kind"] == "image_without_caption"]
    assert warns and warns[0]["exercise_id"] == "cap1"
    # --strict escalates
    res2 = verify(FakeApi(exercises=exs), strict=True)
    assert res2["ok"] is False


def test_verify_note_coverage_gate_and_math():
    exs = {"sec-1": [ex_essay("c1", notes=1), ex_essay("c2", notes=0),
                     ex_voice("c3", notes=0), ex_gap("c4", no_correct=True, notes=1)]}
    api = FakeApi(exercises=exs)
    res = verify(api)
    cov = res["notes_coverage"]
    assert cov["essay"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert cov["voice_recording"] == {"covered": 0, "total": 1, "pct": 0.0}
    assert cov["free_gap"] == {"covered": 1, "total": 1, "pct": 100.0}
    assert cov["overall"]["covered"] == 2 and cov["overall"]["total"] == 4
    assert cov["overall"]["pct"] == 50.0
    # gate below actual → pass, no gate problem
    res_ok = verify(api, min_note_coverage=0.4)
    assert res_ok["ok"] is True
    assert res_ok["gate"]["passed"] is True
    # gate above actual → problem + non-ok
    res_bad = verify(api, min_note_coverage=0.9)
    assert res_bad["ok"] is False
    assert any(p["kind"] == "note_coverage_gate" for p in res_bad["problems"])


def test_verify_duplicates_items_and_sections():
    def big_essay(eid, tail=""):
        return {"id": eid, "type": "essay",
                "body": "Write an opinion paragraph of 120-150 words answering the question above." + tail,
                "heading": ""}

    def big_note(eid, tail=""):
        return {"id": eid, "type": "note",
                "text": "Remember to use linking words like however and therefore when stating an opinion." + tail,
                "heading": ""}

    secs = {"les-1": [{"id": "sec-extra-1", "title": "Extra", "position": 1},
                      {"id": "sec-extra-2", "title": "Extra (dup)", "position": 2},
                      {"id": "sec-clean", "title": "Clean", "position": 3}]}
    exs = {
        "sec-extra-1": [big_essay("d1"), big_note("d2"), big_note("d3", " Use a comma before 'but'.")],
        # same content, one section later — the F1 «Extra ×2» shape
        "sec-extra-2": [big_essay("d4"), big_note("d5"), big_note("d6", " Use a comma before 'but'.")],
        "sec-clean": [ex_note("d7", text="Different note entirely, nothing shared here at all.")],
    }
    res = verify(FakeApi(sections=secs, exercises=exs))
    assert res["ok"] is False
    kinds = {p["kind"] for p in res["problems"]}
    assert "duplicate_items" in kinds and "duplicate_section" in kinds
    sec_dupe = next(p for p in res["problems"] if p["kind"] == "duplicate_section")
    assert set(sec_dupe["section_ids"]) == {"sec-extra-1", "sec-extra-2"}
    assert sec_dupe["overlap"] >= 0.9
    # a lesson with unique content has zero duplicates
    assert duplicate_scan([]) == {"items": [], "sections": []}
    body = "Extra - opinion paragraph. Write 120-150 words about brands."
    assert blob_of(ex_essay("x", body=body)) == blob_of(ex_essay("y", body=body))


def test_verify_state_diff():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "bj.state.json")
        json.dump({"lesson_id": "les-1",
                   "exercises": {"a": "e-gap", "b": "ghost-1"},
                   "warnings": ["old warning"]}, open(sp, "w"))
        res = verify(FakeApi(), state_paths=[sp])
        res_strict = verify(FakeApi(), state_paths=[sp], strict=True)
    diff = res["state_diff"][0]
    assert diff["state_exercises"] == 2
    assert diff["live_exercises"] == 6
    assert diff["missing_from_live"] == ["ghost-1"]
    assert set(diff["untracked_live"]) == {"e-note", "e-img", "e-essay", "e-test", "e-voice"}
    assert any(w["kind"] == "state_drift" for w in res["warnings"])
    assert res["ok"] is True  # drift warns by default
    assert res_strict["ok"] is False   # --strict escalates


def test_verify_grading_spot_check():
    # test-type exercise grades correct → ok status, no problem
    res = verify(FakeApi(), grading=True)
    g = {x["exercise_id"]: x for x in res["grading"]}
    assert g["e-test"]["status"] == "ok"
    assert g["e-gap"]["status"] == "ok"              # fake grades everything correct
    assert res["ok"] is True

    def trial_fail(eid, answer):
        return {"attempts": {"attempts": [{"correct": False}]}}

    res_bad = verify(FakeApi(trial=trial_fail), grading=True)
    assert any(p["kind"] == "grading" for p in res_bad["problems"])
    assert res_bad["ok"] is False

    def trial_500(eid, answer):
        raise LanglistoError(500, "internal_error", "Internal server error.")

    res_un = verify(FakeApi(trial=trial_500), grading=True)
    assert all(x["status"] == "unsupported" for x in res_un["grading"])
    assert all(w["kind"] == "grading_unsupported" for w in res_un["warnings"])
    assert res_un["ok"] is True                     # platform quirk, not our defect


def test_verify_course_and_not_found():
    api = FakeApi()
    agg = verify_course(api, "Verify Fixture", pace=0)
    assert agg["ok"] is True and len(agg["lessons"]) == 1
    assert agg["lessons"][0]["lesson_title"] == "Lesson One"
    try:
        verify_course(api, "No Such Book", pace=0)
        assert False, "expected VerifyError"
    except VerifyError as e:
        assert "not found" in str(e)


# ---------------------------------------------------------------- CLI tests
def _run(args, fake, **kw):
    old = cli_mod.Langlisto
    cli_mod.Langlisto = lambda *a, **k: fake
    try:
        return CliRunner().invoke(cli_mod.cli, args, **kw)
    finally:
        cli_mod.Langlisto = old


def test_cli_verify_clean_exit_0_json():
    r = _run(["--json", "materials", "verify", "Verify Fixture", "--pace", "0"], FakeApi())
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["ok"] is True
    assert data["lessons"][0]["totals"]["exercises"] == 6


def test_cli_verify_defects_exit_1_and_gate():
    exs = {"sec-1": [ex_essay("c1", notes=0)], "sec-2": [ex_test("c2", correct=None)]}
    api = FakeApi(exercises=exs)
    r = _run(["--json", "materials", "verify", "Verify Fixture", "--pace", "0"], api)
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False

    r2 = _run(["--json", "materials", "verify", "Verify Fixture", "--pace", "0",
               "--min-note-coverage", "0.9"], FakeApi(exercises=exs))
    assert r2.exit_code == 1
    kinds = {p["kind"] for p in json.loads(r2.output)["problems"]}
    assert "note_coverage_gate" in kinds

    r3 = _run(["materials", "verify", "No Such Book", "--pace", "0"], FakeApi())
    assert r3.exit_code == 2
