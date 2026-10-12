"""LangListo core client — authenticated JSON API over urllib (stdlib only).

Auth model
----------
* ``cli-anything-langlisto login`` posts email+password to /auth/login ONCE,
  caches only the returned bearer token in ``~/.langlisto/session.json``
  (mode 600) and never stores the password.
* Every other command reads the token from (first found wins):
  1. ``$LANGLISTO_TOKEN``
  2. ``$LANGLISTO_SESSION`` (or ``~/.langlisto/session.json``)
  3. legacy ``~/.config/langlisto/token``
"""

import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

SESSION_FILE = os.environ.get("LANGLISTO_SESSION") or os.path.expanduser(
    "~/.langlisto/session.json"
)
LEGACY_TOKEN_FILES = [os.path.expanduser("~/.config/langlisto/token")]


class LanglistoError(RuntimeError):
    def __init__(self, status, code, message, details=None):
        super().__init__(f"[{status}] {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def save_session(token, email=None, path=None):
    path = path or SESSION_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "email": email}, f)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600


def load_token():
    tok = os.environ.get("LANGLISTO_TOKEN")
    if tok:
        return tok.strip()
    if os.path.exists(SESSION_FILE):
        try:
            d = json.load(open(SESSION_FILE, encoding="utf-8"))
            if d.get("access_token"):
                return d["access_token"]
        except Exception:
            pass
    for p in LEGACY_TOKEN_FILES:
        if os.path.exists(p):
            t = open(p, encoding="utf-8").read().strip()
            if t:
                return t
    raise LanglistoError(
        0,
        "no_token",
        "No session found. Run `cli-anything-langlisto login` first "
        "(or set LANGLISTO_TOKEN / LANGLISTO_SESSION).",
    )


class LanglistoClient:
    BASE = os.environ.get("LANGLISTO_API", "https://app.langlisto.com/api/v1")
    UA = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0 Safari/537.36"
    )
    ORIGIN = "https://app.langlisto.com"

    def __init__(self, token=None, timeout=30):
        self.token = token or load_token()
        self.timeout = timeout

    def request(self, method, path, json_body=None, params=None, _attempts=3):
        url = self.BASE + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": self.UA,
            "Origin": self.ORIGIN,
            "Referer": self.ORIGIN + "/my-classes",
        }
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        last_err = None
        for attempt in range(1, _attempts + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode("utf-8", "replace")
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {}
                err = body.get("error") or {}
                raise LanglistoError(
                    e.code,
                    err.get("code", "http_error"),
                    err.get("message", raw[:300] or e.reason),
                    err.get("details"),
                ) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt < _attempts:
                    time.sleep(1.5 * attempt)
        raise LanglistoError(
            0,
            "network_error",
            f"{method} {path}: network error after {_attempts} attempts ({last_err})",
        )

    # ---- convenience ----
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


def post_public(path, json_body, timeout=30):
    """POST without auth (login etc.). Returns parsed JSON or raises LanglistoError."""
    req = urllib.request.Request(
        LanglistoClient.BASE + path,
        data=json.dumps(json_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": LanglistoClient.UA,
            "Origin": LanglistoClient.ORIGIN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        err = body.get("error") or {}
        raise LanglistoError(
            e.code, err.get("code", "http_error"), err.get("message", raw[:300])
        ) from None
