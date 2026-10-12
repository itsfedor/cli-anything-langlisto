"""Offline CLI-surface tests: the `homework` group, `preview open`
class/unit/tab convenience, `sections` and `units cover`.
click.testing.CliRunner + stubbed Langlisto client — no network.
Run: python3 -m pytest tests/ -q
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from click.testing import CliRunner  # noqa: E402

import cli_langlisto.langlisto_cli as cli_mod  # noqa: E402

CID = "cl-1"


def run(args, fake, **kw):
    old = cli_mod.Langlisto
    cli_mod.Langlisto = lambda *a, **k: fake
    try:
        return CliRunner().invoke(cli_mod.cli, args, **kw)
    finally:
        cli_mod.Langlisto = old


def out_of(res):
    return res.output + (getattr(res, "stderr", "") or "")


ALL_SOURCE_IDS = {"e-note", "e-test", "e-new", "e-mat"}


class HWApi:
    """Stub of the Langlisto API surface used by the new commands."""

    def __init__(self):
        self.log = []                  # classroom-scoped generic calls
        self.classrooms = [{"id": CID, "name": "CLI Lab (test)", "kind": "group"}]
        self.students = [{"id": "st-1", "name": "Надя", "email": "nadya@example.com"}]
        self.members = {CID: [{"id": "st-demo", "name": "Demo Student", "is_demo": True}]}
        self.units = [
            {"id": "u-room", "title": "CLI Lab room lesson", "cover_url": None},
            {"id": "u-mat", "title": "ZZ Type Catalog", "classroom_id": None,
             "cover_url": None},
        ]
        self.hw_sec = {"u-room": "hw-sec"}
        self.workbook = {}             # unit → prepared bucket items
        self.wb_moves = []             # (dest_section_id, ids) — move-to-section calls
        self.lesson_ex = {
            "u-room": [
                {"id": "e-note", "type": "note",
                 "label": "Homework lab — read this note first.", "heading": ""},
                {"id": "e-test", "type": "untimed_test",
                 "label": "Quiz: present simple", "heading": ""},
                {"id": "e-new", "type": "note", "label": "Fresh note", "heading": ""},
            ],
            "u-mat": [
                {"id": "e-mat", "type": "note", "label": "Material note", "heading": ""},
            ],
        }
        # platform truth: choose copies exercises into the hidden section;
        # the classroom starts with exactly ONE already attached item
        self.assigned = {"u-room": ["e-note"]}
        self.copies = {"hw-sec": [{"id": "copy-1", "type": "note",
                                   "text": "Homework lab — read this note first.",
                                   "position": 0}]}
        self.chosen_calls = []         # (unit, classroom_id, ids)
        self.hw_calls = []             # (kind, unit, classroom_id)
        self.hw_removed = []           # (unit, classroom_id, source_id)
        self.hw_unassigned = []        # (unit, classroom_id, ids)
        self.submissions = {"u-room": [
            {"unit_id": "u-room", "student_id": "st-demo", "status": "assigned",
             "submitted_at": None, "completed_at": None, "review_comment": None}]}
        self.complete_calls = []       # (unit, classroom_id, student_id)
        self.revision_calls = []       # (unit, classroom_id, student_id, comment)
        self.sections = {"u-room": [{"id": "s-1", "title": "A", "position": 1},
                                    {"id": "s-2", "title": "B", "position": 2}]}
        self.sections_order_calls = []  # (unit, ids)
        self.section_deleted = []
        self.unit_patches = []         # (unit, fields)
        self.uploads = []              # (path, kind)
        self.demo_calls = []           # next paths

    # ---- classes / students / units
    def classrooms_all(self):
        return self.classrooms

    def classroom_students(self, cid):
        return self.members.get(cid, [])

    def students_all(self):
        return self.students

    def units_list(self, cid):
        return self.units

    # ---- homework
    def classroom_homeworks(self, cid, student_id=None):
        self.log.append(("classroom_homeworks", cid, student_id))
        status = None
        if student_id:
            rec = next((s for s in self.submissions["u-room"]
                        if s["student_id"] == student_id), None)
            status = (rec or {}).get("status")
        if self.assigned["u-room"]:
            return [{"unit_id": "u-room", "has_exercises": True, "status": status}]
        return []

    def unit_homework_section(self, uid, classroom_id=None):
        self.hw_calls.append(("section", uid, classroom_id))
        return {"section_id": self.hw_sec.get(uid)}

    def unit_homework_lesson_exercises(self, uid, classroom_id=None):
        self.hw_calls.append(("lesson", uid, classroom_id))
        return [dict(x) for x in self.lesson_ex.get(uid, [])]

    def unit_homework_workbook_exercises(self, uid, classroom_id=None):
        self.hw_calls.append(("workbook", uid, classroom_id))
        return [dict(x) for x in self.workbook.get(uid, [])]

    def unit_workbook_section(self, uid):
        """The unit's «Exercises for homework» bucket (get-or-create)."""
        self.hw_calls.append(("workbook_section", uid, None))
        return {"id": "wb-sec", "unit_id": uid, "position": 9, "title": None}

    def exercises_move_to_section(self, dest_section_id, exercise_ids):
        self.wb_moves.append((dest_section_id, list(exercise_ids)))
        moved, skipped = 0, 0
        for eid in exercise_ids:
            hit = None
            for lst in self.copies.values():
                hit = next((e for e in lst if e["id"] == eid), None)
                if hit:
                    lst.remove(hit)
                    break
            if hit:
                self.workbook.setdefault("u-room", []).append(hit)
                moved += 1
            else:
                skipped += 1
        return {"moved": moved, "skipped": skipped}

    def unit_homework_assigned(self, uid, classroom_id=None):
        self.hw_calls.append(("assigned", uid, classroom_id))
        return list(self.assigned.get(uid, []))

    def homework_choose(self, uid, classroom_id, exercise_ids):
        self.chosen_calls.append((uid, classroom_id, list(exercise_ids)))
        for eid in exercise_ids:
            if eid in ALL_SOURCE_IDS and eid not in self.assigned[uid]:
                self.assigned[uid].append(eid)         # idempotent on the fake too
                lst = self.copies.setdefault(self.hw_sec[uid], [])
                lst.append({"id": f"copy-{eid}", "type": "note", "text": "copy",
                            "position": len(lst)})

    def homework_unassign(self, uid, classroom_id, exercise_ids):
        self.hw_unassigned.append((uid, classroom_id, list(exercise_ids)))
        self.assigned[uid] = [e for e in self.assigned[uid] if e not in exercise_ids]

    def homework_remove(self, uid, classroom_id, source_id):
        self.hw_removed.append((uid, classroom_id, source_id))
        self.assigned[uid] = [e for e in self.assigned[uid] if e != source_id]

    def homework_submissions(self, uid, classroom_id):
        self.hw_calls.append(("submissions", uid, classroom_id))
        return [dict(s) for s in self.submissions.get(uid, [])]

    def homework_complete(self, uid, classroom_id, student_id):
        self.complete_calls.append((uid, classroom_id, student_id))
        for s in self.submissions[uid]:
            if s["student_id"] == student_id:
                s["status"] = "completed"

    def homework_revision(self, uid, classroom_id, student_id, comment=""):
        self.revision_calls.append((uid, classroom_id, student_id, comment))
        for s in self.submissions[uid]:
            if s["student_id"] == student_id:
                s["status"] = "revision"
                s["review_comment"] = comment

    # ---- sections
    def unit_sections(self, uid, classroom_id=None):
        return [dict(s) for s in self.sections.get(uid, [])]

    def unit_sections_order(self, uid, section_ids):
        self.sections_order_calls.append((uid, list(section_ids)))
        pos = {sid: i + 1 for i, sid in enumerate(section_ids)}
        for s in self.sections.get(uid, []):
            if s["id"] in pos:
                s["position"] = pos[s["id"]]

    def section_delete(self, sid):
        self.section_deleted.append(sid)
        for uid, lst in self.sections.items():
            self.sections[uid] = [s for s in lst if s["id"] != sid]
        self.copies.pop(sid, None)

    def section_exercises(self, sid):
        return [dict(e) for e in self.copies.get(sid, [])]

    # ---- units cover / uploads / preview
    def unit_patch(self, uid, **fields):
        self.unit_patches.append((uid, dict(fields)))
        for u in self.units:
            if u["id"] == uid and "cover_url" in fields:
                u["cover_url"] = fields["cover_url"] or None   # '' → null
        return {"id": uid}

    def upload(self, filepath, kind="auto"):
        self.uploads.append((filepath, kind))
        return "https://cdn.example/cover.png"

    def demo_student_open(self, next_path=None):
        self.demo_calls.append(next_path)
        return {"url": "https://demoview.langlisto.com/auth?next=" + (next_path or "/my-classes")}


