"""Live verification for material lessons (the `materials verify` engine).

READ-ONLY by default: counts, type distribution, empty content, answer-key
flags, captions, teacher-notes coverage, duplicate detection (normalized text
hashes), optional state↔live diff and the render-safety lint — all computed
from a handful of bulk GETs (`/units/{id}/sections` + `/sections/{sid}/exercises`,
which return FULL items incl. answers — no per-exercise drill-down needed;
`deep=True` still fetches each exercise individually for belt-and-braces).

Optional extras (opt-in flags on the CLI):
  * `grading=True`  — POST /exercises/{id}/trial-check spot-checks the key of
    test-type exercises (verified live 2026-10-13: `{"answer":[{"question_idx":
    q,"option_idx":a}], "history":[]}` → `attempts.attempts[].correct`).
    Gap-type trial-check returns 500 `internal_error` on every probed shape —
    reported as `unsupported`, never as a problem (platform quirk).
  * `strict=True`   — warnings (missing image captions, state↔live drift)
    escalate to failures.

The engine returns plain JSON-able dicts; `ok` is False when any problem was
found (the CLI exits 1 in that case, honouring `--min-note-coverage`).
Result contract: exit 0 = clean, 1 = problems, 2 = usage error.

Ported & extended from cli-lab/verify/qa_check.py (W1-3, H4/H8/H12) — the
render-model lint rules and the per-type content/answer probes are kept 1:1
with that verified source; extensions: captions, notes-coverage KPI + gate,
duplicates, state diff, grading spot-check.
"""

import glob as _glob
import hashlib
import json
import re
import time

# ---------- verified render model (keep in sync with qa_check.py) ----------
U_FFFC = "\ufffc"  # gap placeholder on the platform
LOSSY_TAG_RE = re.compile(r"<\s*(ul|ol|li)\b[^>]*>", re.I)
HEADING_TAG_RE = re.compile(r"<\s*h[1-6]\b[^>]*>", re.I)
IMG_TAG_RE = re.compile(r"<\s*img\b[^>]*>", re.I)
STYLE_ATTR_RE = re.compile(r"style\s*=\s*[\"'][^\"']*(color|background)[^\"']*[\"']", re.I)
FONT_COLOR_RE = re.compile(r"<\s*font\b[^>]*color\s*=", re.I)
LEFTOVER_MARKER_RE = re.compile(r"\[\[\s*\d+\s*\]\]")
_TAG_RE = re.compile(r"<[^>]+>")

HTML_FIELDS = {
    "note": ["text"],
    "article": ["body", "heading", "subheading"],
    "essay": ["body", "heading", "subheading"],
    "text": ["body", "heading", "subheading"],
    "enter_word_gap": ["text", "heading"],
    "drag_word_gap": ["text", "heading"],
    "select_word_gap": ["text", "heading"],
    "untimed_test": ["heading"],
    "timed_test": ["heading"],
    "true_false": ["heading"],
    "match_pairs": ["heading"],
    "image_stack": ["heading"],
    "media_clip": ["heading"],
    "voice_recording": ["description", "heading"],
    "sort_columns": ["heading"],
    "video": ["heading"],
    "audio": ["heading"],
}
GAP_TYPES = {"enter_word_gap", "drag_word_gap", "select_word_gap"}
TEST_TYPES = {"untimed_test", "timed_test"}


class VerifyError(RuntimeError):
    """Course/lesson not found or an unusable state file."""


def _clean(s):
    return (s or "").strip()


def split_refs(value):
    """'a,b, c' -> ['a','b','c'] (empty entries dropped)."""
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


