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
    def unit_sections(self, uid):
        return self.c.get(f"/units/{uid}/sections")

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

    # ---------- uploads ----------
    def upload(self, filepath, kind="auto"):
        return upload_file(self.c, filepath, kind=kind)
