"""LangListo API layer — thin wrappers over the reverse-engineered endpoints.

Every method runs with YOUR token against app.langlisto.com; nothing is shared
between users. All operations are additive except explicit deletes.
"""

from .client import LanglistoClient, post_public
from .uploads import upload_file


def login_public(email, password):
    """Log in without any cached session; returns (token, user)."""
    d = post_public("/auth/login", {"email": email, "password": password})
    return d["access_token"], d.get("user")


class Langlisto:
    """High-level LangListo API."""

    def __init__(self, client=None):
        self.c = client or LanglistoClient()

    # ---------- account ----------
    def me(self):
        return self.c.get("/auth/me")

    # ---------- students ----------
    def students_all(self):
        return self.c.get("/students/all")["items"]

    def student_create(self, email, name=None, lang="ru"):
        """Creates the student account (invitation flow — works for new emails).

        The account exists immediately; an invitation email is dispatched
        separately (delivery is not required for the account to work).
        """
        body = {"email": email, "lang": lang}
        if name:
            body["name"] = name.strip()
        return self.c.post("/students/invite", body)

    def student_attach(self, email, name=None):
        """Attach an EXISTING platform user as a student (no invite email)."""
        body = {"email": email}
        if name:
            body["name"] = name.strip()
        return self.c.post("/students", body)

    def student_get(self, sid):
        return self.c.get(f"/students/{sid}")

    def student_login_link(self, sid):
        return self.c.post(f"/students/{sid}/login-link")

    def student_reset_password(self, sid):
        """Returns {'login':..., 'password':...} for handing to the student."""
        return self.c.post(f"/students/{sid}/reset-password")

    def student_patch(self, sid, **fields):
        return self.c.patch(f"/students/{sid}", fields)

    # ---- student-side auth (their own token) ----
    def student_login(self, email, password):
        """Sign in as a student; returns (token, user)."""
        d = post_public("/auth/login", {"email": email, "password": password})
        return d["access_token"], d.get("user")

    def login(self, email, password):
        """Teacher/any-account login; returns (token, user)."""
        d = post_public("/auth/login", {"email": email, "password": password})
        return d["access_token"], d.get("user")

    def student_me(self, token):
        return LanglistoClient(token=token).get("/auth/me")

    def student_set_timezone(self, email, password, tz):
        """Set the student's own profile timezone (their token)."""
        tok, _ = self.student_login(email, password)
        return LanglistoClient(token=tok).patch("/auth/me/timezone", {"timezone": tz})

    # ---------- classrooms ----------
    def classrooms_all(self, params=None):
        return self.c.get("/classrooms", params=params)

    def classrooms_page(self, page=1, page_size=50, **kw):
        return self.c.get(
            "/classrooms/page", params={"page": page, "page_size": page_size, **kw}
        )

    def classroom_create(self, name, kind, language="en", dialect=None,
                         cover_url=None, folder_id=None, teacher_id=None):
        return self.c.post("/classrooms", {
            "name": name,
            "kind": kind,
            "language": language,
            "dialect": dialect,
            "cover_url": cover_url,
            "folder_id": folder_id,
            "teacher_id": teacher_id or None,
        })

    def classroom_get(self, cid):
        return self.c.get(f"/classrooms/{cid}")

    def classroom_students(self, cid):
        return self.c.get(f"/classrooms/{cid}/students")

    def classroom_add_student(self, cid, email, name=None):
        body = {"email": email}
        if name:
            body["name"] = name.strip()
        return self.c.post(f"/classrooms/{cid}/students", body)

    # ---------- lessons / schedule ----------
    def lessons_upcoming(self, from_iso, to_iso, owned_only=True, include_cancelled=False):
        params = {"from": from_iso, "to": to_iso}
        if owned_only:
            params["owned_only"] = "true"
        if include_cancelled:
            params["include_cancelled"] = "true"
        return self.c.get("/lessons/upcoming", params=params)

    def classroom_lessons(self, cid, from_iso, to_iso):
        return self.c.get(f"/classrooms/{cid}/lessons", params={"from": from_iso, "to": to_iso})

    def lesson_conflicts(self, cid, start_at_iso, duration_minutes=60, recurrence=None,
                         lesson_id=None, scope="single"):
        body = {
            "classroom_id": cid,
            "lesson_id": lesson_id,
            "scope": scope,
            "start_at": start_at_iso,
            "duration_minutes": duration_minutes,
            "recurrence": recurrence,
        }
        return self.c.post("/lesson-conflicts", body)

    def schedule_lesson(self, cid, start_at_iso, duration_minutes=60, recurrence=None,
                        check_conflicts=True):
        payload = {
            "start_at": start_at_iso,
            "duration_minutes": duration_minutes,
            "status": "scheduled",
            "recurrence": recurrence,
        }
        conflicts = None
        if check_conflicts:
            try:
                conflicts = self.lesson_conflicts(cid, start_at_iso, duration_minutes)
            except Exception:
                conflicts = None
        lesson = self.c.post(f"/classrooms/{cid}/lessons", payload)
        return {"lesson": lesson, "conflicts": conflicts}

    def lesson_delete(self, lesson_id, scope=None):
        params = {"scope": scope} if scope else None
        return self.c.delete(f"/lessons/{lesson_id}", params=params)

    def lesson_patch(self, lesson_id, start_at_iso=None, duration_minutes=None,
                     status=None, scope=None):
        body = {}
        if start_at_iso is not None:
            body["start_at"] = start_at_iso
        if duration_minutes is not None:
            body["duration_minutes"] = duration_minutes
        if status is not None:
            body["status"] = status
        if scope is not None:
            body["scope"] = scope
        return self.c.patch(f"/lessons/{lesson_id}", body)

    def lesson_series(self, series_id):
        return self.c.get(f"/lesson-series/{series_id}")

    # ---------- units (lessons inside a classroom) ----------
    def units_list(self, cid):
        return self.c.get(f"/classrooms/{cid}/units")

    def unit_create(self, cid):
        return self.c.post(f"/classrooms/{cid}/units")

    def unit_patch(self, uid, title=None, module_id=None, cover_url=None, description=None):
        body = {}
        if title is not None:
            body["title"] = title
        if module_id is not None:
            body["module_id"] = module_id
        if cover_url is not None:
            body["cover_url"] = cover_url
        if description is not None:
            body["description"] = description
        return self.c.patch(f"/units/{uid}", body)

    def unit_access(self, uid, cid, open_=True):
        return self.c.post(f"/units/{uid}/access", {"open": open_, "classroom_id": cid})

    def unit_set_current(self, cid, uid):
        return self.c.put(f"/classrooms/{cid}/current-lesson", {"unit_id": uid})

    # ---------- courses = "books" on the Materials shelf ----------
    def courses_all(self):
        return self.c.get("/courses")

    def course_create(self, title, description=None, language="en", modules_enabled=True,
                      folder_id=None):
        return self.c.post("/courses", {
            "title": title,
            "description": description,
            "language": language,
            "dialect": None,
            "modules_enabled": modules_enabled,
            "folder_id": folder_id,
        })

    def course_get(self, cid):
        return self.c.get(f"/courses/{cid}")

    def course_patch(self, cid, **fields):
        """Accepts: title, description, language, dialect, cover_url,
        modules_enabled, folder_id."""
        return self.c.patch(f"/courses/{cid}", fields)

    def course_lessons(self, cid):
        return self.c.get(f"/courses/{cid}/lessons")

    def course_add_lesson(self, cid):
        """Creates an empty lesson (a "unit") in a course; returns it."""
        return self.c.post(f"/courses/{cid}/lessons")

    def unit_rename(self, uid, title):
        return self.c.patch(f"/units/{uid}", {"title": title})

    # ---------- sections & exercises (material content) ----------
    def unit_sections(self, uid, classroom_id=None):
        params = {"classroom_id": classroom_id} if classroom_id else None
        return self.c.get(f"/units/{uid}/sections", params=params)

    def section_create(self, uid):
        return self.c.post(f"/units/{uid}/sections")

    def section_patch(self, sid, title=None, **fields):
        body = dict(fields)
        if title is not None:
            body["title"] = title
        return self.c.patch(f"/sections/{sid}", body)

    def section_exercises(self, sid):
        return self.c.get(f"/sections/{sid}/exercises")

    def exercise_create(self, sid, etype):
        return self.c.post(f"/sections/{sid}/exercises", {
            "type": etype,
            "hidden": False,
            "before_position": None,
            "attempt_policy": None,
            "remember_attempt_policy": False,
        })

    def exercise_get(self, eid):
        return self.c.get(f"/exercises/{eid}")

    def exercise_patch(self, eid, suffix, body):
        return self.c.patch(f"/exercises/{eid}/{suffix.lstrip('/')}", body)

    def exercise_delete(self, eid):
        return self.c.delete(f"/exercises/{eid}")

    def exercise_teacher_notes(self, eid, text, visible_to_student=False):
        return self.c.put(f"/exercises/{eid}/teacher-notes", {
            "notes": [{
                "text": text,
                "visible_to_student": visible_to_student,
                "kind": "text",
                "audio_url": "",
                "image_url": "",
            }]
        })

    # ---------- folders ----------
    # scope: "material" (the Materials shelf) or "classroom". POST with
    # parent_id=null fails validation — omit it instead.
    def folders(self, scope="material"):
        return self.c.get("/folders", params={"scope": scope})

    def folder_create(self, title, scope="material", color=None, parent_id=None):
        body = {"scope": scope, "title": title, "color": color or "neutral"}
        if parent_id:
            body["parent_id"] = parent_id
        return self.c.post("/folders", body)

    def folder_move_items(self, item_ids, folder_id, scope="material"):
        return self.c.post("/folders/items", {
            "scope": scope, "item_ids": list(item_ids), "folder_id": folder_id,
        })

    # ---------- courses: modules & ordering ----------
    def course_modules(self, cid):
        return self.c.get(f"/courses/{cid}/modules")

    def module_create(self, cid, title=""):
        """Create a module; with a title, the empty default is renamed."""
        m = self.c.post(f"/courses/{cid}/modules", {"title": ""})
        if title and (m.get("title") or "") != title:
            m = self.module_patch(m["id"], title)
        return m

    def module_patch(self, mid, title):
        return self.c.patch(f"/course-modules/{mid}", {"title": title})

    def modules_order(self, cid, module_ids):
        return self.c.put(f"/courses/{cid}/modules/order",
                          {"module_ids": [str(x) for x in module_ids]})

    def course_lessons_order(self, cid, entries):
        """entries: [{'id': lesson_id, 'module_id': mid|None}, …] in book order."""
        clean = [{"id": e["id"], "module_id": e.get("module_id")} for e in entries]
        return self.c.put(f"/courses/{cid}/lessons/order", {"lessons": clean})

    def unit_delete(self, uid):
        """DELETE a lesson unit — used for starter-lesson cleanup."""
        return self.c.delete(f"/units/{uid}")

    # ---------- section/exercise ordering ----------
    def section_exercises_order(self, exercise_ids, classroom_id=None):
        """Reorder exercises inside a section; position 0 = first (banner slot)."""
        return self.c.post("/sections/exercises/order", {
            "exercise_ids": list(exercise_ids), "classroom_id": classroom_id})

    # ---------- grading trial & demo preview ----------
    def exercise_trial_check(self, eid, answer, history=None):
        """Grade an answer WITHOUT saving it as a student attempt (trial-check).

        answer: list of answer objects, e.g. [{'question_idx':0,'option_idx':1}]
        for test types. The response carries `attempts[].attempts[].correct`."""
        return self.c.post(f"/exercises/{eid}/trial-check",
                           {"answer": answer, "history": history or []})

    def demo_student_open(self, next_path=None):
        """Open the platform's built-in demo student (demoview.langlisto.com).

        Returns {'url': …} — a magic-link single-use URL. Never store it."""
        return self.c.post("/demo-student/open", {"next": next_path})

    def demo_student_status(self):
        return self.c.get("/demo-student")

    # ---------- classroom membership ----------
    def classroom_remove_student(self, cid, sid):
        return self.c.delete(f"/classrooms/{cid}/students/{sid}")

    # ---------- uploads ----------
    def upload(self, filepath, kind="auto"):
        return upload_file(self.c, filepath, kind=kind)

    # ---------- homework (hidden per-unit homework section) ----------
    # Homework is keyed by the PAIR (classroom_id, unit_id): always pass
    # classroom_id — on a material unit the management endpoints answer
    # 400 homework_not_classroom_unit without it. All operations address
    # SOURCE exercise ids (the ids of the unit's own exercises or of the
    # source material); the hidden homework section then holds fresh COPIES
    # with new ids. choose is idempotent: re-choosing an assigned exercise
    # is a 204 no-op.
    def unit_homework_section(self, uid, classroom_id=None):
        """GET /units/{uid}/homework → {'section_id': …} (the hidden section)."""
        params = {"classroom_id": classroom_id} if classroom_id else None
        return self.c.get(f"/units/{uid}/homework", params=params)

    def unit_homework_lesson_exercises(self, uid, classroom_id=None):
        """The «Lesson» group of the picker (a unit's own exercises)."""
        params = {"classroom_id": classroom_id} if classroom_id else None
        return self.c.get(f"/units/{uid}/homework/lesson-exercises", params=params)

    def unit_homework_workbook_exercises(self, uid, classroom_id=None):
        """The «Exercises for homework» group (observed empty so far)."""
        params = {"classroom_id": classroom_id} if classroom_id else None
        return self.c.get(f"/units/{uid}/homework/workbook-exercises", params=params)

    def unit_homework_assigned(self, uid, classroom_id):
        """200 [source_exercise_id, …] — what is currently set for homework."""
        return self.c.get(f"/units/{uid}/homework/assigned-sources",
                          params={"classroom_id": classroom_id})

    def homework_choose(self, uid, classroom_id, exercise_ids):
        """Assign → copies the exercises into the unit's hidden hw section."""
        return self.c.post(f"/units/{uid}/homework/choose",
                           {"exercise_ids": [str(x) for x in exercise_ids]},
                           params={"classroom_id": classroom_id})

    def homework_unassign(self, uid, classroom_id, exercise_ids):
        """Bulk-unassign (204)."""
        return self.c.post(f"/units/{uid}/homework/unassign",
                           {"exercise_ids": [str(x) for x in exercise_ids]},
                           params={"classroom_id": classroom_id})

    def homework_remove(self, uid, classroom_id, source_exercise_id):
        """Unassign ONE exercise by its SOURCE id (DELETE → 204)."""
        return self.c.delete(f"/units/{uid}/homework/assigned/{source_exercise_id}",
                             params={"classroom_id": classroom_id})

    def homework_submissions(self, uid, classroom_id):
        """Teacher view: one record per student of the class."""
        return self.c.get(f"/units/{uid}/homework/submissions",
                          params={"classroom_id": classroom_id})

    def homework_complete(self, uid, classroom_id, student_id):
        """Accept a submission (teacher) → status 'completed'."""
        return self.c.post(f"/units/{uid}/homework/students/{student_id}/complete",
                           params={"classroom_id": classroom_id})

    def homework_revision(self, uid, classroom_id, student_id, comment=""):
        """Send back for revision (teacher) → status 'revision' + comment."""
        return self.c.post(f"/units/{uid}/homework/students/{student_id}/revision",
                           {"comment": comment},
                           params={"classroom_id": classroom_id})

    def classroom_homeworks(self, cid, student_id=None):
        """Class's homework units: [{unit_id, has_exercises, status}].

        Without student_id the platform reports status: null; pass a student
        id for their personal statuses."""
        params = {"student_id": student_id} if student_id else None
        return self.c.get(f"/classrooms/{cid}/homeworks", params=params)

    # ---------- section order & delete ----------
    def unit_sections_order(self, uid, section_ids):
        """PUT /units/{uid}/sections/order {section_ids} → 204 (order = array order)."""
        return self.c.put(f"/units/{uid}/sections/order",
                          {"section_ids": [str(x) for x in section_ids]})

    def section_delete(self, sid):
        """DELETE /sections/{sid} → 204 (also drops its exercises & answers)."""
        return self.c.delete(f"/sections/{sid}")
