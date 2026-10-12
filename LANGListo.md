# LangListo API — working reference (reverse-engineered, Oct 2026)

Base: `https://app.langlisto.com/api/v1` (override: `LANGLISTO_API`).
Auth: `Authorization: Bearer <JWT>` — token obtained once via
`POST /auth/login {email,password}` → `{access_token, user}`; TTL ≈1000 days;
cached by the CLI at `~/.langlisto/session.json` (mode 600).
The SPA stores it in localStorage under `langlisto.access_token`.

Browser-like `User-Agent`/`Origin`/`Referer` headers are sent (the API sits
behind DataDome; plain HTTP works fine once authenticated).

## Endpoint map

| Area | Endpoints |
|---|---|
| account | `GET /auth/me`; `PATCH /auth/me/timezone {timezone}`; `PATCH /auth/me/name`; `POST /auth/login {email,password}`; `POST /auth/logout` |
| students | `GET /students/all`; `POST /students/invite {email,lang,name}` (creates NEW accounts); `POST /students {email,name}` (attaches an EXISTING user); `POST /students/{id}/login-link`; `POST /students/{id}/reset-password` → `{login,password}`; `PATCH /students/{id}`; `/students/{id}/settings` (not available on all plans) |
| classrooms | `GET /classrooms`; `GET /classrooms/page?page=1&page_size=N`; `POST /classrooms {name,kind,language,cover_url?...}`; `GET /classrooms/{id}/students`; `POST /classrooms/{id}/students {email,name?}` |
| schedule | `POST /classrooms/{id}/lessons {start_at(UTC Z),duration_minutes,status,recurrence}`; `GET /classrooms/{id}/lessons?from&to`; `GET /lessons/upcoming?from&to`; `POST /lesson-conflicts`; `PATCH /lessons/{id}`; `DELETE /lessons/{id}?scope=single|series`; `GET /lesson-series/{id}` |
| units (classroom lessons) | `GET/POST /classrooms/{id}/units`; `PATCH /units/{id} {title,cover_url?,module_id?}`; `POST /units/{id}/access {open,classroom_id}`; `PUT /classrooms/{id}/current-lesson {unit_id}` |
| courses = books | `GET /courses`; `POST /courses {title,description,language,modules_enabled,folder_id?}`; `GET /courses/{id}`; `PATCH /courses/{id} {title,description,cover_url,...}`; `GET/POST /courses/{id}/lessons` (POST creates a new lesson/unit); `DELETE /courses/{id}` |
| folders | `GET /folders?scope=material` (scopes: `material` for the shelf, `classroom`); `POST /folders {scope,title,color}` — do NOT send `parent_id: null` (422); `PATCH /folders/{id}`; `DELETE /folders/{id}?scope=`; `POST /folders/items {scope,item_ids,folder_id}` (move courses/boards into a folder); `POST /folders/reorder` |
| materials content | `GET/POST /units/{id}/sections`; `PATCH /sections/{id} {title}`; `GET/POST /sections/{id}/exercises`; `GET /exercises/{id}`; per-type `PATCH /exercises/{id}/<suffix>`; `DELETE /exercises/{id}` |
| uploads | multipart `POST /uploads/image` (pictures), `POST /uploads/media` (audio/video), field name `file` → `{url}`; also `/uploads/pdf`, `/uploads/direct`, `/uploads/media/chunked` |
| misc | `/config`; `/notifications`; `/shared-materials/{token}`; `/billing/*`; `/move/jobs` ("paste links" importer) |

`kind` ∈ `individual | group | marathon`. Recurrence payload:
`{weekdays:[1..7], interval_weeks, end_kind, until_date|occurrence_count}`.

## Exercise types & their PATCH suffixes

| type (create) | PATCH suffix | body core |
|---|---|---|
| `note` | `/note` | `{heading, text, color}` |
| `article` / `text` / `essay` | `/text-block` | `{heading, subheading, image_url, body}` |
| `drag_word_gap` / `enter_word_gap` / `select_word_gap` | `/drag-word-gap` | `{heading, text (with `\uFFFC` at gaps), gaps:[{idx, answers, no_correct, hint, correct_indices}]}` |
| `match_pairs` | `/match-pairs` | `{heading, pairs:[{left, right, audio_url}]}` |
| `untimed_test` / `timed_test` | `/test` | `{heading, time_limit_seconds, questions:[{prompt, options:[{text,is_correct}]}]}` |
| `true_false` | `/true-false` | `{heading, not_stated_enabled, statements:[{text,answer}]}` |
| `wordlist` | `/wordlist` | word entries |
| `voice_recording` | `/voice-recording` | `{heading, description, time_limit_seconds}` |
| `image_stack` | `/image-block` | `{heading, document_url, document_name, items:[{image_url, description}]}` |
| `video` / `audio` | `/media-clip` | `{heading, description, media_url, clips:[{media_url,title,script,keep_source_player}]}` — clips must be non-empty for the URL to persist |
| `sentence_order` | `/ordering` | items list |
| `sort_columns` | `/word-columns` | `{heading, no_correct, columns:[{title}], words:[{text,column_idx}]}` |
| `word-columns` / `word-image` / `photo-answer` / `interactive-video` / `diagram` / `link` / `ordering` | same-named suffixes | see app |

Teacher notes: `PUT /exercises/{id}/teacher-notes {notes:[{text,visible_to_student,kind:'text',audio_url:'',image_url:''}]}`.

## Platform quirks (verified)

- Folders take a `scope` of exactly `material` or `classroom` (other values →
  422); on create, omit `parent_id` entirely rather than sending `null`.
- Word-bank placements (`/drag-word-gap/placements`) reject on materials:
  `answer_scope` — "Answers only exist inside a classroom lesson." Use typed
  gaps there; the dropdown flavor becomes configurable only inside a class.
- New courses auto-create one empty starter lesson (like classrooms get a
  starter unit) — leave it or rename it.
- `PATCH /courses/{id}` accepts `cover_url` — set it to an uploaded image URL
  to render the course as a book card.
- Student-side: `PATCH /auth/me/timezone` works with the student's own token.
