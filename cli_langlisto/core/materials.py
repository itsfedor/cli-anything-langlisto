"""Material builder — turn a lesson JSON spec into a LangListo material lesson.

A "material" on LangListo is a course (a book on the teacher's shelf) that
contains lesson units; every unit has sections, and every section has answer-
bearing exercises. This module maps a platform-neutral lesson spec (see
references/lesson-spec.md) onto that structure with one command, resumably.

Design rules (learned in production):
* additive only — the builder creates, never deletes;
* resumable — every created id is written to the state file, so a re-run
  continues where it stopped instead of duplicating;
* dry-run first — ``dry_run=True`` renders the full plan without touching the
  API, so the teacher can sanity-check before anything is created;
* paced — a small pause between API calls (be a well-behaved client), with
  backoff retries on 429/5xx;
* faithful — answers, gaps, model answers and media move across 1:1.

V2 additions
------------
* the full live-verified exercise vocabulary (see ``spec_lint.V2_TYPES``):
  ``sort_columns``, ``sentence_order`` / ``text_order``, ``unscramble``,
  ``select_word_gap``, the word-image trio, ``image_carousel`` /
  ``gif_animation``, ``diagram``, ``interactive_video``, ``audio_match_pairs``,
  ``wordlist``, ``link``, ``photo_answer``, ``divider``, ``timed_test``;
* quality features — per-gap hints, auto-expanded answer variants
  (``core.variants``), free gaps with teacher-note samples, teacher notes on
  every type, image captions, a build lockfile + duplicate detector
  (``core.dedup``) and a render-safe pre-build lint gate (``core.spec_lint``).
"""

import json
import os
import time

from .client import LanglistoError
from .dedup import (BuildLock, BuildLockedError, extract_text, find_duplicates,
                    entries_from_spec, is_meaningful, text_hash)
from .spec_lint import lint_spec, has_failing, SEVERITIES
from .variants import expand_answers

GAP_MARK = "\uFFFC"  # the placeholder character LangListo uses inside gap texts

RETRY_STATUSES = (429, 500, 502, 503, 504, 520)

# dry-run plan labels (CLI `materials build --dry-run` readability)
DRY_LABELS = {
    "open": "essay (+model answer if any)",
    "match": "match pairs",
    "gap_text": "gaps (typed)",
    "select_word_gap": "gaps (select)",
    "short_answer": "short answer",
    "choice": "quiz (grouped)",
    "multi_choice": "quiz (grouped)",
    "voice": "voice task",
    "true_false": "true/false",
    "test": "quiz",
    "timed_test": "timed quiz",
    "sort_columns": "sort columns",
    "sentence_order": "sentence order",
    "text_order": "text order",
    "unscramble": "unscramble",
    "enter_word_image": "word-image (enter)",
    "drag_word_image": "word-image (drag)",
    "select_word_image": "word-image (select)",
    "image_stack": "image stack",
    "image_carousel": "image carousel",
    "gif_animation": "gif animation",
    "diagram": "diagram",
    "interactive_video": "interactive video",
    "audio_match_pairs": "audio match pairs",
    "wordlist": "wordlist",
    "link": "link",
    "photo_answer": "photo answer",
    "divider": "divider",
}


class BuildPlan:
    def __init__(self):
        self.lines = []
        self.warnings = []

    def add(self, line):
        self.lines.append(line)


def _short(text, n=70):
    text = " ".join(str(text or "").split())
    return text[: n - 1] + "…" if len(text) > n else text


