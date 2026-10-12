# Homework — the classroom room's «Homework» tab

Verified live against the platform (recon `RECON2-homework.md` + CLI wave W2b,
2026-10-12/13). Homework is the **class layer** twin of a book lesson's visible
«Homework» section: same content, plus per-student status, submit and review.

## The four facts that matter

1. **Keyed pair `(classroom_id, unit_id)`.** Every homework endpoint is scoped
   by the classroom. On a material unit (one with no classroom of its own) the
   management endpoints answer `400 homework_not_classroom_unit` unless
   `classroom_id` is passed. The CLI ALWAYS sends it — never call these
   endpoints bare.
2. **Every unit has a hidden homework section.** `GET /units/{id}/homework`
   → `{section_id}`; it never appears in `/units/{id}/sections`.
   `homework show` is the only read that shows its content — i.e. exactly what
   the student sees in the room's Homework tab.
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
CID=<classroom id>   UID=<classroom unit id>

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
GET    /units/{uid}/homework?classroom_id={cid}                    → {section_id}
GET    /units/{uid}/homework/lesson-exercises?classroom_id={cid}   → [{id,type,label,heading}]
GET    /units/{uid}/homework/workbook-exercises?classroom_id={cid} → observed [] always
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

## Rules for book building (BGP / Speaking / any book)

- **The visible «Homework» section inside a book lesson stays canonical** —
  it is part of the lesson the student walks through; never remove or rewrite
  it when adding the class layer.
- The class layer is ADDITIVE for tracking: open the lesson in the class,
  then mirror the section's exercises into the room's homework tab:

```bash
cli-langlisto homework choose --class $CID --unit $CLASSROOM_UNIT \
    --from-material $BOOK_UNIT --exercise <ids from the Homework section>
```

  (`--from-material` = the picker's «Choose from other materials»; the copy
  then lives in the class. Target must be the **classroom unit** — never
  assign homework directly onto a material unit: the UI doesn't, and such an
  assignment only surfaces if that unit is in the class's unit list.)
- New books can mark the section in the spec (`"homework": true` is the
  planned signal) so publish-time tooling can run the `choose` automatically.

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
- `workbook-exercises` («Exercises for homework» picker group) has returned
  `[]` in every observation — its semantics remain unproven.
- Copies cannot be deleted from the hidden section directly; unassign via
  `remove`/`clear` (source ids) is the supported path.
- A student has read access to the hw section by id — `homework show` relies
  on that, but don't treat the section id as a secrecy boundary.