# ---------------------------------------------------------------- lint
def lint_exercise(ex):
    """Render-safety lint hits (list of dicts) for one exercise payload."""
    t = ex.get("type") or ""
    eid = ex.get("id")
    hits = []

    def add(rule, detail, field=None):
        hits.append({"exercise_id": eid, "type": t, "rule": rule,
                     "field": field, "detail": detail})

    fields = HTML_FIELDS.get(t, ["heading"])
    pairs = [(f, ex.get(f)) for f in fields]
    if t in TEST_TYPES:
        for qi, q in enumerate(ex.get("questions") or []):
            pairs.append((f"questions[{qi}].prompt", q.get("prompt")))
            for oi, o in enumerate(q.get("options") or []):
                pairs.append((f"questions[{qi}].options[{oi}].text", o.get("text")))
    if t == "match_pairs":
        for pi, p in enumerate(ex.get("pairs") or []):
            pairs.append((f"pairs[{pi}].left", p.get("left")))
            pairs.append((f"pairs[{pi}].right", p.get("right")))
    if t in GAP_TYPES:
        for gi, g in enumerate(ex.get("gaps") or []):
            pairs.append((f"gaps[{gi}].hint", g.get("hint")))

    for fname, val in pairs:
        if not isinstance(val, str) or not val:
            continue
        if LOSSY_TAG_RE.search(val):
            add("ul_ol_stripped", "list tag will be stripped (items glue) — use '• ' lines", fname)
        if HEADING_TAG_RE.search(val):
            add("h_tag_dropped", "h1-h6 tag dropped (text kept) — keep headings unstyled", fname)
        if IMG_TAG_RE.search(val):
            add("img_stripped", "<img> inside text is stripped — use image_stack item", fname)
        if STYLE_ATTR_RE.search(val):
            add("style_stripped", "color/background style is stripped", fname)
        if FONT_COLOR_RE.search(val):
            add("font_color_stripped", "<font color> is stripped", fname)
        if "\n" in val or "\r" in val:
            add("raw_newline", "raw \\n in HTML field (container pre-line) — use <br>", fname)
        m = LEFTOVER_MARKER_RE.search(val)
        if m:
            add("leftover_marker", f"spec marker {m.group(0)} left in text", fname)

    if t in GAP_TYPES:
        gap_n = len(ex.get("gaps") or [])
        marker_n = (ex.get("text") or "").count(U_FFFC)
        if gap_n != marker_n:
            add("gap_marker_mismatch",
                f"U+FFFC markers in text = {marker_n}, gaps list = {gap_n}")
    return hits


# ---------------------------------------------------------------- content probes
def has_free_gap(ex):
    """True when the exercise has a free-response gap (no key by design)."""
    if ex.get("type") not in GAP_TYPES:
        return False
    for g in ex.get("gaps") or []:
        answers = [a for a in (g.get("answers") or []) if _clean(a)]
        if g.get("no_correct") or not answers:
            return True
    return False


def content_probe(ex):
    """(content_desc|None, answer_flags, notes) for one exercise payload.

    Ported 1:1 from qa_check.py content_probe (verified type→field model)."""
    t = ex.get("type") or ""
    flags = []
    content = None

    def get(p, d=None):
        return ex.get(p, d)

    if t == "note":
        content = _clean(get("text"))
    elif t in ("article", "essay", "text"):
        content = _clean(get("body"))
    elif t in GAP_TYPES:
        gaps = get("gaps") or []
        content = f"{len(gaps)} gaps / {len(_clean(get('text')))} char text"
        for g in gaps:
            ans = [a for a in (g.get("answers") or []) if _clean(a)]
            if not ans and not g.get("no_correct"):
                flags.append(f"gap {g.get('idx')}: no answer and no_correct=false")
            if g.get("no_correct") and not (g.get("answers") or []):
                flags.append(f"gap {g.get('idx')}: free gap without placeholder answers item")
    elif t == "match_pairs":
        pairs = get("pairs") or []
        content = f"{len(pairs)} pairs"
        for i, p in enumerate(pairs):
            if not _clean(p.get("left")) or not _clean(p.get("right")):
                flags.append(f"pair {i}: empty side")
    elif t in TEST_TYPES:
        qs = get("questions") or []
        content = f"{len(qs)} questions"
        for qi, q in enumerate(qs):
            if not _clean(q.get("prompt")):
                flags.append(f"q{qi}: empty prompt")
            opts = q.get("options") or []
            if not opts:
                flags.append(f"q{qi}: no options")
            elif not any(o.get("is_correct") for o in opts):
                flags.append(f"q{qi}: NO correct option (answer missing or student-token used)")
            if any(not _clean(o.get("text")) for o in opts):
                flags.append(f"q{qi}: empty option text")
    elif t == "true_false":
        sts = get("statements") or []
        content = f"{len(sts)} statements"
        for si, s in enumerate(sts):
            if not _clean(s.get("text")):
                flags.append(f"s{si}: empty statement")
            if s.get("answer") in (None, ""):
                flags.append(f"s{si}: missing answer")
    elif t == "sort_columns":
        words = get("words") or []
        cols = get("columns") or []
        content = f"{len(cols)} columns / {len(words)} words"
        for wi, w in enumerate(words):
            if not _clean(w.get("text")):
                flags.append(f"w{wi}: empty word")
            if w.get("column_idx") in (None, "") and not ex.get("no_correct") and not w.get("no_correct"):
                flags.append(f"w{wi}: no column_idx")
    elif t == "image_stack":
        items = get("items") or []
        content = f"{len(items)} images" if items else None
    elif t in ("media_clip", "audio", "video"):
        clips = get("clips") or []
        url = _clean(get("media_url"))
        content = (f"{len(clips)} clips" if clips else None) or (url or None)
    elif t == "voice_recording":
        content = _clean(get("heading")) or _clean(get("description"))
    elif t == "divider":
        content = "divider"  # structural item — no content by design
    else:
        content = _clean(get("body")) or _clean(get("text")) or _clean(get("heading")) or "?"
        if content == "?":
            flags.append(f"unknown type '{t}': cannot judge content")

    notes = [n for n in (get("teacher_notes") or [])
             if isinstance(n, dict) and _clean(n.get("text"))]
    return (content if _clean(content) else None), flags, notes


