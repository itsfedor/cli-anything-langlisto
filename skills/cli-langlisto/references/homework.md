# Homework — authoring (workbook bucket) + the classroom «Homework» tab

Verified live against the platform (recon `RECON2-homework.md`, CLI waves W2b
and W2c, 2026-10-12/13; workbook semantics probe `cli-lab/probe2/wb_semantics*.py`).

Homework has TWO layers:

* **Authoring — the workbook bucket (canonical).** Every unit has a hidden
  *workbook-section*: the «Exercises for homework» group in the room's picker
  ("Prepare the homework exercises for this lesson here. Students only see
  them once you assign them as homework"). Homework content belongs THERE —
  it stays out of the lesson flow and is invisible to students until assigned.
  New lessons author it with a spec section flagged `"homework": true`;
  existing lessons migrate with `homework prepare`.
* **The classroom «Homework» tab — the class layer.** Assigning (`choose`)
  COPIES bucket (or lesson) exercises for a class and adds per-student status,
  submit and review.

> The **visible «Homework» section inside a lesson is the OLD model** — a
> regular section students walk through as part of the lesson. It still works
> (nothing breaks, legacy books keep it), but new homework is authored into
> the bucket; migrate legacy lessons with `homework prepare`.

## The four facts that matter

1. **Keyed pair `(classroom_id, unit_id)`.** Every homework endpoint is scoped
   by the classroom. On a material unit (one with no classroom of its own) the
   management endpoints answer `400 homework_not_classroom_unit` unless
   `classroom_id` is passed. The CLI ALWAYS sends it — never call these
   endpoints bare.
2. **Every unit has two hidden sections.** The *workbook-section* — homework
   AUTHORING: `GET /units/{id}/workbook-section` (the «Exercises for homework»
   bucket; its content is also `…/homework/workbook-exercises`) — and the
   *homework section* the assignment copies land in: `GET /units/{id}/homework`
   → `{section_id}`. Neither ever appears in `/units/{id}/sections`.
   `homework show` reads the second one — i.e. exactly what the student sees in
   the room's Homework tab.
3. **Source ids in, copies out.** `choose` COPIES the selected exercises into
   the hidden section under fresh ids. All management calls (`remove`, `clear`,
   the 「Already in homework」 badge) address the **source id** — the id of the
   exercise in the lesson/material, not the copy id.
4. **`choose` is idempotent.** Re-choosing an assigned exercise is a 204 no-op:
   no duplicate copy, `assigned-sources` unchanged.

Status lifecycle per (unit, student): `assigned` → `submitted` →
(`revision` ⇄ re-submit) → `completed`. Only the STUDENT submits; the teacher
accepts (`complete`) or sends back (`revision` + comment, shown to the student).

## CLI flow

```bash
CID=<classroom id>   UID=<unit id>

# 0) authoring — the unit's workbook bucket («Exercises for homework»)
cli-langlisto homework bucket --unit $UID [--json]         # what is prepared
cli-langlisto homework prepare --unit $UID --section Homework \
    [--class $CID] [--keep-section] \
    # moves ALL exercises of that regular section into the bucket (moved/skipped
    # counted, read back via workbook-exercises), then DELETES the emptied
    # section unless --keep-section — deletion cascades: the section's exercises
    # and students' answers on them are gone. Nothing is deleted on failure.

# 1) what is where
cli-langlisto homework list --class $CID [--unit $UID] [--student <sid>] [--json]
cli-langlisto homework show --class $CID --unit $UID        # the student's view (copies)

# 2) pick sources — the unit's own Lesson group, or another material's
cli-langlisto homework available --class $CID --unit $UID [--json]
cli-langlisto homework available --class $CID --unit $UID --from-material <matUnit>

# 3) assign (dry-run first!)
cli-langlisto homework choose --class $CID --unit $UID --exercise <src-id>... --dry-run
cli-langlisto homework choose --class $CID --unit $UID --exercise <src-id>...
cli-langlisto homework assigned --class $CID --unit $UID    # source ids currently in place

# 4) unassign (reversible)
cli-langlisto homework remove --class $CID --unit $UID --exercise <src-id>
cli-langlisto homework clear  --class $CID --unit $UID --exercise <id>,<id>...

# 5) review
cli-langlisto homework review --class $CID --unit $UID      # submissions + statuses
cli-langlisto homework accept --class $CID --unit $UID --student <sid>
cli-langlisto homework return --class $CID --unit $UID --student <sid> --comment "Fix Q2"
```

`choose --dry-run` resolves the given ids against the source unit's
lesson-exercises and prints the exact payload + URL — it writes nothing.
`list` without `--student` shows `status: null` (the platform reports personal
statuses only per student); pass `--student` (name or id — demo students
resolve through the class member list) for `assigned/submitted/revision/completed`.
`remove`/`clear` are no-ops for ids that are not currently assigned.

## HTTP recipe (what the CLI wraps)

```http
# authoring — the workbook bucket («Exercises for homework»)
GET    /units/{uid}/workbook-section                               → {id, title:null, …}  (get-or-create)
GET    /units/{uid}/homework/workbook-exercises?classroom_id={cid} → [{id,type,label,heading}]  (the bucket's exercises)
POST   /exercises/move-to-section   {"dest_section_id": …, "exercise_ids": [<src>…]}  → {"moved":N,"skipped":M}

# assigning — the hidden homework section (class layer)
GET    /units/{uid}/homework?classroom_id={cid}                    → {section_id}
GET    /units/{uid}/homework/lesson-exercises?classroom_id={cid}   → [{id,type,label,heading}]
POST   /units/{uid}/homework/choose?classroom_id={cid}    {"exercise_ids":[<src>…]}  → 204
GET    /units/{uid}/homework/assigned-sources?classroom_id={cid}   → [<src>…]
DELETE /units/{uid}/homework/assigned/{src}?classroom_id={cid}     → 204
POST   /units/{uid}/homework/unassign?classroom_id={cid}  {"exercise_ids":[…]}       → 204
GET    /units/{uid}/homework/submissions?classroom_id={cid}        → per-student records
POST   /units/{uid}/homework/students/{sid}/complete?classroom_id={cid}              → completed
POST   /units/{uid}/homework/students/{sid}/revision?classroom_id={cid} {"comment"}  → revision
GET    /classrooms/{cid}/homeworks[?student_id={sid}]              → [{unit_id,has_exercises,status|null}]
```

`GET /sections/{hwsec}/exercises` returns the copies with FULL payloads
(notes, test questions with `is_correct`, teacher_notes), so `homework show`
needs no per-exercise drill-down.

`move-to-section` keeps the exercise ids and works between the sections of ONE
unit; the moved items leave the regular section and do NOT show in the lesson
flow (`/units/{id}/sections` and `/homework/lesson-exercises` exclude them,
verified 2026-10-13). A prepared bucket item is assignable exactly like any
source id (choose → `assigned-sources` → delete rolls back).

## Authoring rules (the corrected model)

- **Homework content lives in the workbook bucket («Exercises for homework»),
  not in a visible section.** In a lesson spec, flag the section
  `"homework": true`: the builder creates no regular section and routes its
  items into the unit's workbook-section (state records
  `{"kind": "workbook", "section_id": …}`, so resume stays idempotent;
  dry-run plans show «(homework bucket)»). Students never see them in the
  lesson flow — only once assigned.
