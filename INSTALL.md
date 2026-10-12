# Install cli-anything-langlisto — instructions for AI agents

If a user asked you to install this tool (or sent you this file), follow the
steps below. End state: the bundled agent skill is in YOUR skills directory,
the `cli-anything-langlisto` CLI is installed on this machine, and the user is
logged in to THEIR OWN LangListo account.

**Отправьте это сообщение своему ИИ-агенту (для учителей):**

> Установи инструмент для LangListo по инструкции из
> https://github.com/itsfedor/cli-anything-langlisto — следуй INSTALL.md.
> Потом я сам выполню вход в терминале, а ты проверь `doctor` и предложи
> первую задачу (перенос учеников / расписания / материалов).

---

## 1. Get the skill into your skills directory

Try in this order:

1. **Node / npx available:**
   ```bash
   npx skills add itsfedor/cli-anything-langlisto --skill cli-anything-langlisto -g -y
   ```
   `--skill '*'` installs everything in the repo. Add `-a hermes-agent`,
   `-a claude-code`, `-a codex` (or any agent id) to target one agent.
   `--copy` copies files instead of symlinking (safer on Windows).
2. **Hermes Agent**:
   ```bash
   hermes skills install itsfedor/cli-anything-langlisto/skills/cli-anything-langlisto
   ```
   If the identifier is rejected, add the repo as a tap first:
   ```bash
   hermes skills tap add itsfedor/cli-anything-langlisto
   hermes skills search langlisto
   ```
3. **Manual copy** (works everywhere). Fetch the repo and copy the skill
   folder into your skills directory (`~/.hermes/skills/` for Hermes,
   `~/.claude/skills/` for Claude Code, `~/.codex/skills/` for Codex):
   ```bash
   git clone --depth 1 https://github.com/itsfedor/cli-anything-langlisto.git /tmp/l4
   cp -r /tmp/l4/skills/cli-anything-langlisto <your skills dir>/
   ```
   No git? Use curl + tar:
   ```bash
   curl -L https://github.com/itsfedor/cli-anything-langlisto/archive/refs/heads/main.tar.gz | tar xz -C /tmp
   cp -r /tmp/cli-anything-langlisto-main/skills/cli-anything-langlisto <your skills dir>/
   ```

## 2. Install the CLI

From inside the skill folder (it ships its own wheel — offline-friendly):

```bash
sh scripts/setup.sh
```

The script tries, in order: `uv tool` → `pipx` → `pip --user` → a private
venv in `~/.langlisto/venv`. No sudo, no Node needed for this step.
(Alternative: `pipx install git+https://github.com/itsfedor/cli-anything-langlisto`.)

## 3. Verify + human login handoff

```bash
sh scripts/check.sh        # or: cli-anything-langlisto doctor
```

`doctor` shows whether the CLI is installed, the session file location, and
the live account. Before login it will say "no session" — that is expected.

**Login is the user's step.** Ask them to run this themselves in a terminal
(the password is typed hidden, never stored, and never enters the chat):

```
cli-anything-langlisto login
```

If the machine is headless, or the user prefers not to type the password into
a shell, offer the alternative: sign in normally in the browser, copy the
token from DevTools → Application → Local Storage → `langlisto.access_token`,
and run `cli-anything-langlisto login --token <TOKEN>`.

## 4. First task

After `doctor` shows a green account, offer the user their first real task,
e.g.: "import my students", "set up classes and the weekly schedule",
"build this lesson as a material". The skill
(`skills/cli-anything-langlisto/SKILL.md`) carries the command map and the
house rules — follow it.