def caption_flags(ex):
    """image_stack items with an image but no caption (heading/description)."""
    if ex.get("type") != "image_stack":
        return []
    out = []
    for i, item in enumerate(ex.get("items") or []):
        if not isinstance(item, dict):
            continue
        if not _clean(item.get("image_url")):
            continue
        if not _clean(item.get("heading")) and not _clean(item.get("description")):
            out.append(f"image {i}: no caption (heading/description empty)")
    return out


# ---------------------------------------------------------------- duplicates
def norm_text(s):
    s = _TAG_RE.sub(" ", s or "")
    s = s.replace(U_FFFC, " · ")
    return " ".join(s.split()).casefold()


def blob_of(ex):
    parts = []
    for f in ("heading", "subheading", "text", "body"):
        v = ex.get(f)
        if isinstance(v, str):
            parts.append(v)
    for q in ex.get("questions") or []:
        parts.append(q.get("prompt") or "")
        for o in q.get("options") or []:
            parts.append(o.get("text") or "")
    for p in ex.get("pairs") or []:
        parts.append(p.get("left") or "")
        parts.append(p.get("right") or "")
    for s in ex.get("statements") or []:
        parts.append(s.get("text") or "")
    for it in ex.get("items") or []:
        parts.append((it.get("heading") or "") if isinstance(it, dict) else "")
        parts.append((it.get("description") or "") if isinstance(it, dict) else "")
    blob = norm_text(" ".join(parts))
    return blob


def blob_hash(blob):
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def duplicate_scan(exercises):
    """exercises: [{id,type,section,section_id,blob}] → {'items':[…], 'sections':[…]}"""
    by_hash = {}
    for e in exercises:
        if len(e["blob"]) < 40:
            continue
        by_hash.setdefault(blob_hash(e["blob"]), []).append(e)
    items = [{"hash": h, "ids": [e["id"] for e in group],
              "types": sorted({e["type"] for e in group}),
              "sections": sorted({e["section"] for e in group})}
             for h, group in by_hash.items() if len(group) > 1]

    # section-level: jaccard of item-blob sets ≥ 0.9 (min 3 items each)
    secs, titles = {}, {}
    for e in exercises:
        if len(e["blob"]) >= 40:
            secs.setdefault(e["section_id"], set()).add(blob_hash(e["blob"]))
            titles[e["section_id"]] = e["section"]
    names = sorted(secs)
    sections = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, sb = secs[a], secs[b]
            if len(sa) < 3 or len(sb) < 3:
                continue
            j = len(sa & sb) / len(sa | sb)
            if j >= 0.9:
                sections.append({"section_ids": [a, b],
                                 "sections": [titles.get(a), titles.get(b)],
                                 "overlap": round(j, 3),
                                 "shared_items": len(sa & sb)})
    return {"items": items, "sections": sections}


