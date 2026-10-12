"""Offline tests — no network. Run: python3 -m pytest tests/ -q"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_langlisto.core.materials import (  # noqa: E402
    MaterialBuilder, GAP_MARK, build_material,
)
from cli_langlisto.utils.names import translit, email_for  # noqa: E402
from cli_langlisto.utils.timeparse import parse_when  # noqa: E402


class FakeApi:
    """Records every call; enough surface for the builder."""

    def __init__(self):
        self.calls = []
        self._n = 0

    def courses_all(self):
        return [{"id": "course-1", "title": "B1"}]

    def course_lessons(self, cid):
        return []

    def course_add_lesson(self, cid):
        return {"id": "lesson-1"}

    def unit_rename(self, uid, title):
        self.calls.append(("rename", uid, title))

    def section_create(self, uid):
        self._n += 1
        return {"id": f"sec-{self._n}"}

    def section_patch(self, sid, title=None, **kw):
        self.calls.append(("section_title", sid, title))

    def exercise_create(self, sid, ctype):
        self._n += 1
        return {"id": f"ex-{self._n}", "type": ctype}

    def exercise_patch(self, eid, suffix, body):
        self.calls.append(("patch", eid, suffix, body))
        return {"id": eid}

    def exercise_teacher_notes(self, eid, text, visible_to_student=False):
        self.calls.append(("notes", eid, text))

    def upload(self, path, kind="auto"):
        return "https://cdn.example/" + os.path.basename(path)


SPEC = {
    "lesson_title": "Smoke lesson",
    "course": "B1",
    "sections": [
        {"id": "s1", "title": "Warm-Up", "items": [
            {"id": "a1", "type": "open", "prompt_html": "Talk about it.",
             "model_answer": "sample answer"},
            {"id": "a2", "type": "choice", "prompt_html": "Pick:", "options": ["a", "b"], "correct": 1},
            {"id": "a3", "type": "multi_choice", "prompt_html": "Pick two:",
             "options": ["x", "y", "z"], "correct": [0, 2]},
        ]},
        {"id": "s2", "title": "Practice", "items": [
            {"id": "b1", "type": "gap_text",
             "prompt_html": "She [[1]] every day.",
             "gaps": {"1": {"options": ["go", "goes"], "answer": ["goes"]}}},
            {"id": "b2", "type": "short_answer", "number": 4,
             "prompt_html": "How much?", "correct": ["50", "fifty"]},
            {"id": "b3", "type": "match", "prompt_html": "Match:",
             "left": [{"id": "l1", "html": "cat"}], "correct": {"l1": "an animal"}},
            {"id": "b4", "type": "info", "prompt_html": "Just a hint."},
        ]},
    ],
}


def run_build(api, state_path, dry_run=False):
    return build_material(api, SPEC, course=None, state_path=state_path,
                          dry_run=dry_run, pace=0, spec_dir=".", log=lambda *_: None)


def test_dry_run_no_api_calls_and_plan():
    api = FakeApi()
    res = run_build(api, None, dry_run=True)
    assert api.calls == []                       # dry run must not touch the API
    assert res["dry_run"] is True
    assert any("section 'Warm-Up'" in l for l in res["plan"])
    assert any("quiz (grouped)" in l for l in res["plan"])


def test_full_build_mapping():
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "spec.state.json")
        res = run_build(api, state)
        assert res["warnings"] == []
        assert os.path.exists(state)

    patches = [c for c in api.calls if c[0] == "patch"]

    # essay + teacher note
    note_calls = [c for c in api.calls if c[0] == "notes"]
    assert note_calls and note_calls[0][2] == "sample answer"
    essay = next(c for c in patches if c[2] == "/text-block")
    assert essay[3]["body"] == "Talk about it."

    # the two consecutive choice items collapse into ONE quiz in order
    tests = [c for c in patches if c[2] == "/test"]
    assert len(tests) == 1
    qs = tests[0][3]["questions"]
    assert len(qs) == 2
    assert qs[0]["options"][1]["is_correct"] and not qs[0]["options"][0]["is_correct"]
    assert qs[1]["options"][0]["is_correct"] and qs[1]["options"][2]["is_correct"]

    # typed gap: U+FFFC placeholder, answers, derived Box line
    gap = next(c for c in patches if c[2] == "/drag-word-gap" and "goes" in json.dumps(c[3]))
    text = gap[3]["text"]
    assert GAP_MARK in text
    assert "[[1]]" not in text
    assert "Box:" in text and "go" in text and "goes" in text
    assert gap[3]["gaps"][0]["answers"] == ["goes"]
    assert gap[3]["gaps"][0]["correct_indices"] == [0]

    # short answer becomes a trailing typed gap with variant answers
    sa = [c for c in patches if c[2] == "/drag-word-gap" and c[3]["heading"] == "Q4"]
    assert len(sa) == 1
    assert sa[0][3]["gaps"][0]["answers"] == ["50", "fifty"]
    assert sa[0][3]["text"].endswith(GAP_MARK)

    # match pairs
    mp = next(c for c in patches if c[2] == "/match-pairs")
    assert mp[3]["pairs"] == [{"left": "cat", "right": "an animal", "audio_url": ""}]

    # short info note
    note = next(c for c in patches if c[2] == "/note")
    assert note[3]["text"] == "Just a hint."


def test_resume_skips_existing():
    api1 = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "spec.state.json")
        run_build(api1, state)
        first = len([c for c in api1.calls if c[0] == "patch"])
    api2 = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        # fresh state → full work again; compare: same number of patches
        state = os.path.join(d, "spec.state.json")
        run_build(api2, state)
        again = len([c for c in api2.calls if c[0] == "patch"])
        # resume from the same finished state → zero new patches
        api3 = FakeApi()
        run_build(api3, state)
        assert len([c for c in api3.calls if c[0] == "patch"]) == 0
    assert first == again


def test_utils():
    assert translit("Надя") == "nadya"
    assert email_for("Надя", "example.com") == "nadya@example.com"
    assert parse_when("2026-11-03 14:00", "Europe/Istanbul") == "2026-11-03T11:00:00Z"
    assert parse_when("2026-11-03T11:00:00Z") == "2026-11-03T11:00:00Z"


def test_media_upload_dry_and_wet():
    spec = {"lesson_title": "M", "course": "B1", "sections": [
        {"id": "s", "title": "S", "items": [
            {"id": "i", "type": "info", "prompt_html": "Look.", "media": {"image": "pic.png"}}]}]}
    api = FakeApi()
    res = build_material(api, spec, state_path=None, dry_run=True, log=lambda *_: None)
    assert res["plan"] and "pic.png" in " ".join(res["plan"])
    # wet run with no such file: warning, no crash
    api2 = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        r2 = build_material(api2, spec, state_path=os.path.join(d, "st.json"), pace=0,
                            spec_dir=d, log=lambda *_: None)
    assert any("media not found" in w for w in r2["warnings"])
