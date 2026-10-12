"""Spec linter — codifies the VERIFIED LangListo render model + quality rules.

The render model was verified live (2026-10-12, BGP/Speaking migrations):

* survives: ``<b> <em> <i> <u> <s> <br> <p> <table>``;
* STRIPPED (lossy): colors/bg, ``<img>`` inside text, ``<ul>/<ol>`` (items
  glue together), ``<h1>-<h6>`` (tag dropped, text kept);
* the container is ``pre-line``, so raw ``\\n`` in a body renders as a stray
  break — use ``<br>``;
* gap texts use U+FFFC placeholders; spec markers ``[[n]]`` must all be
  consumed by the builder.

The linter runs before any write (``MaterialBuilder``, flag-controlled abort
via ``lint_fail_on``) and is importable by the verify side so build-time and
verification agree on one rule set.

Findings are plain dicts: ``{"code","severity","item","field","message"}``;
severity: ``error`` (lossy/broken on the platform), ``warn`` (quality or
render degradation), ``info`` (advisory).
"""

import re

from .dedup import entries_from_spec, find_duplicates
from .variants import expand_answer_variants

SEVERITIES = ("error", "warn", "info")

# ---------- verified render model ----------
LOSSY_TAG_RE = re.compile(r"<\s*(ul|ol|li)\b[^>]*>", re.I)
HEADING_TAG_RE = re.compile(r"<\s*h[1-6]\b[^>]*>", re.I)
IMG_TAG_RE = re.compile(r"<\s*img\b[^>]*>", re.I)
STYLE_COLOR_RE = re.compile(r"style\s*=\s*[\"'][^\"']*(color|background)[^\"']*[\"']", re.I)
FONT_COLOR_RE = re.compile(r"<\s*font\b[^>]*color\s*=", re.I)
NBSP_RE = re.compile(r"&nbsp;", re.I)
MARKER_RE = re.compile(r"\[\[\s*(\d+)\s*\]\]")

# conservative "the exercise talks about a word bank" phrases.  Deliberately
# NOT a bare \bbox\b — «￼ box do you prefer» is a false friend (finding trap)
BOX_PHRASE_RE = re.compile(
    r"\b(word\s?bank|from the box|in the box|into the box|use the box|"
    r"correct box|right box|box below|boxes below)\b", re.I)

# v2 spec vocabulary (v1 aliases + the types verified in type_catalog.json)
V2_TYPES = {
    # v1 vocabulary
    "info", "open", "voice", "true_false", "test", "match", "gap_text",
    "short_answer", "choice", "multi_choice",
    # v2 additions (schemas live-verified)
    "sort_columns", "sentence_order", "text_order", "unscramble",
    "select_word_gap", "enter_word_image", "drag_word_image",
    "select_word_image", "image_stack", "image_carousel", "gif_animation",
    "diagram", "interactive_video", "audio_match_pairs", "wordlist",
    "link", "photo_answer", "divider", "timed_test",
}
GAP_ITEM_TYPES = {"gap_text", "short_answer", "select_word_gap"}
IMAGE_ITEM_TYPES = {"image_stack", "image_carousel", "gif_animation"}
WORD_IMAGE_TYPES = {"enter_word_image", "drag_word_image", "select_word_image"}


def _finding(code, severity, item, field, message):
    return {"code": code, "severity": severity, "item": item,
            "field": field, "message": message}