# ---------------------------------------------------------------- coverage
def note_coverage(categories):
    """categories: {name: [covered_bool,…]} → per-category + overall."""
    out, cov_all, tot_all = {}, 0, 0
    for name, flags in categories.items():
        covered = sum(1 for f in flags if f)
        total = len(flags)
        cov_all += covered
        tot_all += total
        out[name] = {"covered": covered, "total": total,
                     "pct": round(100.0 * covered / total, 1) if total else None}
    out["overall"] = {"covered": cov_all, "total": tot_all,
                      "pct": round(100.0 * cov_all / tot_all, 1) if tot_all else None}
    return out


# ---------------------------------------------------------------- state diff
def expand_state_paths(patterns):
    files = []
    for p in patterns:
        if any(ch in p for ch in "*?["):
            files.extend(sorted(_glob.glob(p)))
        else:
            files.append(p)
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def state_diff(state_path, live_ids_by_lesson):
    """Compare a builder state file's recorded exercise ids with live ones.

    live_ids_by_lesson: {lesson_id: [exercise_id,…]}. The state's own
    `lesson_id` scopes the diff when present."""
    try:
        st = json.load(open(state_path, encoding="utf-8"))
    except Exception as e:
        return {"path": state_path, "error": f"cannot read state: {e}"}
    state_ids = set((st.get("exercises") or {}).values())
    lid = st.get("lesson_id")
    if lid and lid in live_ids_by_lesson:
        live_ids = set(live_ids_by_lesson[lid])
    else:
        live_ids = set()
        for ids in live_ids_by_lesson.values():
            live_ids |= set(ids)
    missing = sorted(state_ids - live_ids)
    untracked = sorted(live_ids - state_ids)
    return {
        "path": state_path,
        "lesson_id": lid,
        "state_exercises": len(state_ids),
        "live_exercises": len(live_ids),
        "missing_from_live": missing,
        "untracked_live": untracked,
        "warnings": len(st.get("warnings") or []),
    }


# ---------------------------------------------------------------- grading
def _trial_answer(ex):
    """Best-effort trial-check answer for an exercise, or None (unsupported)."""
    t = ex.get("type") or ""
    if t in TEST_TYPES:
        answer = []
        for qi, q in enumerate(ex.get("questions") or []):
            correct = next((o.get("idx") for o in (q.get("options") or [])
                            if o.get("is_correct")), None)
            if correct is None:
                return None
            answer.append({"question_idx": qi, "option_idx": correct})
        return answer or None
    if t in GAP_TYPES:
        answer = []
        for g in ex.get("gaps") or []:
            key = next((a for a in (g.get("answers") or []) if _clean(a)), None)
            if key is None:
                return None
            answer.append({"idx": g.get("idx", 0), "answer": key})
        return answer or None
    return None


def grading_spot_check(api, ex, pace=0.0):
    """One trial-check for a graded exercise → result dict.

    test types: verified shape, expects `attempts…correct: true` for the
    first keyed option. gap types: probed shapes all return 500 on materials
    (2026-10-13) → 'unsupported'."""
    answer = _trial_answer(ex)
    if answer is None:
        return {"exercise_id": ex.get("id"), "type": ex.get("type"),
                "status": "skipped", "detail": "no keyed answer to probe"}
    try:
        res = api.exercise_trial_check(ex["id"], answer)
    except Exception as e:  # noqa: BLE001 — network/HTTP errors become statuses
        status = getattr(e, "status", 0)
        detail = f"{getattr(e, 'code', '')}: {getattr(e, 'message', str(e))}".strip(": ")
        return {"exercise_id": ex.get("id"), "type": ex.get("type"),
                "status": "unsupported" if status >= 400 else "error",
                "detail": detail[:200]}
    if pace:
        time.sleep(pace)
    verdicts = []
    attempts = res.get("attempts") if isinstance(res, dict) else None
    if isinstance(attempts, dict) and isinstance(attempts.get("attempts"), list):
        verdicts = [a.get("correct") for a in attempts["attempts"] if isinstance(a, dict)]
    ok = bool(verdicts) and all(v is True for v in verdicts)
    return {"exercise_id": ex.get("id"), "type": ex.get("type"),
            "status": "ok" if ok else "wrong",
            "correct_flags": verdicts,
            "detail": "" if ok else "trial-check did not grade the key as correct"}


