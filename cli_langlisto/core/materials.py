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
* paced — a small pause between API calls (be a well-behaved client);
* faithful — answers, gaps, model answers and media move across 1:1.
"""

import json
import os
import time

from .client import LanglistoError

GAP_MARK = "\uFFFC"  # the placeholder character LangListo uses inside gap texts


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
                 pace=0.35, media_dir=None, spec_dir=None, log=print):
        self.api = api
        self.spec = spec
        self.course_ref = course or spec.get("course")
        self.lesson_title = spec.get("lesson_title") or "Lesson"
        self.dry_run = dry_run
        self.pace = pace
        self.media_dir = media_dir
        self.spec_dir = spec_dir or "."
        self.log = log
        self.state_path = state_path
        if state_path and os.path.exists(state_path):
            self.state = json.load(open(state_path, encoding="utf-8"))
        else:
            self.state = {}
        self.state.setdefault("sections", {})
        self.state.setdefault("exercises", {})
        self.state.setdefault("warnings", [])
        self.state.setdefault("media_urls", {})
        self.plan = BuildPlan()
        self._sleep = (lambda: None) if dry_run else (lambda: time.sleep(self.pace))

    # ------------------------------------------------ helpers
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
                url = self.api.upload(cand)
                self.state["media_urls"][value] = url
                self._save()
                self.log(f"  ^ uploaded {os.path.basename(cand)} → {url[:60]}…")
                return url
        self._warn(f"media not found locally: {value}")
        return None

    # ------------------------------------------------ structure
    def ensure_course_lesson(self):
        if self.dry_run:
            self._plan(f"course: {self.course_ref!r} → lesson {self.lesson_title!r}")
            return None, None
        if "course_id" not in self.state:
            if not self.course_ref:
                raise LanglistoError(0, "no_course", "spec has no course and none was passed")
            courses = self.api.courses_all()
            hit = None
            for c in courses:
                if str(c.get("id")) == str(self.course_ref) or c.get("title") == self.course_ref:
                    hit = c
                    break
            if hit:
                self.state["course_id"] = hit["id"]
                self.log(f"= course exists: {hit.get('title')} ({hit['id'][:8]})")
            else:
                c = self.api.course_create(self.course_ref)
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
                l = self.api.course_add_lesson(cid)
                self._sleep()
                self.api.unit_rename(l["id"], self.lesson_title)
                self.state["lesson_id"] = l["id"]
                self.log(f"+ lesson created: {self.lesson_title} ({l['id'][:8]})")
            self._save()
        # lesson description (plain text; the platform renders it pre-line)
        desc = self.spec.get("lesson_description")
        if desc and not self.state.get("lesson_desc_set"):
            try:
                self.api.unit_patch(self.state["lesson_id"], description=desc)
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
        sec = self.api.section_create(self.state["lesson_id"])
        self._sleep()
        if section.get("title"):
            self.api.section_patch(sec["id"], title=section["title"])
            self._sleep()
        self.state["sections"][key] = sec["id"]
        self._save()
        self.log(f"+ section: {section.get('title', '(untitled)')}")
        return sec["id"]

    # ------------------------------------------------ exercises
    def create_exercise(self, sec_id, key, ctypes, suffix, body):
        if self.dry_run or key in self.state["exercises"]:
            return None
        ex = None
        for ct in ctypes:
            try:
                ex = self.api.exercise_create(sec_id, ct)
                break
            except LanglistoError as e:
                self._warn(f"{key}: create {ct} failed: {e.message}")
        if not ex:
            return None
        self._sleep()
        try:
            self.api.exercise_patch(ex["id"], suffix, body)
        except LanglistoError as e:
            detail = json.dumps(e.details or "", ensure_ascii=False)[:200]
            self._warn(f"{key}: patch {suffix} failed: {e.message} {detail}")
        self._sleep()
        self.state["exercises"][key] = ex["id"]
        self._save()
        self.log(f"  + {key}")
        return ex["id"]

    def attach_note(self, exid, item, key):
        """Teacher note (student-invisible) for ANY exercise type."""
        note = item.get("teacher_note")
        if not note or not exid:
            return
        try:
            self.api.exercise_teacher_notes(exid, note)
        except LanglistoError as e:
            self._warn(f"{key}: teacher note failed: {e.message}")
        self._sleep()

    def gap_body(self, item):
        text = item["prompt_html"]
        gaps = []
        box = []
        gsrc = item.get("gaps") or {}
        keys = sorted(int(k) for k in gsrc) if isinstance(gsrc, dict) else list(range(1, len(gsrc) + 1))
        for n in keys:
            spec = gsrc[str(n)] if isinstance(gsrc, dict) else gsrc[n - 1]
            text = text.replace(f"[[{n}]]", GAP_MARK)
            for o in spec.get("options") or []:
                if o not in box:
                    box.append(o)
            ans = spec.get("answer") or ([spec["correct"]] if spec.get("correct") else [])
            if isinstance(ans, str):
                ans = [ans]
            gaps.append({"idx": n - 1, "answers": ans, "no_correct": False, "hint": "",
                         "correct_indices": [0]})
        if box:
            text += "<p><b>Box:</b> " + " · ".join(box) + "</p>"
        return {"heading": item.get("number_label") or "", "text": text, "gaps": gaps}

    def build_item(self, sec_id, item):
        t = item["type"]
        key = item["id"]
        ph = item.get("prompt_html") or ""
        media = item.get("media") or {}
        if t == "info":
            if media.get("audio"):
                url = self._resolve_media(media["audio"])
                self.create_exercise(sec_id, key, ["audio", "media_clip", "video"], "/media-clip",
                                     {"heading": "", "description": ph, "media_url": url or "",
                                      "clips": [{"media_url": url or "", "title": "", "script": "",
                                                 "keep_source_player": True}]})
            elif media.get("image"):
                url = self._resolve_media(media["image"])
                self.create_exercise(sec_id, key, ["image_stack"], "/image-block",
                                     {"heading": ph, "document_url": "", "document_name": "",
                                      "items": [{"image_url": url or "", "description": ""}]})
            else:
                if len(ph) < 140:
                    self.create_exercise(sec_id, key, ["note"], "/note",
                                         {"heading": "", "text": ph, "color": "blue"})
                else:
                    self.create_exercise(sec_id, key, ["article", "text"], "/text-block",
                                         {"heading": "", "subheading": "", "image_url": "", "body": ph})
        elif t == "open":
            exid = self.create_exercise(sec_id, key, ["essay"], "/text-block",
                                        {"heading": "", "subheading": "", "image_url": "", "body": ph})
            if item.get("model_answer") and exid:
                try:
                    self.api.exercise_teacher_notes(exid, item["model_answer"])
                except LanglistoError as e:
                    self._warn(f"{key}: teacher notes failed: {e.message}")
                self._sleep()
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
            # explicit multi-question test: {heading, questions:[{prompt, options:[str], correct:[idx]}]}
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
            self.create_exercise(sec_id, key, ["untimed_test", "timed_test"], "/test",
                                 {"heading": item.get("heading", ""),
                                  "time_limit_seconds": item.get("time_limit_seconds"),
                                  "questions": qs})
        elif t == "match":
            pairs = [{"left": l.get("html", ""), "right": item["correct"][l["id"]], "audio_url": ""}
                     for l in item.get("left") or []]
            self.create_exercise(sec_id, key, ["match_pairs"], "/match-pairs",
                                 {"heading": ph or (item.get("number_label") or ""), "pairs": pairs})
        elif t == "gap_text":
            self.create_exercise(sec_id, key, ["enter_word_gap", "drag_word_gap"],
                                 "/drag-word-gap", self.gap_body(item))
        elif t == "short_answer":
            correct = item.get("correct") or []
            if isinstance(correct, str):
                correct = [correct]
            body = {"heading": f"Q{item['number']}" if item.get("number") else "",
                    "text": ph + " " + GAP_MARK,
                    "gaps": [{"idx": 0, "answers": correct, "no_correct": False, "hint": "",
                              "correct_indices": [0]}]}
            self.create_exercise(sec_id, key, ["enter_word_gap", "drag_word_gap"], "/drag-word-gap", body)
        elif t in ("choice", "multi_choice"):
            return "choice"  # grouped by the caller
        else:
            self._warn(f"{key}: unsupported item type {t!r} — skipped")
        return None

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

    # ------------------------------------------------ driver
    def run(self):
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
                        label = {"open": "essay (+model answer if any)",
                                 "match": "match pairs", "gap_text": "gaps (typed)",
                                 "short_answer": "short answer", "choice": "quiz (grouped)",
                                 "multi_choice": "quiz (grouped)", "voice": "voice task",
                                 "true_false": "true/false", "test": "quiz"}.get(t, t)
                    extra = ""
                    if m.get("audio"):
                        extra = f" [audio {m['audio']}]"
                    elif m.get("image"):
                        extra = f" [img {m['image']}]"
                    self._plan(f"    - {label:<22} {_short(item.get('prompt_html'))}{extra}")
                try:
                    res = self.build_item(sec_id, item)
                    if res != "choice":
                        self.attach_note(self.state["exercises"].get(item.get("id")), item, item.get("id"))
                    if res == "choice":
                        run = []
                        j = i
                        while j < len(items) and items[j]["type"] in ("choice", "multi_choice"):
                            run.append(items[j])
                            j += 1
                        self.build_choice_run(sec_id, run)
                        if self.dry_run:
                            for c in run[1:]:
                                self._plan(f"    - quiz (grouped)         {_short(c.get('prompt_html'))}")
                        i = j - 1
                except LanglistoError as e:
                    self._warn(f"{item.get('id')}: {e.message}")
                i += 1
        self._save()
        return self.summary()

    def summary(self):
        return {
            "lesson_title": self.lesson_title,
            "dry_run": self.dry_run,
            "sections": len(self.state["sections"]) if not self.dry_run else
            len(self.spec.get("sections") or []),
            "exercises": len(self.state["exercises"]),
            "warnings": self.state["warnings"],
            "plan": self.plan.lines,
        }


def build_material(api, spec, course=None, state_path=None, dry_run=False, pace=0.35,
                   media_dir=None, spec_dir=None, log=print):
    builder = MaterialBuilder(api, spec, course=course, state_path=state_path,
                              dry_run=dry_run, pace=pace, media_dir=media_dir,
                              spec_dir=spec_dir, log=log)
    return builder.run()