def _walk_strings(obj, path=""):
    """Yield (path, value) for every string leaf of the item."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk_strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _walk_strings(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _gap_source(item):
    gsrc = item.get("gaps")
    if isinstance(gsrc, dict):
        return [(int(k), gsrc[k]) for k in sorted(gsrc, key=lambda x: int(x))]
    if isinstance(gsrc, list):
        return list(enumerate(gsrc, start=1))
    return []


def _render_checks(item, item_id, findings):
    """The verified-lossy markup rules (same codes as qa_check.py)."""
    for field, value in _walk_strings(item):
        if field in ("id", "type"):
            continue
        if "\n" in value or "\r" in value:
            findings.append(_finding(
                "raw_newline", "error", item_id, field,
                r"raw \n in an HTML field (container is pre-line) — use <br>"))
        if LOSSY_TAG_RE.search(value):
            findings.append(_finding(
                "ul_ol_stripped", "error", item_id, field,
                "list tag is stripped (items glue) — use '• ' lines"))
        if HEADING_TAG_RE.search(value):
            findings.append(_finding(
                "h_tag_dropped", "warn", item_id, field,
                "h1-h6 tag is dropped (text kept) — keep headings unstyled"))
        if IMG_TAG_RE.search(value):
            findings.append(_finding(
                "img_stripped", "error", item_id, field,
                "<img> inside a text field is stripped — use an image item"))
        if STYLE_COLOR_RE.search(value):
            findings.append(_finding(
                "style_stripped", "warn", item_id, field,
                "color/background style is stripped"))
        if FONT_COLOR_RE.search(value):
            findings.append(_finding(
                "font_color_stripped", "warn", item_id, field,
                "<font color> is stripped"))
        if NBSP_RE.search(value):
            findings.append(_finding(
                "nbsp_entity", "info", item_id, field,
                "&nbsp; is a source artifact — use a normal space"))


def _marker_checks(item, item_id, findings):
    prompt = item.get("prompt_html") or ""
    markers = [int(m) for m in MARKER_RE.findall(prompt)]
    if item.get("type") in GAP_ITEM_TYPES and item.get("type") != "short_answer":
        keys = [n for n, _ in _gap_source(item)]
        if sorted(markers) != sorted(keys):
            findings.append(_finding(
                "gap_marker_mismatch", "error", item_id, "prompt_html",
                f"[[n]] markers {sorted(markers)} vs gaps keys {sorted(keys)}"))
    else:
        for m in markers:
            findings.append(_finding(
                "leftover_marker", "error", item_id, "prompt_html",
                f"spec marker [[{m}]] left in a non-gap item"))


def _gap_quality_checks(item, item_id, findings):
    if item.get("type") not in GAP_ITEM_TYPES or item.get("type") == "short_answer":
        return
    item_expand = item.get("expand_variants", True) is not False
    box_options = []
    for n, gap in _gap_source(item):
        if not isinstance(gap, dict):
            continue
        free = bool(gap.get("no_correct"))
        authored = gap.get("answer", gap.get("correct"))
        if isinstance(authored, str):
            authored = [authored]
        authored = [str(a) for a in (authored or []) if str(a).strip()]
        box_options.extend(gap.get("options") or [])
        if not free and not authored:
            findings.append(_finding(
                "gap_no_key", "warn", item_id, f"gaps[{n}]",
                "gap has neither an answer nor no_correct — it builds unanswerable"))
        enabled = item_expand and gap.get("variants", True) is not False
        if len(authored) == 1 and not free:
            final = expand_answer_variants(authored[0], enabled=enabled)
            if enabled and len(final) > 1:
                findings.append(_finding(
                    "gap_single_variant", "info", item_id, f"gaps[{n}]",
                    f"single authored variant '{authored[0]}' — auto-expands to "
                    f"{len(final)}; add an alternate wording if one is acceptable"))
            else:
                findings.append(_finding(
                    "gap_single_variant", "warn", item_id, f"gaps[{n}]",
                    f"single authored variant '{authored[0]}' with expansion "
                    f"disabled — students matching a different valid form are wrong-marked"))
        if free and not (gap.get("sample_answer") or "").strip():
            findings.append(_finding(
                "free_gap_no_sample", "info", item_id, f"gaps[{n}]",
                "free gap without sample_answer — teacher note would be empty"))
    text = (item.get("prompt_html") or "") + " " + (item.get("heading") or "")
    if (not box_options and "<b>Box:</b>" not in text
            and BOX_PHRASE_RE.search(text)):
        findings.append(_finding(
            "box_mentioned_no_options", "warn", item_id, "prompt_html",
            "text mentions a word bank/box but no gap options exist — "
            "no 'Box:' line will be derived"))


def _image_checks(item, item_id, findings):
    t = item.get("type")
    if t == "diagram":
        if not item.get("image"):
            findings.append(_finding(
                "image_missing", "error", item_id, "image", "diagram without image"))
        if not (item.get("description") or "").strip():
            findings.append(_finding(
                "image_missing_caption", "info", item_id, "description",
                "diagram without a description/instruction"))
    if t == "interactive_video" and not (
            item.get("description") or item.get("prompt_html") or "").strip():
        findings.append(_finding(
            "image_missing_caption", "info", item_id, "description",
            "interactive video without a description/instruction"))
    if t in IMAGE_ITEM_TYPES or t == "info":
        entries = item.get("items")
        if isinstance(entries, list) and t in IMAGE_ITEM_TYPES:
            if t == "image_carousel" and len(entries) < 2:
                findings.append(_finding(
                    "carousel_too_few_items", "warn", item_id, "items",
                    "image_carousel needs ≥2 images to be a carousel"))
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                if not (entry.get("image") or entry.get("image_url")):
                    findings.append(_finding(
                        "image_missing", "error", item_id, f"items[{i}].image",
                        "image item without an image"))
                caption = entry.get("caption") or entry.get("description")
                if not (caption or "").strip() and not entry.get("allow_no_caption") \
                        and not item.get("allow_no_caption"):
                    findings.append(_finding(
                        "image_missing_caption", "warn", item_id, f"items[{i}].caption",
                        "image without a caption/description (student sees a bare picture)"))
                if t == "gif_animation":
                    src = str(entry.get("image") or entry.get("image_url") or "")
                    if src.startswith("http") and "." in src.split("/")[-1]:
                        if not src.lower().split("?")[0].endswith(".gif"):
                            findings.append(_finding(
                                "gif_not_gif", "info", item_id, f"items[{i}].image",
                                "gif_animation image does not look like a .gif"))


def _structured_checks(item, item_id, findings):
    t = item.get("type")
    if t == "sort_columns":
        columns = item.get("columns") or []
        if not columns:
            findings.append(_finding("columns_missing", "error", item_id, "columns",
                                     "sort_columns without columns"))
        for i, w in enumerate(item.get("words") or []):
            if not isinstance(w, dict):
                continue
            col = w.get("column", w.get("column_idx"))
            if col is None:
                findings.append(_finding("sort_word_no_column", "error", item_id,
                                         f"words[{i}]", "word without a column"))
            elif not isinstance(col, int) or not (0 <= col < len(columns)):
                findings.append(_finding("sort_column_range", "error", item_id,
                                         f"words[{i}].column",
                                         f"column {col!r} outside 0..{len(columns) - 1}"))
    elif t in ("sentence_order", "text_order"):
        if len(item.get("items") or []) < 2:
            findings.append(_finding("ordering_too_few_items", "warn", item_id,
                                     "items", "ordering item with <2 lines"))
    elif t == "unscramble":
        for i, w in enumerate(item.get("words") or []):
            if not isinstance(w, dict) or len(w.get("tiles") or w.get("items") or []) < 2:
                findings.append(_finding("unscramble_no_tiles", "error", item_id,
                                         f"words[{i}].tiles",
                                         "unscramble word without ≥2 tiles"))
    elif t == "diagram":
        points = item.get("points") or []
        if not points:
            findings.append(_finding("diagram_no_points", "error", item_id,
                                     "points", "diagram without points"))
        for i, c in enumerate(item.get("cards") or []):
            if not isinstance(c, dict):
                continue
            idx = c.get("point_idx")
            if not isinstance(idx, int) or not (0 <= idx < len(points)):
                findings.append(_finding("diagram_point_range", "error", item_id,
                                         f"cards[{i}].point_idx",
                                         f"point_idx {idx!r} outside 0..{len(points) - 1}"))
    elif t == "interactive_video":
        cps = item.get("checkpoints") or []
        if not cps:
            findings.append(_finding("iv_no_checkpoints", "error", item_id,
                                     "checkpoints", "interactive_video without checkpoints"))
        for i, cp in enumerate(cps):
            if not isinstance(cp, dict):
                continue
            if not isinstance(cp.get("time_seconds"), (int, float)):
                findings.append(_finding("iv_time_missing", "error", item_id,
                                         f"checkpoints[{i}].time_seconds",
                                         "checkpoint without numeric time_seconds"))
            opts = cp.get("options") or []
            if not opts or not any(o.get("is_correct") for o in opts
                                   if isinstance(o, dict)):
                findings.append(_finding("iv_no_correct", "error", item_id,
                                         f"checkpoints[{i}].options",
                                         "checkpoint without a correct option"))
    elif t == "wordlist":
        if not item.get("words"):
            findings.append(_finding("wordlist_empty", "error", item_id, "words",
                                     "wordlist without words"))
    elif t == "audio_match_pairs" or t == "match":
        if t == "audio_match_pairs" and not item.get("pairs"):
            findings.append(_finding("pairs_empty", "error", item_id, "pairs",
                                     "audio_match_pairs without pairs"))
    elif t == "link":
        if not (item.get("url") or "").strip():
            findings.append(_finding("link_no_url", "error", item_id, "url",
                                     "link without url"))
    elif t == "photo_answer":
        if not (item.get("description") or item.get("prompt_html") or "").strip():
            findings.append(_finding("photo_answer_no_prompt", "error", item_id,
                                     "description", "photo_answer without a description"))
    elif t == "timed_test":
        limit = item.get("time_limit_seconds")
        if not isinstance(limit, (int, float)) or limit <= 0:
            findings.append(_finding("timed_test_no_limit", "warn", item_id,
                                     "time_limit_seconds",
                                     "timed_test without a positive time limit"))
    elif t == "voice":
        if not (item.get("heading") or "").strip():
            findings.append(_finding("voice_no_heading", "info", item_id, "heading",
                                     "voice task without a heading (hard to navigate)"))
    if t in WORD_IMAGE_TYPES:
        for i, entry in enumerate(item.get("items") or []):
            if not isinstance(entry, dict):
                continue
            if not (entry.get("image") or entry.get("image_url")):
                findings.append(_finding("image_missing", "error", item_id,
                                         f"items[{i}].image", "word-image without an image"))
            if not (entry.get("word") or "").strip():
                findings.append(_finding("word_missing", "error", item_id,
                                         f"items[{i}].word", "word-image without a word"))


def lint_spec(spec):
    """Lint a whole lesson spec.  Returns a list of finding dicts."""
    findings = []
    desc = spec.get("lesson_description")
    if isinstance(desc, str) and re.search(r"<\s*[a-z][^>]*>", desc, re.I):
        findings.append(_finding(
            "lesson_description_html", "warn", "(lesson)", "lesson_description",
            "lesson descriptions render as PLAIN TEXT (pre-line) — tags show as code"))
    all_items = []
    for section in spec.get("sections") or []:
        for item in section.get("items") or []:
            all_items.append(item)
    seen_ids = {}
    for item in all_items:
        item_id = item.get("id") or "(no id)"
        if item_id in seen_ids:
            findings.append(_finding("duplicate_item_id", "error", item_id, "id",
                                     "item id used twice in the spec — state/resume breaks"))
        seen_ids[item_id] = True
        t = item.get("type")
        if t not in V2_TYPES:
            findings.append(_finding("unsupported_type", "error", item_id, "type",
                                     f"unsupported item type {t!r} — build will skip it"))
            continue
        _render_checks(item, item_id, findings)
        _marker_checks(item, item_id, findings)
        _gap_quality_checks(item, item_id, findings)
        _image_checks(item, item_id, findings)
        _structured_checks(item, item_id, findings)
    for dup in find_duplicates(entries_from_spec(all_items)):
        findings.append(_finding(
            "duplicate_item", "warn", ", ".join(str(i) for i in dup["ids"]), "",
            f"identical normalized content in {len(dup['ids'])} items "
            f"(hash {dup['hash']}) — possible double build"))
    return findings


def format_findings(findings):
    """Human-readable one-liners (stable order: severity, then item)."""
    order = {s: i for i, s in enumerate(SEVERITIES)}
    lines = []
    for f in sorted(findings, key=lambda x: (order.get(x["severity"], 9), str(x["item"]))):
        lines.append(f"[{f['severity']}] {f['code']} @ {f['item']}"
                     + (f".{f['field']}" if f.get("field") else "")
                     + f": {f['message']}")
    return lines


def has_failing(findings, severities):
    sev = set(severities or ())
    return [f for f in findings if f["severity"] in sev]