# ---------------------------------------------------------------- lesson verify
def verify_lesson(api, lesson_id, lesson_title="", course_id=None, deep=False,
                  state_paths=(), min_note_coverage=None, grading=False,
                  strict=False, pace=0.5):
    """Verify ONE lesson via bulk reads. Returns a JSON-able result dict."""
    last = [0.0]

    def paced(fn, *args):
        if pace:
            dt = pace - (time.time() - last[0])
            if dt > 0:
                time.sleep(dt)
        out = fn(*args)
        last[0] = time.time()
        return out

    secs = paced(api.unit_sections, lesson_id) or []
    secs = secs if isinstance(secs, list) else secs.get("items", [])

    result = {
        "course_id": course_id, "lesson_id": lesson_id,
        "lesson_title": (lesson_title or "").strip(),
        "sections": [], "empty_content": [], "answer_flags": [], "lint_hits": [],
        "captions": [], "duplicates": {"items": [], "sections": []},
        "grading": [], "problems": [], "warnings": [],
        "state_diff": [],
        "totals": {"sections": 0, "exercises": 0, "types": {}, "with_teacher_notes": 0,
                   "empty_content": 0, "answer_flags": 0, "lint_hits": 0,
                   "images": 0, "images_without_caption": 0},
    }
    coverage = {"essay": [], "voice_recording": [], "free_gap": []}
    all_ex_for_dupes = []
    live_ids_by_lesson = {}

    for s in sorted(secs, key=lambda x: x.get("position") or 0):
        sid = s["id"]
        exs = paced(api.section_exercises, sid) or []
        exs = exs if isinstance(exs, list) else exs.get("items", [])
        srec = {"section_id": sid, "title": s.get("title") or "(untitled)",
                "exercises": len(exs), "types": {}, "empty_content": [],
                "notes": 0, "answer_flags": [], "lint_hits": [], "captions": []}
        for e in exs:
            ex = paced(api.exercise_get, e["id"]) if deep else e
            t = ex.get("type") or "?"
            srec["types"][t] = srec["types"].get(t, 0) + 1
            result["totals"]["types"][t] = result["totals"]["types"].get(t, 0) + 1
            content, aflags, notes = content_probe(ex)
            if content is None:
                srec["empty_content"].append({"exercise_id": e["id"], "type": t})
                result["empty_content"].append(
                    {"section": srec["title"], "exercise_id": e["id"], "type": t})
            if notes:
                srec["notes"] += 1
                result["totals"]["with_teacher_notes"] += 1
            for f in aflags:
                a = {"exercise_id": e["id"], "type": t, "flag": f}
                srec["answer_flags"].append(a)
                result["answer_flags"].append(dict(a, section=srec["title"]))
            for h in lint_exercise(ex):
                h["section"] = srec["title"]
                srec["lint_hits"].append(h)
                result["lint_hits"].append(h)
            for c in caption_flags(ex):
                c = {"exercise_id": e["id"], "section": srec["title"], "detail": c}
                srec["captions"].append(c)
                result["captions"].append(c)
            if t == "image_stack":
                result["totals"]["images"] += 1
                if caption_flags(ex):
                    result["totals"]["images_without_caption"] += 1
            # coverage: essay / voice / free-gap exercises
            cat = None
            if t == "essay":
                cat = "essay"
            elif t == "voice_recording":
                cat = "voice_recording"
            elif has_free_gap(ex):
                cat = "free_gap"
            if cat:
                coverage[cat].append(bool(notes))
            all_ex_for_dupes.append({"id": e["id"], "type": t,
                                     "section": srec["title"], "section_id": sid,
                                     "blob": blob_of(ex)})
            if grading and (_trial_answer(ex) is not None):
                g = grading_spot_check(api, ex, pace=pace)
                result["grading"].append(g)
                if g["status"] == "wrong":
                    result["problems"].append(
                        {"kind": "grading", "exercise_id": e["id"], "type": t,
                         "detail": g["detail"]})
        result["sections"].append(srec)
        result["totals"]["sections"] += 1
        result["totals"]["exercises"] += len(exs)
        live_ids_by_lesson[lesson_id] = live_ids_by_lesson.get(lesson_id, []) + [e["id"] for e in exs]

    result["totals"]["empty_content"] = len(result["empty_content"])
    result["totals"]["answer_flags"] = len(result["answer_flags"])
    result["totals"]["lint_hits"] = len(result["lint_hits"])
    result["duplicates"] = duplicate_scan(all_ex_for_dupes)
    result["notes_coverage"] = note_coverage(coverage)

    # problems (fail the gate) — defects that must block publication
    for e in result["empty_content"]:
        result["problems"].append({"kind": "empty_content", **e})
    for a in result["answer_flags"]:
        result["problems"].append({"kind": "answer_flag", **a})
    for h in result["lint_hits"]:
        result["problems"].append({"kind": "render_lint", **h})
    for d in result["duplicates"]["items"]:
        result["problems"].append({"kind": "duplicate_items", **d})
    for d in result["duplicates"]["sections"]:
        result["problems"].append({"kind": "duplicate_section", **d})

    # warnings — quality signals surfaced but not gating by default
    for c in result["captions"]:
        result["warnings"].append({"kind": "image_without_caption", **c})
    for g in result["grading"]:
        if g["status"] in ("unsupported",):
            result["warnings"].append({"kind": "grading_unsupported", **g})

    # notes-coverage gate
    gate = None
    if min_note_coverage is not None:
        pct = result["notes_coverage"]["overall"]["pct"]
        gate = {"min": min_note_coverage, "value": pct,
                "passed": (pct is not None and pct >= min_note_coverage * 100.0)}
        if not gate["passed"]:
            result["problems"].append(
                {"kind": "note_coverage_gate",
                 "detail": f"notes coverage {pct}% < required {min_note_coverage * 100:.0f}%"})

    # state↔live diff
    for sp in state_paths:
        diff = state_diff(sp, live_ids_by_lesson)
        result["state_diff"].append(diff)
        if diff.get("error"):
            result["warnings"].append({"kind": "state_unreadable", **diff})
        elif diff.get("missing_from_live") or diff.get("untracked_live"):
            result["warnings"].append({"kind": "state_drift", **{
                k: diff[k] for k in ("path", "state_exercises", "live_exercises",
                                     "missing_from_live", "untracked_live")}})

    if strict:
        result["problems"].extend(result["warnings"])
        result["warnings"] = []

    result["gate"] = gate
    result["ok"] = not result["problems"]
    return result


