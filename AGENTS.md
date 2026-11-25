# Repository Guidance

## Project Overview

Vega is a Windows personal assistant with chat, voice input/output, desktop
commands, reminders, a study Lab Log, and an optional Telegram bridge. It uses
Ollama locally and supports optional cloud providers. The root README currently
describes a different project; use the current source and launchers as the source
of truth.

## Code Map

- `vega_webview.py`: current entry point, Bottle HTTP API, Server-Sent Events,
  shared runtime state, browser app window, tray, and background services.
- `web/index.html`, `web/style.css`, `web/app.js`: plain HTML/CSS/JavaScript UI.
  `web/settings.js` and `web/lablog.js` extend the settings and Lab Log views.
  There is no frontend package manager or build step.
- `vega_core.py`: `Vega`, command routing, model providers, configuration,
  persistence helpers, and `ReminderClock`.
- `vega_control.py`: desktop automation and application/media controls.
- `vega_voice.py`: `Mouth`, `Ears`, wake-word recognition, and audio utilities.
- `vega_security.py`: PINs, locking, voiceprints, and speaker verification.
- `vega_chats.py`: `ChatStore`, saved conversations, and projects.
- `vega_lablog.py`, `vega_tracks.py`, `vega_summaries.py`: mixins used by `Vega`.
- `vega_telegram.py`: optional phone bridge; `vega_popup.py`: desktop popups.
- `vega_app.py`, `vega_pages.py`, `vega_lablog_window.py`: older Tkinter UI.
  `assistant.py` is an earlier command-line assistant.
- `tests/command_test.py`: standalone command diagnostic, not an assertion-based
  unit test suite.

Work in the root implementation. Update directories (`Vega-update-*`),
`vega_2_fix`, backup directories, and ZIP archives are historical copies, not the
default edit targets. Exclude browser profiles, logs, archives, and caches from
routine searches.

## Setup and Running

Run commands from the repository root in PowerShell. Launchers use the Python
executable recorded in `python_path.txt` when available; check that interpreter
when diagnosing missing dependencies.

```powershell
python -m pip install -r requirements-vega.txt
python -m pip install bottle pystray pillow comtypes
python vega_webview.py
```

`requirements-vega.txt` covers more of the application than `requirements.txt`,
but omits the web/tray helpers listed above. Voice unlock additionally requires
`speechbrain`, `torch`, and `torchaudio`; inspect `setup_voice.py` before using it
because it installs packages and restarts Vega. Local model features need a
running Ollama service and the configured model.

`Start Vega.bat` starts the current UI through `Start Vega.vbs`.
`Start Vega (show errors).bat` keeps errors visible. The web server binds to
`127.0.0.1:47632`; port `47631` is the single-instance lock shared with the old
UI. Check for an existing instance before starting another. Closing the browser
window can leave Vega running in the tray.

## Change Conventions

- Follow the existing style: Python uses four-space indentation and snake_case;
  frontend JavaScript uses two-space indentation and camelCase. Keep changes
  focused; avoid introducing a framework or reformatting large files.
- Keep API routes, JSON payloads, DOM IDs, and SSE event names consistent between
  `vega_webview.py` and the scripts in `web/`.
- Reuse `here()`, `read_json(name, default)`, `write_json()`, and
  `write_json_file()` for persisted data. The JSON writer uses atomic replacement.
  Use `save_config_value()` for settings so credentials follow existing storage
  rules. Preserve existing data formats and defaults.
- Keep blocking model, network, and audio work off UI/event handlers using the
  existing thread, queue, and event patterns. Preserve cancellation and locking.
- Preserve the shared single-instance mechanism and lock/PIN checks when changing
  routes or commands. `VERSION` in `vega_webview.py` controls replacement of older
  instances and frontend cache refresh; consider it when changing release behavior.
- Treat vendored `web/marked.min.js`, `web/highlight.min.js`, and `highlight.css`
  as dependencies rather than ordinary application edit targets.

## Validation

There is no configured general test runner, linter, or CI workflow. Use checks
appropriate to the changed code and report exactly what ran.

- For Python syntax, compile only changed modules, for example
  `python -m py_compile vega_core.py vega_webview.py`. Compilation writes bytecode;
  some cache files are tracked, so inspect the diff afterward.
- For frontend JavaScript, use `node --check web/app.js` and equivalent commands
  for other changed scripts when Node is available.
- `python tests/command_test.py` mocks `subprocess.Popen` and `os.startfile` only.
  Its commands include locking the laptop and closing applications; other action
  paths remain live. Inspect and isolate those paths before running it. Printed
  YES/NO results are diagnostics, not a reliable automated pass/fail gate.
- For an already-running app, `GET /api/status` checks its status;
  `GET /api/selftest` runs built-in diagnostics. Results depend on configured
  optional features, hardware, and Ollama availability. Inspect individual
  results, not just the top-level `ok` field.
- For UI changes, exercise the affected flow in the actual Bottle-served page,
  check browser errors, and check SSE updates. Opening the HTML alone does not
  provide the backend or version substitution.
- Importing `vega_webview.py` initializes runtime objects and logging. Prefer
  isolated fixtures and mocks over importing it into a generic smoke test.

## Local Data and Working Tree

This repository also holds live personal data and often has local changes.
Inspect `git status --short` before editing and preserve existing work.

Do not print, commit, or copy credentials from `.env`, PIN material from
`security.json`, voiceprint arrays, browser profiles, or personal conversations.
Treat `config.json`, chat/memory files, diary/documents, contacts, reminders,
progress, Lab Log data, and logs as user data. Read only what the task needs;
use temporary fixtures for tests and do not reset live state to make a test pass.
Avoid running repair/install scripts as routine validation: they can replace
files, install dependencies, stop processes, and restart the application.