# ---------------------------------------------------------------- homework
def test_homework_show_reads_copies_with_classroom_id():
    api = HWApi()
    r = run(["--json", "homework", "show", "--class", "CLI Lab (test)",
             "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["section_id"] == "hw-sec"
    assert [e["id"] for e in d["exercises"]] == ["copy-1"]
    assert ("section", "u-room", "cl-1") in api.hw_calls


def test_homework_list_filters_and_student_status():
    api = HWApi()
    r = run(["--json", "homework", "list", "--class", "cl-1"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["classroom_id"] == "cl-1" and d["student_id"] is None
    assert d["homeworks"][0]["unit_id"] == "u-room"
    assert d["homeworks"][0]["unit_title"] == "CLI Lab room lesson"
    assert d["homeworks"][0]["status"] is None            # platform null without student
    # per-student status
    r = run(["--json", "homework", "list", "--class", "cl-1", "--student", "st-demo"], api)
    d = json.loads(r.output)
    assert d["student_id"] == "st-demo" and d["homeworks"][0]["status"] == "assigned"
    assert ("classroom_homeworks", "cl-1", "st-demo") in api.log
    # unit filter resolves by title; unknown unit → usage error
    r = run(["--json", "homework", "list", "--class", "cl-1", "--unit", "room lesson"], api)
    assert json.loads(r.output)["homeworks"]
    assert run(["homework", "list", "--class", "cl-1", "--unit", "nope"], api).exit_code == 2


def test_homework_available_groups_and_assigned_marks():
    api = HWApi()
    r = run(["--json", "homework", "available", "--class", "cl-1",
             "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert [x["id"] for x in d["lesson"]] == ["e-note", "e-test", "e-new"]
    assert d["workbook"] == []
    marks = {x["id"]: x["already_assigned"] for x in d["lesson"]}
    assert marks == {"e-note": True, "e-test": False, "e-new": False}
    assert ("lesson", "u-room", "cl-1") in api.hw_calls
    assert ("workbook", "u-room", "cl-1") in api.hw_calls
    assert ("assigned", "u-room", "cl-1") in api.hw_calls


def test_homework_available_from_material_source():
    api = HWApi()
    r = run(["--json", "homework", "available", "--class", "cl-1", "--unit", "u-room",
             "--from-material", "u-mat"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["source_unit_id"] == "u-mat"
    assert [x["id"] for x in d["lesson"]] == ["e-mat"]     # Lesson group of the material
    assert ("lesson", "u-mat", "cl-1") in api.hw_calls     # read with the class's id
    assert ("workbook", "u-room", "cl-1") in api.hw_calls  # workbook stays the target's


def test_homework_choose_dry_run_prints_payload_no_write():
    api = HWApi()
    r = run(["--json", "homework", "choose", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-new", "--dry-run"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["dry_run"] is True
    assert d["payload"] == {"exercise_ids": ["e-new"]}
    assert d["url"] == "/units/u-room/homework/choose?classroom_id=cl-1"
    assert d["resolved"][0] == {"id": "e-new", "found": True, "type": "note",
                                "label": "Fresh note", "heading": ""}
    assert api.chosen_calls == []                    # nothing written
    assert api.assigned["u-room"] == ["e-note"]      # state untouched
    # already-assigned ids are flagged
    r = run(["--json", "homework", "choose", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-note", "--dry-run"], api)
    assert json.loads(r.output)["already_assigned"] == ["e-note"]


def test_homework_choose_live_idempotent_readback():
    api = HWApi()
    r = run(["--json", "homework", "choose", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-new,e-test"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.chosen_calls == [("u-room", "cl-1", ["e-new", "e-test"])]
    d = json.loads(r.output)
    assert d["confirmed"] is True
    assert set(d["assigned_sources"]) == {"e-note", "e-new", "e-test"}
    # re-choose an assigned id → no duplicate, still confirmed (idempotent)
    r = run(["--json", "homework", "choose", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-new"], api)
    assert json.loads(r.output)["confirmed"] is True
    assert api.assigned["u-room"].count("e-new") == 1
    # missing --exercise → usage error
    assert run(["homework", "choose", "--class", "cl-1", "--unit", "u-room"],
               api).exit_code == 2


def test_homework_choose_readback_miss_exits_1():
    api = HWApi()
    r = run(["--json", "homework", "choose", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-ghost"], api)
    assert r.exit_code == 1
    d = json.loads(r.output)
    assert d["confirmed"] is False and d["missing"] == ["e-ghost"]


def test_homework_remove_uses_source_id_and_noops():
    api = HWApi()
    r = run(["--json", "homework", "remove", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-note"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.hw_removed == [("u-room", "cl-1", "e-note")]   # SOURCE id + classroom
    assert json.loads(r.output)["assigned_sources"] == []
    # removing something not assigned → no-op, no extra call
    r = run(["--json", "homework", "remove", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-test"], api)
    assert r.exit_code == 0 and json.loads(r.output)["removed"] is False
    assert len(api.hw_removed) == 1


def test_homework_clear_bulk_and_skips_unassigned():
    api = HWApi()
    api.assigned["u-room"] = ["e-note", "e-test"]
    r = run(["--json", "homework", "clear", "--class", "cl-1", "--unit", "u-room",
             "--exercise", "e-note,e-test,e-new"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert api.hw_unassigned == [("u-room", "cl-1", ["e-note", "e-test"])]
    assert d["skipped_not_assigned"] == ["e-new"]
    assert d["assigned_sources"] == [] and d["confirmed"] is True


def test_homework_review_return_accept():
    api = HWApi()
    r = run(["--json", "homework", "review", "--class", "cl-1", "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["submissions"][0]["student_name"] == "Demo Student"
    # return → revision + comment read-back
    r = run(["--json", "homework", "return", "--class", "cl-1", "--unit", "u-room",
             "--student", "st-demo", "--comment", "Fix Q2"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.revision_calls == [("u-room", "cl-1", "st-demo", "Fix Q2")]
    d = json.loads(r.output)
    assert d["returned"] is True and d["review_comment"] == "Fix Q2"
    # accept by class-member NAME (demo students are not in /students/all)
    r = run(["--json", "homework", "accept", "--class", "cl-1", "--unit", "u-room",
             "--student", "Demo Student"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.complete_calls == [("u-room", "cl-1", "st-demo")]
    assert json.loads(r.output)["accepted"] is True


def test_homework_accept_readback_mismatch_exits_1():
    api = HWApi()
    api.homework_complete = lambda uid, cid, sid: None     # server ignored it
    r = run(["homework", "accept", "--class", "cl-1", "--unit", "u-room",
             "--student", "st-demo"], api)
    assert r.exit_code == 1


def test_homework_submit_stub_is_deferred():
    api = HWApi()
    r = run(["homework", "submit", "--class", "cl-1", "--unit", "u-room"], api)
    assert r.exit_code == 2
    msg = out_of(r)
    assert "deferred" in msg and "preview open" in msg


def test_homework_assigned_resolves_source_labels():
    api = HWApi()
    r = run(["--json", "homework", "assigned", "--class", "cl-1", "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["assigned_sources"] == ["e-note"]
    assert d["resolved"][0]["label"] == "Homework lab — read this note first."
    assert d["resolved"][0]["type"] == "note"
    assert ("assigned", "u-room", "cl-1") in api.hw_calls
    # empty state renders cleanly
    api.assigned["u-room"] = []
    r = run(["--json", "homework", "assigned", "--class", "cl-1", "--unit", "u-room"], api)
    assert r.exit_code == 0 and json.loads(r.output)["assigned_sources"] == []
    # an id from another material stays bare (no label)
    api.assigned["u-room"] = ["e-mat"]
    r = run(["--json", "homework", "assigned", "--class", "cl-1", "--unit", "u-room"], api)
    d = json.loads(r.output)
    assert d["resolved"][0] == {"id": "e-mat", "type": None, "label": None, "heading": None}


def test_every_homework_call_carries_classroom_id():
    api = HWApi()
    run(["homework", "list", "--class", "cl-1"], api)
    run(["homework", "show", "--class", "cl-1", "--unit", "u-room"], api)
    run(["homework", "available", "--class", "cl-1", "--unit", "u-room"], api)
    run(["homework", "assigned", "--class", "cl-1", "--unit", "u-room"], api)
    run(["homework", "choose", "--class", "cl-1", "--unit", "u-room", "--exercise", "e-new"], api)
    run(["homework", "choose", "--class", "cl-1", "--unit", "u-room", "--exercise", "e-test",
         "--dry-run"], api)
    run(["homework", "review", "--class", "cl-1", "--unit", "u-room"], api)
    run(["homework", "clear", "--class", "cl-1", "--unit", "u-room", "--exercise", "e-test"], api)
    run(["homework", "remove", "--class", "cl-1", "--unit", "u-room", "--exercise", "e-note"], api)
    assert api.hw_calls and all(c[2] == "cl-1" for c in api.hw_calls), api.hw_calls
    assert all(c[1] == "cl-1" for c in api.chosen_calls)
    assert all(c[1] == "cl-1" for c in api.hw_removed)
    assert all(c[1] == "cl-1" for c in api.hw_unassigned)
    assert all(c[1] == "cl-1" for c in api.log if c[0] == "classroom_homeworks")


# ------------------------------------------- homework bucket prepare / bucket
def _hw_api_with_section():
    api = HWApi()
    api.sections["u-room"] = [{"id": "s-1", "title": "A", "position": 1},
                              {"id": "s-hw", "title": "Homework", "position": 2}]
    api.copies["s-hw"] = [
        {"id": "e-hw1", "type": "note", "position": 0},
        {"id": "e-hw2", "type": "enter_word_gap", "position": 1},
    ]
    return api


def test_homework_prepare_moves_section_then_deletes_it():
    api = _hw_api_with_section()
    r = run(["--json", "homework", "prepare", "--unit", "CLI Lab room lesson",
             "--class", "cl-1", "--section", "Homework"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["section_id"] == "s-hw" and d["section_title"] == "Homework"
    assert d["workbook_section_id"] == "wb-sec"
    assert d["total"] == 2 and d["moved"] == 2 and d["skipped"] == 0
    assert d["readback_missing"] == [] and d["confirmed"] is True
    assert d["section_deleted"] is True and d["section_remaining"] == 0
    assert api.wb_moves == [("wb-sec", ["e-hw1", "e-hw2"])]
    assert api.section_deleted == ["s-hw"]
    assert "s-hw" not in [s["id"] for s in api.sections["u-room"]]
    assert [w["id"] for w in api.workbook["u-room"]] == ["e-hw1", "e-hw2"]


def test_homework_prepare_by_section_id_and_rerun_finds_nothing():
    api = _hw_api_with_section()
    r = run(["--json", "homework", "prepare", "--unit", "u-room", "--class", "cl-1",
             "--section", "s-hw"], api)
    assert r.exit_code == 0, out_of(r)
    assert json.loads(r.output)["section_deleted"] is True
    # re-run: the section is gone → clean usage error, nothing written
    r2 = run(["homework", "prepare", "--unit", "u-room", "--class", "cl-1",
              "--section", "s-hw"], api)
    assert r2.exit_code == 2
    assert len(api.wb_moves) == 1


def test_homework_prepare_skips_delete_on_move_failure():
    api = _hw_api_with_section()

    def half(dest, ids):
        api.wb_moves.append((dest, list(ids)))
        e = api.copies["s-hw"][0]                      # only the first one moves
        api.copies["s-hw"].remove(e)
        api.workbook.setdefault("u-room", []).append(e)
        return {"moved": 1, "skipped": 1}

    api.exercises_move_to_section = half
    r = run(["--json", "homework", "prepare", "--unit", "u-room", "--class", "cl-1",
             "--section", "Homework"], api)
    assert r.exit_code == 1, out_of(r)
    d = json.loads(r.output)
    assert d["moved"] == 1 and d["skipped"] == 1
    assert d["readback_missing"] == ["e-hw2"]
    assert d["section_deleted"] is False and d["confirmed"] is False
    assert api.section_deleted == []                   # NOT deleted after a failure
    assert "s-hw" in [s["id"] for s in api.sections["u-room"]]


def test_homework_prepare_keep_section():
    api = _hw_api_with_section()
    r = run(["--json", "homework", "prepare", "--unit", "u-room", "--class", "cl-1",
             "--section", "Homework", "--keep-section"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["keep_section"] is True and d["section_deleted"] is False
    assert d["confirmed"] is True and d["moved"] == 2
    assert api.section_deleted == []
    assert "s-hw" in [s["id"] for s in api.sections["u-room"]]


def test_homework_prepare_unit_title_needs_class():
    api = _hw_api_with_section()
    r = run(["homework", "prepare", "--unit", "CLI Lab room lesson", "--section", "Homework"], api)
    assert r.exit_code == 2
    assert "class" in out_of(r)
    assert api.wb_moves == []


def test_homework_bucket_lists_prepared_items():
    api = HWApi()
    api.workbook["u-room"] = [
        {"id": "wb1", "type": "note", "label": "1", "heading": ""},
        {"id": "wb2", "type": "enter_word_gap", "label": "2", "heading": "Gap"},
    ]
    r = run(["--json", "homework", "bucket", "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["count"] == 2 and [x["id"] for x in d["workbook"]] == ["wb1", "wb2"]
    assert ("workbook", "u-room", None) in api.hw_calls   # bare read — no classroom_id
    r = run(["homework", "bucket", "--unit", "u-empty"], api)
    assert r.exit_code == 0 and "empty" in r.output


# ---------------------------------------------------------------- preview open
def test_preview_open_class_room_deep_links():
    api = HWApi()
    r = run(["--json", "preview", "open", "--class", "cl-1", "--unit", "u-room",
             "--tab", "homework"], api)
    assert r.exit_code == 0, out_of(r)
    d = json.loads(r.output)
    assert d["next"] == "/classrooms/cl-1/room?unit=u-room&tab=homework"
    assert d["url"].endswith("next=" + d["next"])
    assert api.demo_calls == ["/classrooms/cl-1/room?unit=u-room&tab=homework"]
    # tab only → /classrooms/{cid}/room?tab=…
    r = run(["--json", "preview", "open", "--class", "cl-1", "--tab", "lesson"], api)
    assert json.loads(r.output)["next"] == "/classrooms/cl-1/room?tab=lesson"
    # class only → plain room
    r = run(["--json", "preview", "open", "--class", "cl-1"], api)
    assert json.loads(r.output)["next"] == "/classrooms/cl-1/room"
    # legacy modes unchanged
    assert json.loads(run(["--json", "preview", "open"], api).output)["next"] == "/my-classes"
    r = run(["--json", "preview", "open", "--next", "/my-classes"], api)
    assert json.loads(r.output)["next"] == "/my-classes"
    # misuse → usage errors
    assert run(["preview", "open", "--tab", "homework"], api).exit_code == 2
    assert run(["preview", "open", "--unit", "u-room"], api).exit_code == 2
    assert run(["preview", "open", "--class", "cl-1", "--next", "/x"], api).exit_code == 2


# ---------------------------------------------------------------- sections
def test_sections_list_reorder_delete():
    api = HWApi()
    r = run(["--json", "sections", "list", "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    assert [s["id"] for s in json.loads(r.output)["sections"]] == ["s-1", "s-2"]
    # reorder: PUT body = array order; read-back verified
    r = run(["--json", "sections", "reorder", "--unit", "u-room", "--ids", "s-2,s-1"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.sections_order_calls == [("u-room", ["s-2", "s-1"])]
    d = json.loads(r.output)
    assert d["ok"] is True and d["readback"] == ["s-2", "s-1"]
    # delete with read-back through the unit
    r = run(["--json", "sections", "delete", "--section", "s-1", "--unit", "u-room"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.section_deleted == ["s-1"]
    assert json.loads(r.output)["confirmed"] is True
    # delete without --unit: allowed, reported as unverified
    r = run(["sections", "delete", "--section", "s-2"], api)
    assert r.exit_code == 0 and "read back" in r.output


def test_sections_reorder_readback_mismatch_exits_1():
    api = HWApi()
    api.unit_sections_order = lambda uid, ids: None        # silently ignored
    r = run(["sections", "reorder", "--unit", "u-room", "--ids", "s-2,s-1"], api)
    assert r.exit_code == 1


# ---------------------------------------------------------------- units cover
def test_units_cover_set_url_file_clear():
    api = HWApi()
    r = run(["--json", "units", "cover", "set", "--unit", "u-room",
             "--url", "https://cdn.example/a.png", "--class", "cl-1"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.unit_patches == [("u-room", {"cover_url": "https://cdn.example/a.png"})]
    assert json.loads(r.output)["confirmed"] is True
    # --file uploads first, then sets the returned URL
    with tempfile.NamedTemporaryFile("w", suffix=".png", delete=False) as f:
        f.write("pretend-png")
        fname = f.name
    r = run(["--json", "units", "cover", "set", "--unit", "u-room",
             "--file", fname, "--class", "cl-1"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.uploads and api.uploads[-1][1] == "image"
    assert json.loads(r.output)["cover_url"] == "https://cdn.example/cover.png"
    # clear sends '' (→ null) and reads back clean
    r = run(["--json", "units", "cover", "clear", "--unit", "u-room", "--class", "cl-1"], api)
    assert r.exit_code == 0, out_of(r)
    assert api.unit_patches[-1] == ("u-room", {"cover_url": ""})
    assert json.loads(r.output)["confirmed"] is True
    # both/neither selectors → usage errors
    assert run(["units", "cover", "set", "--unit", "u-room"], api).exit_code == 2
    assert run(["units", "cover", "set", "--unit", "u-room", "--url", "x",
                "--file", fname], api).exit_code == 2


# ---------------------------------------------------------------- api wire shapes
def test_api_methods_wire_shapes_against_fake_client():
    """core.api → wire: classroom_id lives in the query string; bodies carry
    source ids / section_ids exactly as RECON2 §3.2 and RECON3 §2 describe."""
    from cli_langlisto.core.api import Langlisto

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, json_body=None, params=None):
            self.calls.append((method, path, json_body, params))
            return 200, None

        def get(self, path, params=None):
            return self.request("GET", path, params=params)[1]

        def post(self, path, json_body=None, params=None):
            return self.request("POST", path, json_body=json_body, params=params)[1]

        def put(self, path, json_body=None):
            return self.request("PUT", path, json_body=json_body)[1]

        def patch(self, path, json_body=None):
            return self.request("PATCH", path, json_body=json_body)[1]

        def delete(self, path, params=None):
            return self.request("DELETE", path, params=params)[1]

    c = FakeClient()
    a = Langlisto(c)
    a.unit_homework_section("u1", classroom_id="cid-1")
    a.unit_homework_lesson_exercises("u1", classroom_id="cid-1")
    a.unit_homework_assigned("u1", "cid-1")
    a.homework_choose("u1", "cid-1", ["e1", "e2"])
    a.homework_unassign("u1", "cid-1", ["e1"])
    a.homework_remove("u1", "cid-1", "e1")
    a.homework_complete("u1", "cid-1", "s1")
    a.homework_revision("u1", "cid-1", "s1", "fix this")
    a.classroom_homeworks("cid-1", student_id="s1")
    a.unit_sections_order("u1", ["sec-2", "sec-1"])
    a.section_delete("sec-9")
    a.unit_patch("u1", cover_url="")
    a.unit_workbook_section("u1")
    a.exercises_move_to_section("sec-wb", ["e1", "e2"])
    calls = c.calls
    assert calls[0] == ("GET", "/units/u1/homework", None, {"classroom_id": "cid-1"})
    assert calls[1] == ("GET", "/units/u1/homework/lesson-exercises", None,
                        {"classroom_id": "cid-1"})
    assert calls[2] == ("GET", "/units/u1/homework/assigned-sources", None,
                        {"classroom_id": "cid-1"})
    assert calls[3] == ("POST", "/units/u1/homework/choose",
                        {"exercise_ids": ["e1", "e2"]}, {"classroom_id": "cid-1"})
    assert calls[4] == ("POST", "/units/u1/homework/unassign",
                        {"exercise_ids": ["e1"]}, {"classroom_id": "cid-1"})
    assert calls[5] == ("DELETE", "/units/u1/homework/assigned/e1",
                        None, {"classroom_id": "cid-1"})       # SOURCE id in the path
    assert calls[6] == ("POST", "/units/u1/homework/students/s1/complete",
                        None, {"classroom_id": "cid-1"})
    assert calls[7] == ("POST", "/units/u1/homework/students/s1/revision",
                        {"comment": "fix this"}, {"classroom_id": "cid-1"})
    assert calls[8] == ("GET", "/classrooms/cid-1/homeworks", None, {"student_id": "s1"})
    assert calls[9] == ("PUT", "/units/u1/sections/order",
                        {"section_ids": ["sec-2", "sec-1"]}, None)
    assert calls[10] == ("DELETE", "/sections/sec-9", None, None)
    assert calls[11] == ("PATCH", "/units/u1", {"cover_url": ""}, None)
    assert calls[12] == ("GET", "/units/u1/workbook-section", None, None)
    assert calls[13] == ("POST", "/exercises/move-to-section",
                         {"dest_section_id": "sec-wb", "exercise_ids": ["e1", "e2"]}, None)
    # without classroom_id the material-unit path is *not* taken silently:
    # params are simply omitted (the platform then answers 400) — never a wrong id
    c.calls.clear()
    a.unit_homework_lesson_exercises("umat")
    assert c.calls[0] == ("GET", "/units/umat/homework/lesson-exercises", None, None)