def verify_course(api, course_ref, lesson_ref=None, **kw):
    """Verify one course (all lessons, or one). Aggregates lesson results."""
    courses = api.courses_all()
    course = None
    for c in courses:
        if str(c.get("id")) == str(course_ref):
            course = c
            break
    if course is None:
        n = str(course_ref).strip().lower()
        pool = [c for c in courses if n in (c.get("title") or "").lower()]
        if len(pool) == 1:
            course = pool[0]
        elif not pool:
            raise VerifyError(f"course not found: {course_ref!r}. Known: "
                              + ", ".join(repr(c.get("title")) for c in courses[:20]))
        else:
            raise VerifyError(f"ambiguous course {course_ref!r}: "
                              + ", ".join(repr(c.get("title")) for c in pool))
    lessons = api.course_lessons(course["id"]) or []
    lessons = lessons if isinstance(lessons, list) else lessons.get("items", [])
    if lesson_ref:
        lid = str(lesson_ref).strip().lower()
        lessons = [l for l in lessons
                   if str(l.get("id")) == str(lesson_ref)
                   or lid in (l.get("title") or "").lower()]
        if not lessons:
            raise VerifyError(f"lesson not found in {course.get('title')!r}: {lesson_ref!r}")
    results = []
    for l in sorted(lessons, key=lambda x: x.get("position") or 0):
        results.append(verify_lesson(api, l["id"], lesson_title=l.get("title") or "",
                                     course_id=course["id"], **kw))
    agg = {"course_id": course["id"], "course_title": course.get("title"),
           "lessons": results,
           "problems": [dict(p, lesson=r["lesson_title"]) for r in results for p in r["problems"]],
           "warnings": [dict(w, lesson=r["lesson_title"]) for r in results for w in r["warnings"]]}
    agg["ok"] = not agg["problems"]
    return agg


