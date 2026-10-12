# Changelog

## v2.0.0 — 2026-10-13

**Engine — full type coverage.** 18 new exercise types: `sort_columns`, the
ordering family (`sentence_order` / `text_order` / `unscramble`), the
word-image family (`enter_word_image` / `drag_word_image` /
`select_word_image`), `image_carousel` / `gif_animation`, `diagram`,
`interactive_video`, `audio_match_pairs`, `wordlist`, `link`, `photo_answer`,
`divider`, `timed_test`, `select_word_gap` (all live-verified).

**Quality features.** Gap hints; answer variants with an auto-expansion linter;
free gaps with sample answers; teacher notes on ALL exercise types; image
captions via `media.caption`; duplicate guard (normalized section/item
hashes); render spec-lint gate.

**CLI.** `materials verify | run | status | tree`; `exercises
show|delete|notes|order|patch`; `books modules | lessons order | purge-starter`;
`homework list|show|available|assigned|choose|remove|clear|review|accept|return`;
`preview open` (platform demo-student view, `--class/--unit/--tab`);
`sections list|reorder|delete`; `units cover set|clear`;
`students patch|unenroll|verify`.

**QA.** 65 offline tests; full surface live-verified on a 27-type showcase lesson.

**Upstream fixes folded in (v1.1.1/v1.1.2).** `info` items with `media.video_url`
build a media player (YouTube links embed as a player) instead of falling back
to a note; `true_false` answers accept `"not_stated"` (pairs with
`not_stated_enabled`); and a build that adopts an existing same-title lesson now
reports how many sections it already has and warns to keep lesson titles unique.

**Earlier.** v1.1.0 renamed the project to `cli-langlisto` (old
`cli-anything-langlisto` command kept as an alias); v1.0.x added teacher notes
for any exercise type, voice tasks and the offline test suite.
