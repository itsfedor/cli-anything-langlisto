"""File uploads to LangListo's own storage (used for covers, course images and
lesson media such as audio).

Endpoint: multipart POST /uploads/image (pictures) or /uploads/media (audio,
video) with a single form field named ``file`` → ``{"url": "https://..."}``.
Files land on the platform's CDN; the returned URL is what the app itself
stores in exercises / course covers.
"""

import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid

from .client import LanglistoClient, LanglistoError


def _guess_type(filename):
    ctype = mimetypes.guess_type(filename)[0]
    if ctype:
        return ctype
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def upload_file(client, filepath, kind="auto"):
    """Upload a local file; returns its public URL.

    kind: "auto" (image/* → /uploads/image, everything else → /uploads/media),
    or force "image" / "media".
    """
    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        raise LanglistoError(0, "file_not_found", f"no such file: {filepath}")
    name = os.path.basename(filepath)
    ctype = _guess_type(name)
    if kind == "auto":
        kind = "image" if ctype.startswith("image/") else "media"
    endpoint = "/uploads/image" if kind == "image" else "/uploads/media"

    boundary = "----langlisto" + uuid.uuid4().hex
    with open(filepath, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        client.BASE + endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {client.token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": client.UA,
            "Origin": client.ORIGIN,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            import json as _json

            payload = _json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise LanglistoError(e.code, "upload_failed", raw[:300]) from None
    url = payload.get("url") or (payload.get("data") or {}).get("url")
    if not url:
        raise LanglistoError(0, "upload_failed", f"unexpected upload response: {payload!r:.200}")
    return url


def upload_pdf(client, filepath):
    """Upload a PDF to ``/uploads/pdf`` → ``{document_url, document_name, pages}``.

    The server renders the PDF pages to jpg itself (verified 2026-10-12);
    ``pages`` is a list of page-image URLs.  NOTE: authoring a ``pdf_document``
    exercise on a material still lands as a note stub server-side (recon:
    ``pdf_document`` = partial), so the returned url is meant for classroom
    flows / future use — the material builder does not build PDF items.
    """
    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        raise LanglistoError(0, "file_not_found", f"no such file: {filepath}")
    name = os.path.basename(filepath)
    boundary = "----langlisto" + uuid.uuid4().hex
    with open(filepath, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        client.BASE + "/uploads/pdf",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {client.token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": client.UA,
            "Origin": client.ORIGIN,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            import json as _json

            payload = _json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise LanglistoError(e.code, "upload_failed", raw[:300]) from None
    payload = payload.get("data") or payload
    if not payload.get("document_url"):
        raise LanglistoError(0, "upload_failed", f"unexpected pdf upload response: {payload!r:.200}")
    return payload