# ---------------------------------------------------------------- human output
def exercise_brief(ex):
    """One-line content summary of an exercise payload (for tree/verify)."""
    t = ex.get("type") or "?"
    if t in TEST_TYPES:
        return f"{len(ex.get('questions') or [])} questions"
    if t in GAP_TYPES:
        return f"{len(ex.get('gaps') or [])} gaps"
    if t == "image_stack":
        return f"{len(ex.get('items') or [])} images"
    if t == "match_pairs":
        return f"{len(ex.get('pairs') or [])} pairs"
    if t == "true_false":
        return f"{len(ex.get('statements') or [])} statements"
    if t == "sort_columns":
        return f"{len(ex.get('columns') or [])} cols / {len(ex.get('words') or [])} words"
    for f in ("heading", "text", "body", "description"):
        v = norm_text(ex.get(f) or "")
        if v:
            return v[:60]
    return ""


def fmt_verify_lesson(res):
    L = []
    t = res["totals"]
    L.append(f"Lesson: {res['lesson_title'] or '(untitled)'} ({res['lesson_id']})")
    cov = res.get("notes_coverage", {}).get("overall", {})
    cov_s = f"{cov.get('pct')}% ({cov.get('covered')}/{cov.get('total')})" if cov.get("total") else "n/a"
    L.append(f"Sections: {t['sections']} | Exercises: {t['exercises']} | "
             f"teacher-notes: {t['with_teacher_notes']} | notes-coverage: {cov_s}")
    L.append("Types: " + ", ".join(f"{k}:{v}" for k, v in sorted(t["types"].items())))
    for s in res["sections"]:
        types = ", ".join(f"{k}:{v}" for k, v in sorted(s["types"].items()))
        L.append(f"  - {s['title']}: {s['exercises']} ex [{types}]"
                 + (f" | notes {s['notes']}" if s["notes"] else ""))
    if res["problems"]:
        L.append(f"PROBLEMS: {len(res['problems'])}")
        for p in res["problems"][:25]:
            L.append(f"  ! [{p['kind']}] " + json.dumps(
                {k: v for k, v in p.items() if k != "kind"}, ensure_ascii=False)[:160])
    else:
        L.append("PROBLEMS: 0")
    if res["warnings"]:
        L.append(f"WARNINGS: {len(res['warnings'])}")
        for w in res["warnings"][:25]:
            L.append(f"  * [{w['kind']}] " + json.dumps(
                {k: v for k, v in w.items() if k != "kind"}, ensure_ascii=False)[:160])
    nc = res.get("notes_coverage") or {}
    if nc and any(nc.get(k, {}).get("total") for k in ("essay", "voice_recording", "free_gap")):
        parts = [f"{k} {v['covered']}/{v['total']}"
                 for k, v in nc.items() if k != "overall" and v.get("total")]
        L.append("Notes coverage by type: " + ", ".join(parts))
    if res.get("state_diff"):
        for d in res["state_diff"]:
            L.append(f"State↔live: {d.get('path')} — state {d.get('state_exercises')}, "
                     f"live {d.get('live_exercises')}, missing_from_live {len(d.get('missing_from_live') or [])}, "
                     f"untracked_live {len(d.get('untracked_live') or [])}")
    if res.get("grading"):
        ok = sum(1 for g in res["grading"] if g["status"] == "ok")
        un = sum(1 for g in res["grading"] if g["status"] == "unsupported")
        bad = sum(1 for g in res["grading"] if g["status"] == "wrong")
        L.append(f"Grading spot-check: ok {ok}, wrong {bad}, unsupported {un} "
                 f"(of {len(res['grading'])})")
    L.append(f"STATUS: {'OK' if res['ok'] else 'FAILED'}")
    return "\n".join(L)


def fmt_verify(res):
    """Human formatter for verify_course results."""
    L = [f"Course: {res['course_title']} ({res['course_id']})",
         f"Lessons verified: {len(res['lessons'])}", ""]
    for r in res["lessons"]:
        L.append(fmt_verify_lesson(r))
        L.append("")
    if res["problems"]:
        L.append(f"TOTAL PROBLEMS: {len(res['problems'])}")
        for p in res["problems"][:40]:
            L.append(f"  ! [{p['kind']}] lesson={p.get('lesson', '')}: "
                     + json.dumps({k: v for k, v in p.items()
                                   if k not in ("kind", "lesson")}, ensure_ascii=False)[:140])
    else:
        L.append("TOTAL PROBLEMS: 0")
    L.append(f"STATUS: {'OK' if res['ok'] else 'FAILED'}")
    return "\n".join(L).rstrip()


