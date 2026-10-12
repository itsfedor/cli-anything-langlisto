"""Duplicate guard (quality feature, H8) — two independent mechanisms:

1. :class:`BuildLock` — a lockfile next to a spec's state file.  Two builders
   writing one spec/state (an orphaned run + a relaunch, the classic source of
   duplicated sections; finding F1) are detected before any write: the second
   builder refuses to start while the holder's PID is alive, and stale locks
   (dead PID) are taken over automatically.
2. :func:`find_duplicates` — a normalized-text hash detector.  The builder
   runs it over the spec (and records item hashes in the state); the verify
   side can import it and run it over live exercise payloads with the same
   normalization, so build and verification agree on what "duplicate" means.

Pure and offline: no network, no global state.
"""

import hashlib
import json
import os
import re
import time

# keys whose values are identifiers/media, never content — excluded from the
# duplicate fingerprint (two copies still match; two different media do not
# become false duplicates).
_SKIP_KEYS = {
    "id", "exercise_id", "section_id", "lesson_id", "unit_id", "course_id",
    "classroom_id", "image", "image_url", "audio_url", "media_url", "video_url",
    "document_url", "url", "cover_url", "state", "version", "position",
}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_FFFC = "\ufffc"


def normalize_text(text):
    """Canonical fingerprint text: tags/entities/punctuation/ws stripped."""
    text = str(text or "")
    text = _TAG_RE.sub(" ", text)
    text = text.replace(_FFFC, " ")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return _WS_RE.sub(" ", text).strip()


def text_hash(text):
    """Stable short hash of the normalized text (empty string -> '')."""
    norm = normalize_text(text)
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def is_meaningful(text, min_len=12):
    return len(normalize_text(text)) >= min_len


def extract_text(obj):
    """Recursively collect content strings from a spec item or live payload."""
    parts = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in _SKIP_KEYS:
                    continue
                if isinstance(value, (dict, list)):
                    walk(value)
                elif isinstance(value, str) and value.strip():
                    parts.append(value)
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    walk(obj)
    return " ".join(parts)


def entries_from_spec(items):
    """[{id, text}] for a list of spec items (for find_duplicates)."""
    return [{"id": it.get("id") or f"item-{i}", "text": extract_text(it)}
            for i, it in enumerate(items or [])]


def entries_from_live(exercises):
    """[{id, text}] for live API exercise payloads (verify-side use)."""
    return [{"id": ex.get("id") or f"ex-{i}", "text": extract_text(ex)}
            for i, ex in enumerate(exercises or [])]


def find_duplicates(entries, min_len=12):
    """Group entries by normalized-text hash.

    Returns a list of ``{"hash": ..., "ids": [...], "sample": ...}`` for every
    group with ≥2 members whose normalized text is at least ``min_len`` chars
    (short strings like "True" would collide harmlessly otherwise).
    """
    groups = {}
    for e in entries or []:
        text = e.get("text") or ""
        if not is_meaningful(text, min_len=min_len):
            continue
        h = text_hash(text)
        groups.setdefault(h, []).append(e.get("id"))
    dups = [{"hash": h, "ids": ids, "sample": ""}
            for h, ids in groups.items() if len(ids) > 1]
    dups.sort(key=lambda d: -len(d["ids"]))
    return dups


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class BuildLock:
    """A PID lockfile guarding one spec/state from concurrent builders."""

    def __init__(self, path):
        self.path = path
        self.held = False

    def acquire(self, owner=""):
        if os.path.exists(self.path):
            holder = {}
            try:
                with open(self.path, encoding="utf-8") as f:
                    holder = json.load(f)
            except Exception:
                holder = {}
            pid = holder.get("pid")
            if pid and _pid_alive(pid):
                raise BuildLockedError(
                    f"another builder is writing this spec (pid {pid}, "
                    f"started {holder.get('started_at')}). "
                    f"If that run is dead, delete {self.path} and retry."
                )
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S%z"), "owner": owner}, f)
        self.held = True
        return self

    def release(self):
        if not self.held:
            return
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        finally:
            self.held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class BuildLockedError(RuntimeError):
    """Raised when a live builder already holds the lock for a spec."""
