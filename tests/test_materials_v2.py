"""Offline tests for the v2 engine — new types, quality features, lint, dedup.

No network. Run from the repo dir:
    uv run --with pytest python3 -m pytest tests/ -q
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_langlisto.core.client import LanglistoError  # noqa: E402
from cli_langlisto.core.dedup import (  # noqa: E402
    BuildLock, BuildLockedError, entries_from_live, entries_from_spec,
    find_duplicates, normalize_text, text_hash,
)
from cli_langlisto.core.materials import build_material  # noqa: E402
from cli_langlisto.core.spec_lint import format_findings, lint_spec  # noqa: E402
from cli_langlisto.core.variants import expand_answer_variants, expand_answers  # noqa: E402

IMG = "https://cdn.example/img.png"
GIF = "https://cdn.example/anim.gif"
AUDIO = "https://cdn.example/clip.wav"
VIDEO = "https://cdn.example/vid.mp4"


class FakeApi:
    """Records every call; enough surface for the builder."""

    def __init__(self):
        self.calls = []
        self._n = 0

    def courses_all(self):
        return [{"id": "course-1", "title": "CLI Lab"}]

    def course_lessons(self, cid):
        return []

    def course_add_lesson(self, cid):
        return {"id": "lesson-1"}

    def unit_rename(self, uid, title):
        self.calls.append(("rename", uid, title))

    def unit_patch(self, uid, **kw):
        self.calls.append(("unit_patch", uid, kw))

    def section_create(self, uid):
        self._n += 1
        return {"id": f"sec-{self._n}"}

    def section_patch(self, sid, title=None, **kw):
        self.calls.append(("section_title", sid, title))

    def exercise_create(self, sid, ctype):
        self._n += 1
        self.calls.append(("create", f"ex-{self._n}", ctype))
        return {"id": f"ex-{self._n}", "type": ctype}

    def exercise_patch(self, eid, suffix, body):
        self.calls.append(("patch", eid, suffix, body))
        return {"id": eid}

    def exercise_teacher_notes(self, eid, text, visible_to_student=False):
        self.calls.append(("notes", eid, text))

    def upload(self, path, kind="auto"):
        return "https://cdn.example/" + os.path.basename(path)


V2_SPEC = {
    "lesson_title": "ZZ Engine Test",
    "course": "CLI Lab",
    "sections": [
        {"id": "s1", "title": "Ordering", "items": [
            {"id": "sort1", "type": "sort_columns", "heading": "Sort the words",
             "columns": ["Fruit", "Vegetable"],
             "words": [{"text": "apple", "column": 0}, {"text": "carrot", "column": 1}]},
            {"id": "so1", "type": "sentence_order", "heading": "Order", "hint": "Put in order",
             "items": ["First sentence.", "Second sentence.", "Third sentence."]},
            {"id": "to1", "type": "text_order", "heading": "Lines", "items": ["Line one", "Line two", "Line three"]},
            {"id": "uns1", "type": "unscramble", "heading": "Unscramble",
             "words": [{"hint": "a fruit", "tiles": ["ap", "ple"]}]},
            {"id": "swg1", "type": "select_word_gap", "heading": "Select",
             "prompt_html": "She [[1]] to school. Follow-up: [[2]]",
             "gaps": {"1": {"answer": ["goes"], "options": ["go", "goes"], "hint": "3rd person"},
                      "2": {"no_correct": True, "sample_answer": "I walk to school."}}},
            {"id": "gt1", "type": "gap_text", "heading": "Contractions",
             "prompt_html": "They [[1]] like it.",
             "gaps": {"1": {"answer": ["don't"], "hint": "short form"}}},
        ]},
        {"id": "s2", "title": "Words & Images", "items": [
            {"id": "ewi1", "type": "enter_word_image", "heading": "Type it",
             "items": [{"image": IMG, "word": "apple", "alternatives": ["an apple"],
                        "options": ["apple", "banana"]}],
             "teacher_note": "Key: apple"},
            {"id": "dwi1", "type": "drag_word_image", "heading": "Drag it",
             "items": [{"image": IMG, "word": "apple"}]},
            {"id": "swi1", "type": "select_word_image", "heading": "Select it",
             "items": [{"image": IMG, "word": "apple", "options": ["apple", "pear"]}]},
            {"id": "car1", "type": "image_carousel", "heading": "Slides",
             "items": [{"image": IMG, "caption": "Slide one"}, {"image": IMG, "caption": "Slide two"}]},
            {"id": "gif1", "type": "gif_animation", "heading": "Animated",
             "items": [{"image": GIF, "caption": "Watch the loop"}]},
        ]},
        {"id": "s3", "title": "Media & Tests", "items": [
            {"id": "dia1", "type": "diagram", "heading": "Label", "image": IMG,
             "description": "Label the picture",
             "points": [{"x": 0.2, "y": 0.3}, {"x": 0.7, "y": 0.6}],
             "cards": [{"text": "Sun", "point_idx": 0}, {"text": "Tree", "point_idx": 1}]},
            {"id": "iv1", "type": "interactive_video", "heading": "Watch", "video_url": VIDEO,
             "description": "Answer while watching",
             "checkpoints": [{"time_seconds": 5, "prompt": "What did he say?",
                              "options": [{"text": "Hello", "is_correct": True},
                                          {"text": "Bye", "is_correct": False}]}]},
            {"id": "amp1", "type": "audio_match_pairs", "heading": "Match the clips",
             "pairs": [{"left": "Clip 1", "right": "hello", "audio": AUDIO},
                       {"left": "Clip 2", "right": "bye", "audio": AUDIO}]},
            {"id": "wl1", "type": "wordlist", "heading": "Vocabulary",
             "words": [{"word": "apple", "translation": "яблоко"}], "teacher_note": "Drill these"},
            {"id": "lk1", "type": "link", "heading": "Read more",
             "description": "Example site", "url": "https://example.com"},
            {"id": "pa1", "type": "photo_answer", "heading": "Photo task",
             "description": "Take a photo of your desk."},
            {"id": "dv1", "type": "divider"},
            {"id": "tt1", "type": "timed_test", "heading": "Speed quiz",
             "time_limit_seconds": 120,
             "questions": [{"prompt": "2+2=?", "options": ["3", "4"], "correct": 1}]},
            {"id": "v1", "type": "voice", "heading": "Record yourself",
             "prompt_html": "Say hello.", "model_answer": "Hello!"},
            {"id": "op1", "type": "open", "heading": "Write",
             "prompt_html": "Write about your day.",
             "model_answer": "I wake up at 7.", "teacher_note": "Check tenses"},
        ]},
    ],
}


def run_build(api, state_path, dry_run=False, spec=None, **kw):
    return build_material(api, spec or V2_SPEC, course=None, state_path=state_path,
                          dry_run=dry_run, pace=0, spec_dir=".", log=lambda *_: None, **kw)


def patches(api, suffix, pred=None):
    """Bodies of all PATCH calls with this endpoint suffix (optionally filtered)."""
    out = [c[3] for c in api.calls if c[0] == "patch" and c[2] == suffix]
    return [b for b in out if pred(b)] if pred else out


def notes(api):
    return [c for c in api.calls if c[0] == "notes"]


def creates(api):
    return [c for c in api.calls if c[0] == "create"]


# ---------------------------------------------------------------- variants
def test_variant_expander_classes():
    case = expand_answer_variants("goes")
    assert case[0] == "goes" and "Goes" in case
    assert "London" in expand_answer_variants("london")  # capitalize
    assert "london" in expand_answer_variants("London")  # lowercase form

    punct = expand_answer_variants("apple.")
    assert punct[0] == "apple." and "apple" in punct

    apos = expand_answer_variants("don\u2019t")  # typographic apostrophe
    assert "don't" in apos                       # normalised
    assert "do not" in apos                      # expanded contraction

    assert "I'm" in expand_answer_variants("I am")   # compression
    assert "will not" in expand_answer_variants("won't")

    num = expand_answer_variants("50")
    assert "fifty" in num
    assert "50" in expand_answer_variants("fifty")
    assert "21" in expand_answer_variants("twenty-one")
    assert "twenty-one" in expand_answer_variants("21.") or "twenty one" in expand_answer_variants("21.")

    # safety: a wrong form must NOT sneak in
    assert "go" not in expand_answer_variants("goes")
    assert "goes" == expand_answer_variants("goes", enabled=False)[0]
    assert expand_answer_variants("goes", enabled=False) == ["goes"]

    multi = expand_answers(["goes", "Goes"])
    assert multi[0] == "goes" and multi.count("Goes") <= 1  # deduplicated


# ---------------------------------------------------------------- mapping
def test_v2_dry_run_plan_and_no_calls():
    api = FakeApi()
    res = run_build(api, None, dry_run=True)
    assert api.calls == []
    joined = " ".join(res["plan"])
    for label in ("sort columns", "sentence order", "unscramble", "word-image (enter)",
                  "image carousel", "gif animation", "diagram", "interactive video",
                  "audio match pairs", "wordlist", "timed quiz", "divider", "voice task"):
        assert label in joined, label
    assert res["lint"]["warn"] == 0 and res["lint"]["error"] == 0


def test_v2_full_build_mapping():
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "st.json")
        res = run_build(api, state)
        assert res["warnings"] == []
        assert os.path.exists(state)
        state_data = json.load(open(state, encoding="utf-8"))
        type_by_id = {c[1]: c[2] for c in creates(api)}

    # -- sort_columns
    sc = patches(api, "/word-columns")[0]
    assert sc["columns"] == [{"title": "Fruit"}, {"title": "Vegetable"}]
    assert sc["words"][0] == {"text": "apple", "column_idx": 0}
    assert sc["words"][1] == {"text": "carrot", "column_idx": 1}
    assert sc["no_correct"] is False

    # -- ordering family
    ordering = patches(api, "/ordering")
    assert len(ordering) == 3
    so = next(b for b in ordering if b["items"])
    assert [i["text"] for i in so["items"]] == ["First sentence.", "Second sentence.", "Third sentence."]
    assert so["hint"] == "Put in order" and so["words"] == []
    uns = next(b for b in ordering if b["words"])
    assert uns["items"] == []
    assert uns["words"][0]["hint"] == "a fruit"
    assert [t["text"] for t in uns["words"][0]["items"]] == ["ap", "ple"]
    assert uns["words"][0]["answer"] == []
    assert type_by_id[state_data["exercises"]["uns1"]] == "unscramble"
    assert type_by_id[state_data["exercises"]["swg1"]] == "select_word_gap"

    # -- select_word_gap (created as its own type, patched as a gap)
    swg = patches(api, "/drag-word-gap", lambda b: b["gaps"][0]["hint"] == "3rd person")[0]
    assert swg["gaps"][0]["answers"][0] == "goes"
    assert swg["gaps"][0]["correct_indices"] == [0]
    assert "Box:" in swg["text"]
    free = swg["gaps"][1]
    assert free["no_correct"] is True and free["answers"] == ["—"] and free["correct_indices"] == []
    assert swg["text"].count("\ufffc") == 2 and "[[2]]" not in swg["text"]

    # -- gap hint + variant expansion pass-through
    gt = patches(api, "/drag-word-gap", lambda b: "like it" in b["text"])[0]
    assert gt["gaps"][0]["hint"] == "short form"
    assert "don't" in gt["gaps"][0]["answers"] and "do not" in gt["gaps"][0]["answers"]

    # -- word-image trio
    wi = patches(api, "/word-image")
    assert len(wi) == 3
    first = next(b for b in wi if b["items"][0]["options"] == ["apple", "banana"])
    assert first["items"][0]["image_url"] == IMG
    assert first["items"][0]["alternatives"] == ["an apple"]
    drag = next(b for b in wi if b["items"][0]["options"] == ["apple"])
    assert drag["items"][0]["word"] == "apple"  # options default = [word]

    # -- carousel / gif
    ib = patches(api, "/image-block")
    assert len(ib) == 2
    car = next(b for b in ib if len(b["items"]) == 2)
    assert [i["description"] for i in car["items"]] == ["Slide one", "Slide two"]
    assert car["items"][0]["image_url"] == IMG
    assert any(c[2] == "image_carousel" for c in creates(api))

    # -- diagram
    dia = patches(api, "/diagram")[0]
    assert dia["image_url"] == IMG and len(dia["points"]) == 2
    assert dia["cards"][0] == {"text": "Sun", "image_url": "", "point_idx": 0}

    # -- interactive video
    iv = patches(api, "/interactive-video")[0]
    assert iv["media_url"] == VIDEO
    assert iv["checkpoints"][0]["time_seconds"] == 5
    assert iv["checkpoints"][0]["options"][0]["is_correct"] is True

    # -- audio match pairs
    amp = patches(api, "/match-pairs")[0]
    assert amp["pairs"][0] == {"left": "Clip 1", "right": "hello", "audio_url": AUDIO}
    assert any(c[2] == "audio_match_pairs" for c in creates(api))

    # -- wordlist / link / photo_answer / timed_test
    assert patches(api, "/wordlist")[0]["words"] == [{"word": "apple", "translation": "яблоко"}]
    assert patches(api, "/link")[0]["url"] == "https://example.com"
    assert patches(api, "/photo-answer")[0]["description"] == "Take a photo of your desk."
    tt = patches(api, "/test")[0]
    assert tt["time_limit_seconds"] == 120
    assert tt["questions"][0]["options"][1]["is_correct"] is True

    # -- divider: created as its own type, never patched
    dv_eid = state_data["exercises"]["dv1"]
    assert type_by_id[dv_eid] == "divider"
    assert not [c for c in api.calls if c[0] == "patch" and c[1] == dv_eid]

    # -- teacher notes everywhere (incl. voice model answer + free-gap sample)
    ntexts = [c[2] for c in notes(api)]
    assert "Key: apple" in ntexts                    # word-image item note
    assert "Drill these" in ntexts                   # wordlist note
    assert "Hello!" in ntexts                        # voice model answer
    assert any(t.startswith("I wake up at 7.") and "Check tenses" in t for t in ntexts)  # open merge
    assert any("Sample answer (gap 2)" in t for t in ntexts)  # free gap sample → note


def test_v2_resume_never_duplicates():
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "st.json")
        run_build(api, state)
        first_patches = len([c for c in api.calls if c[0] == "patch"])
        api2 = FakeApi()
        res2 = run_build(api2, state)
        assert len([c for c in api2.calls if c[0] == "patch"]) == 0
        assert len([c for c in api2.calls if c[0] == "create"]) == 0
        assert res2["duplicates"] == []
        assert res2["warnings"] == []
    assert first_patches >= 10


def test_v2_expand_optout_keeps_authored_key():
    spec = {"lesson_title": "T", "course": "CLI Lab", "sections": [
        {"id": "s", "title": "S", "items": [
            {"id": "g", "type": "gap_text", "prompt_html": "She [[1]].",
             "expand_variants": False,
             "gaps": {"1": {"answer": ["goes"]}}}]}]}
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        run_build(api, os.path.join(d, "st.json"), spec=spec)
    g = patches(api, "/drag-word-gap")[0]
    assert g["gaps"][0]["answers"] == ["goes"]


def test_media_local_path_uploaded_for_images():
    spec = {"lesson_title": "T", "course": "CLI Lab", "sections": [
        {"id": "s", "title": "S", "items": [
            {"id": "im1", "type": "image_stack", "heading": "Pics",
             "items": [{"image": "pic.png", "caption": "A picture"}]}]}]}
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "pic.png"), "wb").write(b"\x89PNG")
        res = build_material(api, spec, state_path=os.path.join(d, "st.json"), pace=0,
                             spec_dir=d, log=lambda *_: None)
    assert res["warnings"] == []
    body = patches(api, "/image-block")[0]
    assert body["items"][0]["image_url"] == "https://cdn.example/pic.png"
    assert body["items"][0]["description"] == "A picture"


def test_dry_run_with_existing_state_and_no_api_never_calls_api():
    """The CLI passes api=None on --dry-run; a loaded state must not crash it."""
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "st.json")
        run_build(api, state)                      # a completed real build
        calls_before = len(api.calls)
        res = build_material(None, V2_SPEC, state_path=state, dry_run=True, pace=0,
                             spec_dir=".", log=lambda *_: None)
        assert res["dry_run"] is True
        assert len(api.calls) == calls_before      # nothing new was touched


# ---------------------------------------------------------------- lint
def _lint(items, **spec_extra):
    spec = {"lesson_title": "L", "course": "CLI Lab",
            "sections": [{"id": "s", "title": "S", "items": items}], **spec_extra}
    return lint_spec(spec)


def _codes(findings):
    return {f["code"] for f in findings}


def test_lint_catches_render_model_faults():
    f = _lint([
        {"id": "a", "type": "open", "prompt_html": "Line1\nLine2"},
        {"id": "b", "type": "open", "prompt_html": "<ul><li>x</li></ul>"},
        {"id": "c", "type": "open", "prompt_html": "<h2>Title</h2> text"},
        {"id": "d", "type": "open", "prompt_html": "Look <img src='x.png'> here"},
        {"id": "e", "type": "open", "prompt_html": "<span style=\"color:red\">bad</span>"},
        {"id": "f", "type": "open", "prompt_html": "a&nbsp;b"},
    ])
    codes = _codes(f)
    assert {"raw_newline", "ul_ol_stripped", "h_tag_dropped", "img_stripped",
            "style_stripped", "nbsp_entity"} <= codes
    sev = {f_["code"]: f_["severity"] for f_ in f}
    assert sev["raw_newline"] == "error" and sev["ul_ol_stripped"] == "error"
    assert sev["h_tag_dropped"] == "warn" and sev["nbsp_entity"] == "info"
    assert format_findings(f)  # human lines render


def test_lint_gap_markers_and_box_rules():
    bad = _lint([{"id": "g", "type": "gap_text", "prompt_html": "A [[1]] B",
                  "gaps": {"1": {"answer": ["x"]}, "2": {"answer": ["y"]}}}])
    assert "gap_marker_mismatch" in _codes(bad)

    # false friend: "￼ box do you prefer" must NOT trigger the word-bank rule
    friend = _lint([{"id": "g", "type": "gap_text",
                     "prompt_html": "Which \ufffc box do you prefer?",
                     "gaps": {"1": {"answer": ["one"]}}}])
    assert "box_mentioned_no_options" not in _codes(friend)

    # a real instruction without options IS flagged
    real = _lint([{"id": "g", "type": "gap_text",
                   "prompt_html": "Choose from the box. She [[1]] here.",
                   "gaps": {"1": {"answer": ["goes"]}}}])
    assert "box_mentioned_no_options" in _codes(real)

    # markers on a non-gap item are leftover spec syntax
    left = _lint([{"id": "n", "type": "info", "prompt_html": "Hello [[1]]"}])
    assert "leftover_marker" in _codes(left)


def test_lint_quality_and_structured_rules():
    f = _lint([
        {"id": "v", "type": "voice", "prompt_html": "Say hi."},
        {"id": "c1", "type": "image_carousel", "heading": "One",
         "items": [{"image": IMG}]},
        {"id": "sc", "type": "sort_columns", "columns": ["A"],
         "words": [{"text": "w", "column": 3}]},
        {"id": "di", "type": "diagram", "image": IMG,
         "points": [{"x": 0.1, "y": 0.1}], "cards": [{"text": "t", "point_idx": 5}]},
        {"id": "tt", "type": "timed_test", "questions": []},
        {"id": "u", "type": "unscramble", "words": [{"tiles": ["one"]}]},
    ])
    codes = _codes(f)
    assert {"voice_no_heading", "carousel_too_few_items", "image_missing_caption",
            "sort_column_range", "diagram_point_range", "timed_test_no_limit",
            "unscramble_no_tiles"} <= codes
    for code in ("sort_column_range", "diagram_point_range", "unscramble_no_tiles"):
        assert next(f_ for f_ in f if f_["code"] == code)["severity"] == "error"


def test_lint_single_variant_flag_and_description():
    f = _lint([{"id": "g", "type": "gap_text", "prompt_html": "She [[1]].",
                "gaps": {"1": {"answer": ["goes"]}}}])
    single = [f_ for f_ in f if f_["code"] == "gap_single_variant"]
    assert single and single[0]["severity"] == "info"  # auto-expansion covers it

    f2 = _lint([{"id": "g", "type": "gap_text", "prompt_html": "She [[1]].",
                 "expand_variants": False, "gaps": {"1": {"answer": ["goes"]}}}])
    single2 = [f_ for f_ in f2 if f_["code"] == "gap_single_variant"]
    assert single2 and single2[0]["severity"] == "warn"  # nothing will save it

    f3 = _lint([{"id": "n", "type": "info", "prompt_html": "Hi"}],
               lesson_description="<p>Tags show as code</p>")
    assert "lesson_description_html" in _codes(f3)

    f4 = _lint([{"id": "x", "type": "bogus", "prompt_html": "?"}])
    assert "unsupported_type" in _codes(f4)


def test_lint_gate_aborts_before_any_write():
    spec = {"lesson_title": "X", "course": "CLI Lab", "sections": [
        {"id": "s", "title": "S", "items": [
            {"id": "i1", "type": "open", "prompt_html": "Line1\nLine2"}]}]}
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(LanglistoError) as ei:
            build_material(api, spec, state_path=os.path.join(d, "st.json"), pace=0,
                           lint_fail_on=["error"], log=lambda *_: None)
        assert ei.value.code == "lint_failed"
        assert not os.path.exists(os.path.join(d, "st.json.lock"))  # lock released
    assert api.calls == []  # zero writes


# ---------------------------------------------------------------- dedup guard
def test_dedup_normalization_and_find_duplicates():
    assert normalize_text("<p>She  goes!</p>") == "she goes"
    entries = [
        {"id": "a", "text": "She goes to school every day."},
        {"id": "b", "text": "<p>She  goes to school every day.</p>"},
        {"id": "c", "text": "A totally different sentence."},
        {"id": "d", "text": "True"},
    ]
    dups = find_duplicates(entries)
    assert len(dups) == 1 and set(dups[0]["ids"]) == {"a", "b"}
    assert find_duplicates([{"id": "x", "text": "True"}]) == []
    assert text_hash("She goes!") == text_hash("<b>she  goes</b>")

    live = entries_from_live([{"id": "e1", "text": "Hello world, my friend."},
                              {"id": "e2", "text": "hello WORLD my friend"}])
    assert find_duplicates(live)[0]["ids"] == ["e1", "e2"]
    assert entries_from_spec([{"id": "s1", "prompt_html": "Hi there"}])[0]["id"] == "s1"


def test_build_warns_on_duplicate_items():
    spec = {"lesson_title": "D", "course": "CLI Lab", "sections": [
        {"id": "s", "title": "S", "items": [
            {"id": "d1", "type": "info", "prompt_html": "Same short text here."},
            {"id": "d2", "type": "info", "prompt_html": "Same short text here."}]}]}
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        res = run_build(api, os.path.join(d, "st.json"), spec=spec)
    assert any("duplicate content of d1" in w for w in res["warnings"])
    assert res["duplicates"] and res["duplicates"][0]["items"] == ["d1", "d2"]


def test_build_lock_blocks_live_holder_and_recovers_from_stale():
    spec = {"lesson_title": "L", "course": "CLI Lab", "sections": [
        {"id": "s", "title": "S", "items": [{"id": "n", "type": "info", "prompt_html": "Hi"}]}]}
    # live holder (our own pid) -> refuse to start, no writes
    api = FakeApi()
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "st.json")
        with open(state + ".lock", "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": "now"}, f)
        res = run_build(api, state, spec=spec)
        assert api.calls == []
        assert any("build lock" in w for w in res["warnings"])
        assert res["locked"] is True                        # explicit refusal signal
        # stale holder (dead pid) -> take over, build, release
        with open(state + ".lock", "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "started_at": "long ago"}, f)
        res2 = run_build(api, state, spec=spec)
        assert len(creates(api)) == 1                       # stale lock taken over → build ran
        assert res2["warnings"] == res["warnings"]          # no NEW warnings (state keeps history)
        assert res2["locked"] is False                      # completed run is not a refusal
        assert not os.path.exists(state + ".lock")          # released after the build

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.lock")
        lock = BuildLock(path)
        lock.acquire(owner="t")
        with pytest.raises(BuildLockedError):
            BuildLock(path).acquire()
        lock.release()
        assert not os.path.exists(path)


def test_uploads_pdf_helper_present():
    from cli_langlisto.core.uploads import upload_pdf

    class C:
        BASE = "https://x"
        token = "t"
        UA = "u"
        ORIGIN = "o"

    with pytest.raises(LanglistoError):
        upload_pdf(C(), "/no/such/file.pdf")