# ---------------------------------------------------------------- materials status
def _status_records(data):
    """Find per-item build records in a run-state JSON of any of our shapes."""
    recs = []  # (group, key, record)
    if not isinstance(data, dict):
        return recs
    for k, v in data.items():
        if isinstance(v, dict) and "status" in v:
            recs.append((None, k, v))
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, dict) and "status" in v2:
                    recs.append((k, k2, v2))
    return recs


def summarize_state_files(paths):
    """Summarize run-state / builder-state files → JSON-able report."""
    files, totals = [], {"done": 0, "done_with_warnings": 0, "failed": 0, "other": 0,
                         "seconds": 0.0, "warnings": 0, "files": 0, "records": 0}
    for p in paths:
        f = {"path": p, "kind": None, "records": 0,
             "by_status": {}, "failed": [], "seconds": 0.0, "warnings": 0,
             "groups": {}, "error": None}
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            f["error"] = str(e)
            files.append(f)
            continue
        if isinstance(data, dict) and ("sections" in data or "exercises" in data):
            f["kind"] = "build-state"
            f["records"] = 1
            f["lesson_id"] = data.get("lesson_id")
            f["course_id"] = data.get("course_id")
            f["sections"] = len(data.get("sections") or {})
            f["exercises"] = len(data.get("exercises") or {})
            f["warnings"] = len(data.get("warnings") or [])
        else:
            f["kind"] = "run-state"
            recs = _status_records(data)
            f["records"] = len(recs)
            for group, key, rec in recs:
                st = str(rec.get("status") or "?")
                bucket = st if st in ("done", "done_with_warnings", "failed") else "other"
                f["by_status"][st] = f["by_status"].get(st, 0) + 1
                g = f["groups"].setdefault(group or "-", {"done": 0, "failed": 0,
                                                          "done_with_warnings": 0, "other": 0})
                g[bucket] = g.get(bucket, 0) + 1
                f["seconds"] += float(rec.get("seconds") or 0)
                f["warnings"] += len(rec.get("warnings") or [])
                if bucket == "failed":
                    f["failed"].append({"key": key, "group": group,
                                        "error": str(rec.get("error") or "")[:200]})
        totals["files"] += 1
        totals["records"] += f["records"]
        totals["seconds"] += f["seconds"]
        totals["warnings"] += f["warnings"]
        for st, n in f["by_status"].items():
            totals[st if st in totals else "other"] = totals.get(
                st if st in totals else "other", 0) + n
        files.append(f)
    return {"files": files, "totals": totals}


def fmt_status(res):
    L = []
    for f in res["files"]:
        if f["error"]:
            L.append(f"✗ {f['path']}: {f['error']}")
            continue
        if f["kind"] == "build-state":
            L.append(f"· {f['path']} [build-state] lesson={f.get('lesson_id') or '—'} "
                     f"sections={f['sections']} exercises={f['exercises']} warnings={f['warnings']}")
            continue
        by = ", ".join(f"{k} {v}" for k, v in sorted(f["by_status"].items()))
        mins = f["seconds"] / 60.0
        L.append(f"· {f['path']} [run-state] records={f['records']}: {by or '—'}"
                 + (f" | build time {mins:.1f} min" if f["seconds"] else "")
                 + (f" | warnings {f['warnings']}" if f["warnings"] else ""))
        for g, gs in sorted(f["groups"].items()):
            L.append(f"    {g}: " + ", ".join(f"{k} {v}" for k, v in sorted(gs.items())))
        for x in f["failed"][:10]:
            L.append(f"    ✗ {x['group'] or '-'}/{x['key']}: {x['error']}")
    t = res["totals"]
    L.append(f"TOTAL: files {t['files']}, records {t['records']} — done {t['done']}, "
             f"done_with_warnings {t['done_with_warnings']}, failed {t['failed']}"
             + (f", other {t['other']}" if t["other"] else "")
             + (f" | build time {t['seconds'] / 3600.0:.2f} h" if t["seconds"] else ""))
    return "\n".join(L)
