"""Name → placeholder email helpers (RU→EN transliteration).

Used when a teacher wants to create a student account before knowing the
student's real address. The placeholder domain is chosen by the user:

    langlisto students add --name "Надя" --domain mydomain.com
    # or export LANGLISTO_EMAIL_DOMAIN=mydomain.com

The address is a placeholder only — it can be changed later
(`PATCH /students/{id}`), and the platform never requires email delivery for
the account to work (a sign-in link / password is handed to the student).
"""

import os

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    " ": "-", "'": "",
}


def translit(s):
    return "".join(_TRANSLIT.get(ch, ch) for ch in s.lower().strip())


def default_domain():
    return os.environ.get("LANGLISTO_EMAIL_DOMAIN") or None


def email_for(name, domain=None):
    domain = domain or default_domain()
    if not domain:
        raise ValueError(
            "no placeholder email domain set — pass --email, or --domain, "
            "or set LANGLISTO_EMAIL_DOMAIN"
        )
    return f"{translit(name)}@{domain}"