class MaterialBuilder:
    def __init__(self, api, spec, course=None, state_path=None, dry_run=False,
                 pace=0.35, media_dir=None, spec_dir=None, log=print,
                 lint_fail_on=None, lock=None):
        self.api = api
        self.spec = spec
        self.course_ref = course or spec.get("course")
        self.lesson_title = spec.get("lesson_title") or "Lesson"
        self.dry_run = dry_run
        self.pace = pace
        self.media_dir = media_dir
        self.spec_dir = spec_dir or "."
        self.log = log
        self.lint_fail_on = list(lint_fail_on) if lint_fail_on is not None else None
        self.state_path = state_path
        if state_path and os.path.exists(state_path):
            self.state = json.load(open(state_path, encoding="utf-8"))
        else:
            self.state = {}
        self.state.setdefault("sections", {})
        self.state.setdefault("exercises", {})
        self.state.setdefault("warnings", [])
        self.state.setdefault("media_urls", {})
        self.state.setdefault("item_hashes", {})
        self.state.setdefault("lint", [])
        self.lock_path = self._resolve_lock_path(lock)
        self.locked = False  # set when a live lock refused this run (no writes)
        self.plan = BuildPlan()
        self._sleep = (lambda: None) if dry_run else (lambda: time.sleep(self.pace))

    # ------------------------------------------------ helpers
    def _resolve_lock_path(self, lock):
        """None → auto (state_path given, wet run); False → disabled; str → path."""
        if lock is False or self.dry_run:
            return None
        if self.spec.get("build_lock") is False:
            return None
        if isinstance(lock, str):
            return lock
        if not self.state_path:
            return None
        return self.state_path + ".lock"

    def _plan(self, line):
        self.plan.add(line)
        self.log(line)

    def _save(self):
        if self.dry_run or not self.state_path:
            return
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)

    def _warn(self, msg):
        self.state["warnings"].append(msg)
        self.plan.warnings.append(msg)
        self.log(f"  ! {msg}")

    def _api_retry(self, fn, *args, **kwargs):
        """Call an API function; retry 429/5xx with 2-6 s backoff (≤3 retries)."""
        delay = 2.0
        for attempt in range(4):
            try:
                return fn(*args, **kwargs)
            except LanglistoError as e:
                if e.status in RETRY_STATUSES and attempt < 3 and not self.dry_run:
                    self.log(f"  ~ HTTP {e.status} — retry in {delay:.0f}s")
                    time.sleep(delay)
                    delay = min(delay * 2, 6.0)
                    continue
                raise

    def note_text(self, item, extra=None):
        """Merged teacher note: model answer (open/voice) + teacher_note + extras."""
        parts = []
        if item.get("type") in ("open", "voice") and item.get("model_answer"):
            parts.append(str(item["model_answer"]))
        if item.get("teacher_note"):
            parts.append(str(item["teacher_note"]))
        for x in extra or []:
            if x:
                parts.append(str(x))
        return "<br>".join(parts)

    def _put_note(self, exid, text, key):
        if self.dry_run or self.api is None or not text or not exid:
            return  # dry runs never touch the API (the CLI passes api=None)
        try:
            self._api_retry(self.api.exercise_teacher_notes, exid, text)
        except LanglistoError as e:
            self._warn(f"{key}: teacher note failed: {e.message}")
        self._sleep()

    def attach_note(self, exid, item, key):
        """Teacher note (student-invisible) for ANY exercise type (back-compat)."""
        self._put_note(exid, self.note_text(item), key)

    def attach_item_notes(self, item, extra=None):
        """Attach the merged note for a built item (no-op when note is empty)."""
        self._put_note(self.state["exercises"].get(item.get("id")),
                       self.note_text(item, extra), item.get("id"))

    def _resolve_media(self, value):
        """Local path (relative to spec/media dir) or full URL → public URL."""
        if not value:
            return None
        if str(value).startswith(("http://", "https://")):
            return value
        if self.dry_run:
            return f"«{value}»"  # plan placeholder — nothing is uploaded on dry runs
        if value in self.state["media_urls"]:
            return self.state["media_urls"][value]
        candidates = [value]
        if self.media_dir:
            candidates.append(os.path.join(self.media_dir, value))
        candidates.append(os.path.join(self.spec_dir, value))
        candidates.append(os.path.join(self.spec_dir, os.path.basename(value)))
        for cand in candidates:
            if os.path.exists(cand):
                url = self._api_retry(self.api.upload, cand)
                self.state["media_urls"][value] = url
                self._save()
                self.log(f"  ^ uploaded {os.path.basename(cand)} → {url[:60]}…")
                return url
        self._warn(f"media not found locally: {value}")
        return None

    # ------------------------------------------------ duplicate guard
    def _record_item_hash(self, item_or_group, key):
        if self.dry_run:
            return
        text = extract_text(item_or_group)
        if not is_meaningful(text):
            return
        h = text_hash(text)
        keys = self.state["item_hashes"].setdefault(h, [])
        if key in keys:  # already recorded (resume) — stay quiet
            return
        keys.append(key)
        if len(keys) > 1:
            self._warn(f"{key}: duplicate content of {keys[0]} "
                       f"(normalized hash {h}) — possible double build")

    # ------------------------------------------------ structure
    def ensure_course_lesson(self):
        if self.dry_run:
            self._plan(f"course: {self.course_ref!r} → lesson {self.lesson_title!r}")
            return None, None
        if "course_id" not in self.state:
            if not self.course_ref:
                raise LanglistoError(0, "no_course", "spec has no course and none was passed")
            hit = None
            for c in self.api.courses_all():
                if str(c.get("id")) == str(self.course_ref) or c.get("title") == self.course_ref:
                    hit = c
                    break
            if hit:
                self.state["course_id"] = hit["id"]
                self.log(f"= course exists: {hit.get('title')} ({hit['id'][:8]})")
            else:
                c = self._api_retry(self.api.course_create, self.course_ref)
                self.state["course_id"] = c["id"]
                self.log(f"+ course created: {self.course_ref} ({c['id'][:8]})")
            self._save()
        if "lesson_id" not in self.state:
            cid = self.state["course_id"]
            hit = None
            for l in (self.api.course_lessons(cid) or []):
                if (l.get("title") or "") == self.lesson_title:
                    hit = l
                    break
            if hit:
                self.state["lesson_id"] = hit["id"]
                self.log(f"= lesson exists: {self.lesson_title} ({hit['id'][:8]})")
            else:
                l = self._api_retry(self.api.course_add_lesson, cid)
                self._sleep()
                self._api_retry(self.api.unit_rename, l["id"], self.lesson_title)
                self.state["lesson_id"] = l["id"]
                self.log(f"+ lesson created: {self.lesson_title} ({l['id'][:8]})")
            self._save()
        # lesson description (plain text; the platform renders it pre-line)
        desc = self.spec.get("lesson_description")
        if desc and not self.state.get("lesson_desc_set"):
            try:
                self._api_retry(self.api.unit_patch, self.state["lesson_id"], description=desc)
                self.state["lesson_desc_set"] = True
                self._save()
            except LanglistoError as e:
                self._warn(f"lesson description: {e.message}")
        return self.state["course_id"], self.state["lesson_id"]

    def ensure_section(self, section):
        key = section["id"]
        if self.dry_run:
            self._plan(f"  section {section.get('title', '')!r} ({len(section['items'])} items)")
            return None
        if key in self.state["sections"]:
            return self.state["sections"][key]
        sec = self._api_retry(self.api.section_create, self.state["lesson_id"])
        self._sleep()
        if section.get("title"):
            self._api_retry(self.api.section_patch, sec["id"], title=section["title"])
            self._sleep()
        self.state["sections"][key] = sec["id"]
        self._save()
        self.log(f"+ section: {section.get('title', '(untitled)')}")
        return sec["id"]

    # ------------------------------------------------ exercises
    def create_exercise(self, sec_id, key, ctypes, suffix, body):
        """Create + configure one exercise; ``suffix=None`` skips the PATCH
        (contentless types like ``divider``)."""
        if self.dry_run or key in self.state["exercises"]:
            return None
        ex = None
        for ct in ctypes:
            try:
                ex = self._api_retry(self.api.exercise_create, sec_id, ct)
                break
            except LanglistoError as e:
                self._warn(f"{key}: create {ct} failed: {e.message}")
        if not ex:
            return None
        self._sleep()
        if suffix:
            try:
                self._api_retry(self.api.exercise_patch, ex["id"], suffix, body)
            except LanglistoError as e:
                detail = json.dumps(e.details or "", ensure_ascii=False)[:200]
                self._warn(f"{key}: patch {suffix} failed: {e.message} {detail}")
            self._sleep()
        self.state["exercises"][key] = ex["id"]
        self._save()
        self.log(f"  + {key}")
        return ex["id"]

    # ------------------------------------------------ gap bodies (v2 quality)
    def gap_body(self, item, note_lines=None):
        """Gap exercise body: markers, hints, expanded variants, free gaps,
        derived ``Box:`` line (word-bank is classroom-only — H2 verdict)."""
        text = item["prompt_html"]
        gaps = []
        box = []
        gsrc = item.get("gaps") or {}
        if isinstance(gsrc, dict):
            keys = sorted(int(k) for k in gsrc)
            get = lambda n: gsrc[str(n)]  # noqa: E731
        else:
            keys = list(range(1, len(gsrc) + 1))
            get = lambda n: gsrc[n - 1]  # noqa: E731
        item_expand = item.get("expand_variants", True) is not False
        for n in keys:
            spec = get(n) or {}
            text = text.replace(f"[[{n}]]", GAP_MARK)
            for o in spec.get("options") or []:
                if o not in box:
                    box.append(o)
            free = bool(spec.get("no_correct"))
            ans = spec.get("answer")
            if ans is None and spec.get("correct") is not None:
                ans = spec["correct"]
            if isinstance(ans, str):
                ans = [ans]
            ans = [str(a) for a in (ans or []) if str(a).strip()]
            if free:
                # the schema requires ≥1 answers item per gap even when free
                answers = ["—"]
            else:
                enabled = item_expand and spec.get("variants", True) is not False
                answers = expand_answers(ans, enabled=enabled) if ans else []
            gaps.append({
                "idx": n - 1,
                "answers": answers,
                "no_correct": free,
                "hint": str(spec.get("hint") or ""),
                "correct_indices": [] if free else ([0] if answers else []),
            })
            if free and spec.get("sample_answer") and note_lines is not None:
                note_lines.append(f"Sample answer (gap {n}): {spec['sample_answer']}")
        if box:
            text += "<p><b>Box:</b> " + " · ".join(box) + "</p>"
        return {"heading": item.get("number_label") or "", "text": text, "gaps": gaps}

    def test_body(self, item, default_limit=None):
        """Multi-question test body (``test`` / ``timed_test`` share it)."""
        qs = []
        for q in item.get("questions") or []:
            opts = q.get("options") or []
            correct = q.get("correct")
            if isinstance(correct, bool):
                correct = [0] if correct else []
            if isinstance(correct, int):
                correct = [correct]
            if not isinstance(correct, list):
                correct = []
            qs.append({"prompt": q.get("prompt", ""),
                       "options": [{"text": o, "is_correct": (k in correct)}
                                   for k, o in enumerate(opts)]})
        limit = item.get("time_limit_seconds")
        if limit is None:
            limit = default_limit
        return {"heading": item.get("heading", ""),
                "time_limit_seconds": limit, "questions": qs}

    # ------------------------------------------------ item dispatch
    def build_item(self, sec_id, item):
        """Build one spec item.

        Returns ``"choice"`` for grouped quiz runs, a list of extra teacher-note
        lines otherwise (possibly empty), or ``None`` when nothing was built.
        """
        t = item["type"]
        key = item["id"]
        ph = item.get("prompt_html") or ""
        media = item.get("media") or {}
        note_lines = []
        if t == "info":
            if media.get("audio"):
                url = self._resolve_media(media["audio"])
                self.create_exercise(sec_id, key, ["audio", "media_clip", "video"], "/media-clip",
                                     {"heading": item.get("heading", ""), "description": ph,
                                      "media_url": url or "",
                                      "clips": [{"media_url": url or "", "title": "",
                                                 "script": "", "keep_source_player": True}]})
            elif media.get("image"):
                url = self._resolve_media(media["image"])
                self.create_exercise(sec_id, key, ["image_stack"], "/image-block",
                                     {"heading": ph, "document_url": "", "document_name": "",
                                      "items": [{"image_url": url or "",
                                                 "description": media.get("caption", "")}]})
            else:
                if len(ph) < 140:
                    self.create_exercise(sec_id, key, ["note"], "/note",
                                         {"heading": item.get("heading", ""), "text": ph,
                                          "color": item.get("color", "blue")})
                else:
                    self.create_exercise(sec_id, key, ["article", "text"], "/text-block",
                                         {"heading": item.get("heading", ""), "subheading": "",
                                          "image_url": "", "body": ph})
        elif t == "open":
            self.create_exercise(sec_id, key, ["essay"], "/text-block",
                                 {"heading": item.get("heading", ""), "subheading": "",
                                  "image_url": "", "body": ph})
        elif t == "voice":
            self.create_exercise(sec_id, key, ["voice_recording"], "/voice-recording",
                                 {"heading": item.get("heading", ""), "description": ph,
                                  "time_limit_seconds": item.get("time_limit_seconds") or 180})
        elif t == "true_false":
            sts = [{"text": s.get("text", ""),
                    "answer": "true" if (s.get("answer") is True or str(s.get("answer")).lower() == "true") else "false"}
                   for s in item.get("statements") or []]
            self.create_exercise(sec_id, key, ["true_false"], "/true-false",
                                 {"heading": item.get("heading", ""),
                                  "not_stated_enabled": bool(item.get("not_stated_enabled", False)),
                                  "statements": sts})
        elif t == "test":
            self.create_exercise(sec_id, key, ["untimed_test", "timed_test"], "/test",
                                 self.test_body(item))
        elif t == "timed_test":
            self.create_exercise(sec_id, key, ["timed_test", "untimed_test"], "/test",
                                 self.test_body(item, default_limit=60))
        elif t == "match":
            pairs = [{"left": l.get("html", ""), "right": item["correct"][l["id"]],
                      "audio_url": ""}
                     for l in item.get("left") or []]
            self.create_exercise(sec_id, key, ["match_pairs"], "/match-pairs",
                                 {"heading": item.get("heading", "") or (item.get("number_label") or "")
                                  or ph, "pairs": pairs})
        elif t == "audio_match_pairs":
            pairs = [{"left": p.get("left", ""), "right": p.get("right", ""),
                      "audio_url": self._resolve_media(p.get("audio")) or ""}
                     for p in item.get("pairs") or []]
            self.create_exercise(sec_id, key, ["audio_match_pairs", "match_pairs"], "/match-pairs",
                                 {"heading": item.get("heading", ""), "pairs": pairs})
        elif t in ("gap_text", "select_word_gap"):
            chain = (["select_word_gap", "enter_word_gap", "drag_word_gap"] if t == "select_word_gap"
                     else ["enter_word_gap", "drag_word_gap"])
            self.create_exercise(sec_id, key, chain, "/drag-word-gap",
                                 self.gap_body(item, note_lines))
        elif t == "short_answer":
            correct = item.get("correct") or []
            if isinstance(correct, str):
                correct = [correct]
            correct = [str(c) for c in correct if str(c).strip()]
            enabled = item.get("expand_variants", True) is not False
            body = {"heading": f"Q{item['number']}" if item.get("number") else "",
                    "text": ph + " " + GAP_MARK,
                    "gaps": [{"idx": 0, "answers": expand_answers(correct, enabled=enabled),
                              "no_correct": False, "hint": str(item.get("hint") or ""),
                              "correct_indices": [0] if correct else []}]}
            self.create_exercise(sec_id, key, ["enter_word_gap", "drag_word_gap"],
                                 "/drag-word-gap", body)
        elif t == "sort_columns":
            columns = [{"title": str(c)} for c in item.get("columns") or []]
            words = []
            for w in item.get("words") or []:
                col = w.get("column", w.get("column_idx"))
                words.append({"text": w.get("text", ""),
                              "column_idx": int(col) if col is not None else 0})
            self.create_exercise(sec_id, key, ["sort_columns"], "/word-columns",
                                 {"heading": item.get("heading", ""),
                                  "no_correct": bool(item.get("no_correct", False)),
                                  "columns": columns, "words": words})
        elif t in ("sentence_order", "text_order"):
            items_ = [{"text": x if not isinstance(x, dict) else x.get("text", "")}
                      for x in item.get("items") or []]
            self.create_exercise(sec_id, key, [t], "/ordering",
                                 {"heading": item.get("heading", ""),
                                  "hint": item.get("hint", ""), "items": items_, "words": []})
        elif t == "unscramble":
            words = []
            for w in item.get("words") or []:
                tiles = w.get("tiles", w.get("items")) or []
                words.append({"hint": w.get("hint", ""),
                              "items": [{"text": x if not isinstance(x, dict) else x.get("text", "")}
                                        for x in tiles],
                              "answer": []})
            self.create_exercise(sec_id, key, ["unscramble"], "/ordering",
                                 {"heading": item.get("heading", ""),
                                  "hint": item.get("hint", ""), "items": [], "words": words})
        elif t in ("enter_word_image", "drag_word_image", "select_word_image"):
            entries = []
            for it in item.get("items") or []:
                url = self._resolve_media(it.get("image") or it.get("image_url"))
                word = it.get("word", "")
                alts = list(it.get("alternatives") or [])
                options = list(it.get("options") or [])
                if not options:
                    options = [word] + [a for a in alts if a != word]
                entries.append({"image_url": url or "", "word": word,
                                "alternatives": alts, "options": options})
            self.create_exercise(sec_id, key, [t], "/word-image",
                                 {"heading": item.get("heading", ""),
                                  "no_correct": bool(item.get("no_correct", False)),
                                  "items": entries})
        elif t in ("image_stack", "image_carousel", "gif_animation"):
            entries = []
            for it in item.get("items") or []:
                url = self._resolve_media(it.get("image") or it.get("image_url"))
                caption = it.get("caption", it.get("description", ""))
                entries.append({"image_url": url or "", "description": caption})
            self.create_exercise(sec_id, key, [t], "/image-block",
                                 {"document_url": "", "document_name": "",
                                  "heading": item.get("heading", ""), "items": entries})
        elif t == "diagram":
            url = self._resolve_media(item.get("image") or item.get("image_url"))
            points = [{"x": p.get("x"), "y": p.get("y")} for p in item.get("points") or []]
            cards = []
            for c in item.get("cards") or []:
                cards.append({"text": c.get("text", ""),
                              "image_url": self._resolve_media(c.get("image")) or "",
                              "point_idx": int(c.get("point_idx", 0))})
            self.create_exercise(sec_id, key, ["diagram"], "/diagram",
                                 {"heading": item.get("heading", ""),
                                  "description": item.get("description", ""),
                                  "image_url": url or "", "points": points, "cards": cards})
        elif t == "interactive_video":
            url = self._resolve_media(item.get("video_url") or item.get("media_url"))
            checkpoints = []
            for cp in item.get("checkpoints") or []:
                opts = [{"text": o.get("text", ""), "is_correct": bool(o.get("is_correct"))}
                        for o in cp.get("options") or []]
                checkpoints.append({"time_seconds": int(cp.get("time_seconds") or 0),
                                    "prompt": cp.get("prompt", ""), "options": opts})
            self.create_exercise(sec_id, key, ["interactive_video"], "/interactive-video",
                                 {"heading": item.get("heading", ""),
                                  "description": item.get("description", ""),
                                  "media_url": url or "", "checkpoints": checkpoints})
        elif t == "wordlist":
            words = [{"word": w.get("word", ""), "translation": w.get("translation", "")}
                     for w in item.get("words") or []]
            self.create_exercise(sec_id, key, ["wordlist"], "/wordlist",
                                 {"heading": item.get("heading", ""), "words": words})
        elif t == "link":
            self.create_exercise(sec_id, key, ["link"], "/link",
                                 {"heading": item.get("heading", ""),
                                  "description": item.get("description", ""),
                                  "url": item.get("url", "")})
        elif t == "photo_answer":
            self.create_exercise(sec_id, key, ["photo_answer"], "/photo-answer",
                                 {"heading": item.get("heading", ""),
                                  "description": item.get("description", ph)})
        elif t == "divider":
            self.create_exercise(sec_id, key, ["divider"], None, None)
        elif t in ("choice", "multi_choice"):
            return "choice"  # grouped by the caller
        else:
            self._warn(f"{key}: unsupported item type {t!r} — skipped")
        return note_lines

    def build_choice_run(self, sec_id, run):
        qs = []
        for c in run:
            correct = c.get("correct")
            opts = [{"text": o,
                     "is_correct": (k in correct) if isinstance(correct, list) else (k == correct)}
                    for k, o in enumerate(c.get("options") or [])]
            qs.append({"prompt": c.get("prompt_html") or "", "options": opts})
        key = f"test-{run[0]['id']}"
        self.create_exercise(sec_id, key, ["untimed_test", "timed_test"],
                             "/test", {"heading": "", "time_limit_seconds": None, "questions": qs})
        notes = [c.get("teacher_note") for c in run if c.get("teacher_note")]
        if notes:
            self.attach_note(self.state["exercises"].get(key), {"teacher_note": "<br>".join(notes)}, key)

    # ------------------------------------------------ lint gate
    def run_lint_gate(self):
        """Run the spec linter; abort pre-write when ``lint_fail_on`` matches."""
        findings = lint_spec(self.spec)
        self.state["lint"] = findings
        counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
        for f in findings:
            line = (f"  lint [{f['severity']}] {f['code']} @ {f['item']}"
                    + (f".{f['field']}" if f.get("field") else "") + f": {f['message']}")
            if f["severity"] == "error":
                self._warn(line.strip())
            elif f["severity"] == "warn":
                self._plan(line)
        if findings:
            self._plan(f"  lint summary: {counts['error']} error / {counts['warn']} warn "
                       f"/ {counts['info']} info")
        fail_on = self.lint_fail_on
        if fail_on is None:
            fail_on = (self.spec.get("lint") or {}).get("fail_on") or ()
        bad = has_failing(findings, fail_on)
        if bad:
            raise LanglistoError(
                0, "lint_failed",
                f"{len(bad)} lint finding(s) at severity {sorted(set(fail_on))} — "
                f"build aborted before any write")
        return findings

    # ------------------------------------------------ driver
    def run(self):
        lock = None
        try:
            if self.lock_path:
                lock = BuildLock(self.lock_path)
                try:
                    lock.acquire(owner=self.state_path or "")
                except BuildLockedError as e:
                    self.locked = True
                    self._warn(f"build lock: {e}")
                    self._save()
                    return self.summary()
            try:
                self.run_lint_gate()
            except LanglistoError:
                self._save()
                raise
            try:
                self.ensure_course_lesson()
            except LanglistoError as e:
                self._warn(f"course/lesson: {e.message}")
                self._save()
                return self.summary()
            for section in self.spec.get("sections") or []:
                sec_id = self.ensure_section(section)
                items = section.get("items") or []
                i = 0
                while i < len(items):
                    item = items[i]
                    if self.dry_run:
                        t = item["type"]
                        m = item.get("media") or {}
                        if t == "info":
                            label = "image" if m.get("image") else ("audio" if m.get("audio") else "note/article")
                        else:
                            label = DRY_LABELS.get(t, t)
                        extra = ""
                        if m.get("audio"):
                            extra = f" [audio {m['audio']}]"
                        elif m.get("image"):
                            extra = f" [img {m['image']}]"
                        elif item.get("teacher_note"):
                            extra = " [note]"
                        self._plan(f"    - {label:<22} {_short(item.get('prompt_html') or item.get('description'))}{extra}")
                    try:
                        res = self.build_item(sec_id, item)
                        if res == "choice":
                            run = []
                            j = i
                            while j < len(items) and items[j]["type"] in ("choice", "multi_choice"):
                                run.append(items[j])
                                j += 1
                            self.build_choice_run(sec_id, run)
                            self._record_item_hash(run, f"test-{run[0]['id']}")
                            if self.dry_run:
                                for c in run[1:]:
                                    self._plan(f"    - quiz (grouped)         {_short(c.get('prompt_html'))}")
                            i = j - 1
                        else:
                            self._record_item_hash(item, item.get("id"))
                            self.attach_item_notes(item, res or None)
                    except LanglistoError as e:
                        self._warn(f"{item.get('id')}: {e.message}")
                    i += 1
            self._save()
            return self.summary()
        finally:
            if lock:
                lock.release()

    def summary(self):
        hashes = self.state.get("item_hashes") or {}
        dups = [{"hash": h, "items": keys} for h, keys in hashes.items() if len(keys) > 1]
        counts = {s: sum(1 for f in (self.state.get("lint") or [])
                         if f["severity"] == s) for s in SEVERITIES}
        return {
            "lesson_title": self.lesson_title,
            "dry_run": self.dry_run,
            "locked": bool(getattr(self, "locked", False)),
            "sections": len(self.state["sections"]) if not self.dry_run else
            len(self.spec.get("sections") or []),
            "exercises": len(self.state["exercises"]),
            "warnings": self.state["warnings"],
            "lint": counts,
            "duplicates": dups,
            "plan": self.plan.lines,
        }


def build_material(api, spec, course=None, state_path=None, dry_run=False, pace=0.35,
                   media_dir=None, spec_dir=None, log=print, lint_fail_on=None, lock=None):
    builder = MaterialBuilder(api, spec, course=course, state_path=state_path,
                              dry_run=dry_run, pace=pace, media_dir=media_dir,
                              spec_dir=spec_dir, log=log,
                              lint_fail_on=lint_fail_on, lock=lock)
    return builder.run()