- **Migrate legacy lessons with `homework prepare`** — the OLD model kept
  homework as a visible «Homework» section the student walked through. The
  command moves ALL exercises of that section into the bucket and, unless
  `--keep-section`, DELETES the emptied section (deletion cascades: exercises
  and students' answers). Nothing is deleted when the move or the read-back
  failed; `moved`/`skipped` and the `workbook-exercises` read-back are
  reported (exit 1 when not confirmed).
- Assigning stays ADDITIVE and reversible — open the lesson in the class, then
  choose the prepared exercises into the room's homework tab:

```bash
cli-langlisto homework choose --class $CID --unit $CLASSROOM_UNIT \
    --from-material $BOOK_UNIT --exercise <ids from the workbook group>
```

  (`--from-material` = the picker's «Choose from other materials»; the copy
  then lives in the class. Target must be the **classroom unit** — never
  assign homework directly onto a material unit: the UI doesn't, and such an
  assignment only surfaces if that unit is in the class's unit list.)

## Student view & testing the student's flow

`preview open --class <cid> [--unit <uid>] [--tab homework]` opens the
platform's demo student (single-use magic link, `POST /demo-student/open`)
directly in the room's Homework tab: status banner, content, Submit button.
Student-side `homework submit` needs a student session and is **deferred** in
the CLI — the demo-student deep link is the supported way to exercise it.

## Observed gotchas

- After an add + remove round-trip on a unit whose submission was `completed`,
  the student's status read back as `assigned` (seen 2026-10-13): expect the
  submission record to react when the homework content changes; verify the
  **assignment set** (`assigned-sources`) when restoring state.
- `workbook-exercises` («Exercises for homework» picker group) lists the unit's
  workbook-section exercises — the PREPARED homework (`homework bucket`), not
  the assigned one; it is empty until something has been prepared
  (semantics proven 2026-10-13).
- The workbook-section endpoint is get-or-create: every
  `GET /units/{uid}/workbook-section` returns the same section object
  (`{id, title: null, position, unit_id}`); it never shows in
  `/units/{id}/sections`.
- Exercises moved into the bucket (`move-to-section`) keep their ids and leave
  the regular section; after `homework prepare` they no longer appear in
  `…/homework/lesson-exercises` (the Lesson group) nor in the unit's section
  list.
- Copies cannot be deleted from the hidden section directly; unassign via
  `remove`/`clear` (source ids) is the supported path.
- A student has read access to the hw section by id — `homework show` relies
  on that, but don't treat the section id as a secrecy boundary.
