# Repository Guidelines

## Project Structure & Module Organization
- `nudge/`: Python package for CLI, device control, service loop, storage, subtitles, and web UI.
- `nudge-data/`: runtime content, logs, and SQLite history; `nudge-data/content/` stores per-title JSON.
- `config.json` and `wordlist.txt`: local configuration and language filter list; see `.sample` files.
- Docs and assets live at the repo root (`README.md`, `QUICKSTART.md`, `DATA.md`, `ROADMAP.md`, `*.png`).

## Build, Test, and Development Commands
Use `uv` to run the CLI and service.
- `uv run nudge scan`: discover AppleTV devices on the network.
- `uv run nudge pair`: pair with a device (prompts for PIN).
- `uv run nudge test ~/Downloads/movie.srt --offset -2 --save`: parse subtitles, adjust timing, and save content.
- `uv run nudge start` / `uv run nudge stop`: start or stop the background service.
- `uv run nudge status`: check service/device status.
- `uv run nudge simulate "Movie Title"`: rapid end-to-end nudge testing.
Tip: `./nudge.sh` is a thin wrapper for `uv run python -m nudge.cli`.

## Coding Style & Naming Conventions
- Python 3.10+, 4-space indentation, module-level docstrings, and explicit imports.
- Use snake_case for functions and variables, PascalCase for classes, and ALL_CAPS for constants.
- Keep CLI changes in `nudge/cli.py`, service logic in `nudge/service.py`, and web UI in `nudge/web.py`.
- No formatter is configured; match the existing style in touched files.

## Testing Guidelines
- No automated test suite is present.
- Validate changes via CLI workflows: `uv run nudge verify "Movie Title"` and `uv run nudge simulate "Movie Title"`.
- Use local subtitle files and wordlists; do not commit `nudge-data/`, logs, or credentials.

## Commit & Pull Request Guidelines
- Workflow: create a feature branch, develop, test locally, then commit; merge the feature branch and push to `origin`.
- Use branch names like `feature/<short-topic>` or `fix/<short-topic>`.
- Recent commits use short, sentence-style subjects (e.g., "Enhance README...", "added export function").
- Prefer imperative verbs, keep the subject under ~72 chars, and mention scope when helpful (e.g., `cli: add export`).
- PRs should include a summary, commands run, and screenshots for web UI changes.
- Link related issues when applicable.

## Configuration & Security Notes
- Copy `config.json.sample` and `wordlist.txt.sample` for local setup.
- `credentials.conf` and `nudge-data/` include device identifiers and logs; never commit them.
