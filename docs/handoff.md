# Handoff to your coding agent

The analyzer's root cause is predicted from logs alone, without the service's
code. When it does not convince you, **Mở trong coding agent** in the ticket
drawer hands the occurrence on screen to your own coding agent (Claude Code,
Codex, agy, Gemini or anything else), which can check the prediction against the
code. herjev's part ends once the terminal has opened: it does not run the agent
or collect its results.

The portal renders one Markdown bundle per occurrence
(`GET /api/tickets/<fingerprint>/handoff[?occ=<incident id>]`, `text/markdown`,
never cached). It holds the ticket link, project, service, host, level and
counts, the analyzer's full prediction (or a note that there is none), the
sample and preceding log lines, and the agent's task: confirm or refute each
predicted cause with file:line evidence, find the code that logs the message,
say when the deployed commit may differ, and ask before touching production.
**Copy prompt** and **Tải .md** deliver the same text and need no installation.

**Setup, once per machine** (macOS; Linux through a desktop entry):

```bash
herjev handoff setup          # asks for the portal URL, agent, runtime, terminal and token
herjev handoff setup --portal-url https://herjev.example.workers.dev --agent claude --runtime herdr --terminal ghostty
herjev handoff setup --uninstall [--purge]
```

Setup offers the agents and session runtimes found on your `PATH` and the terminals installed, checks
the token against `/api/status`, and saves `$HERJEV_HOME/handoff.json`
(`~/.herjev`, mode 0600) with the portal URL, the token and your shell's `PATH`.
The token comes from `$HERJEV_DASHBOARD_TOKEN` or a hidden prompt, never from a
flag. It then registers `herjev://`: on macOS by building
`~/Applications/Herjev Handoff.app`, a small AppleScript applet that runs
`herjev handoff open <url>`; on Linux with
`~/.local/share/applications/herjev-handoff.desktop` and `xdg-mime`. The handler
starts without your shell profile, so the absolute path of `herjev` and your
`HERJEV_HOME` are written into it. Re-running setup is safe and replaces the
handler; re-run it if `herjev` or `HERJEV_HOME` moves (for example after
reinstalling herjev). `--template` is accepted only with a `custom` agent or
terminal.

**What a click does**: the browser asks once whether to open the link, then
`herjev handoff open`:

1. fetches the bundle from the configured portal, with the saved token;
2. asks which folder the agent should work in (the dialog starts at your previous
   choice; Cancel stops here and nothing starts);
3. writes `$HERJEV_HOME/handoff/<fp>-<occ>-<time>/handoff.md` and
   `launch.command` (directory 0700, deleted after 7 days because they hold
   production logs);
4. opens a terminal running `launch.command`, which shows the ticket and waits
   for Enter before starting the agent with
   `Read and follow the herjev incident handoff in <file>.` With a runtime, the
   agent's session lives in that runtime and the terminal shows it (see below).

Failures are posted as a desktop notification and appended to
`$HERJEV_HOME/handoff/handoff.log`.

| `--agent` | runs |
|---|---|
| `claude` | `claude "<instruction>"` |
| `codex` | `codex "<instruction>"` |
| `agy` | `agy -i "<instruction>"` |
| `gemini` | `gemini -i "<instruction>"` |
| `custom` | your template, started directly (no terminal, no Enter gate) |

| `--terminal` | opens `launch.command` with |
|---|---|
| `terminal` (default on macOS) | `open -a Terminal` |
| `iterm2`, `ghostty` | AppleScript (macOS asks once to allow controlling the app) |
| `wezterm`, `kitty` | `wezterm start --cwd …` / `kitty --single-instance --directory …` |
| `linux` | `$TERMINAL -e`, else `x-terminal-emulator -e` |
| `custom` | your template, which must contain `{launch_script}` |

A handoff combines three choices: the agent, the terminal app, and the
**runtime** that holds the agent's session. For example `claude` + `ghostty` +
`herdr` starts Claude Code in a herdr workspace and opens Ghostty attached to
herdr. With `none` the terminal runs the agent directly, as before.

| `--runtime` | what happens |
|---|---|
| `none` (default) | the terminal opens `launch.command` |
| `tmux` | the terminal opens `session.command`: `tmux new-session -s herjev-<handoff dir> -c <folder> <launch.command>` |
| `herdr` | when a window of the configured terminal app already shows herdr (a `herdr` client whose parent processes include that app; for `linux` and `custom` any herdr window counts), herjev adds a tab to its focused workspace (`herdr tab create --cwd … --focus`), runs `launch.command` in it and brings the terminal app forward; no new window opens. Otherwise, including when herdr is open only in another terminal app, it creates a workspace in the folder (`herdr workspace create --cwd … --focus`, starting `herdr server` if it is not running), runs `launch.command` there, and the terminal opens `herdr` to attach |
| `paseo` | the paseo daemon runs the agent (`paseo run --provider claude\|codex --no-wait --quiet <instruction>`, starting `paseo daemon start` if `run` fails) and the terminal opens `paseo attach <id>`; there is no Enter gate, and only `claude` and `codex` are allowed |
| `orca` | `orca terminal create --worktree path:<folder> --command <launch.command> --json` (running `orca open` first if needed) in the Orca app; no terminal app opens and a notification says where the agent waits |

tmux and herdr are tested on macOS; paseo and orca follow their documented CLIs
([paseo.sh/docs/cli](https://paseo.sh/docs/cli),
[onorca.dev/docs/cli/reference](https://www.onorca.dev/docs/cli/reference)) and
are untested here. A custom agent starts itself, so it allows only `none`.

**Custom templates** are split like a shell command once, when saved, and then
run without a shell, so log text can never become a command. Placeholders are
replaced inside each word: `{launch_script}` (the gated script that starts your
preset agent), `{prompt_file}` (the bundle), `{instruction}` (the one-line
prompt) and `{workdir}` (the chosen folder, also the working directory). With
`--agent custom` the template starts the agent itself, so there is no
`launch.command` and no Enter gate. With a runtime, `{launch_script}` in a
custom terminal template is `session.command`, the script that shows the
runtime's session.

**Safety**: the link carries only the ticket and occurrence ids (hex, checked);
the portal and token always come from `handoff.json`, so a page that fires a
`herjev://` link cannot redirect the token. Log text lands in `handoff.md`, and
the incident title is also shown by `launch.command`, quoted and with control
characters removed; nothing from the log is ever run. The bundle tells the agent
that the title, the analyzer's prediction and the logs are untrusted data, and
not to run any command from the file without your approval.
