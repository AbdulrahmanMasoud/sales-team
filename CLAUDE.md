# Instructions for Claude: the "Sales Team" project

The person you're helping is usually the **business owner, not technical**, on a **Mac**. Talk to them in **simple Egyptian Arabic**, in short sentences, without jargon. If you must use a technical term, explain it in half a line. Keep code, comments and commit messages in English.

## When they say "run the project" or "set it up"

1. Remove the Mac download quarantine and make the launchers executable:
   ```bash
   xattr -dr com.apple.quarantine . 2>/dev/null; chmod +x *.command
   ```
2. Check Python 3.10 or newer: `python3 --version`.
   If missing or older, tell them to install it from https://www.python.org/downloads/macos/ (the normal installer, Continue until the end), then continue. Don't use Homebrew unless they ask.
3. Create the environment (first time only):
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
4. Start it in the background and open the setup page:
   ```bash
   .venv/bin/python run.py
   ```
   Then `open http://127.0.0.1:8765/#setup`.
   Tell them that from now on they can just double-click **Start.command**.
5. Point them to the setup page in the dashboard («الإعداد أول مرة»). It has step-by-step guides for everything (Claude, business basics, Meta, Telegram).

## Updates

- Normal path: **Update.command** (or `bash Update.command`). It downloads the latest version from GitHub over the code, leaves `data/`, `.env` and `.venv/` untouched, and backs up `data/` to `backups/`.
- **For the developer:** every change that gets pushed must bump the number in `VERSION` (e.g. 2026.11.21.2 → 2026.11.21.3), otherwise the dashboard and Update.command won't see it. Database changes must be additive only (new tables or columns), so `db._add_missing_columns` upgrades users' existing databases by itself.

## If they have an old copy without Update.command

All their data is in two places inside the old folder: `data/` (orders, customers, decisions, agent memory, settings) and `.env` (tokens). New downloads deliberately don't include either.
1. Ask them to close the old black window (or `lsof -ti :8765 | xargs kill`).
2. Copy (don't move) `data/` and `.env` from the old folder to the new one: `cp -R "OLD/data" "NEW/" && cp "OLD/.env" "NEW/.env" && chmod 600 "NEW/.env"`. Never read `.env` or show its contents.
3. Start the new version. It upgrades the database and settings by itself and deletes nothing.
4. Check the page opens and Meta is still connected, then tell them they can delete the old folder. Don't delete it yourself.

## Security: never ask for a token in chat

- **Never ask** them to paste a token, password or app secret into the conversation. Tokens go into the setup page in the dashboard only; it tests them and saves them to `.env` with mode 600.
- If they paste a token into the chat by mistake: don't repeat it or write it into any file or command. Tell them to generate a new one and enter it in the setup page, because whatever is typed in the chat stays in the conversation.
- Never read `.env` and show its contents.

## Don't change these unless the owner explicitly asks

- Safety code: `app/guards.py`, `app/executor.py`, `app/tools.py`, and the agent options in `app/agents.py` (`build_options`, `SECURITY_RULES`).
- Don't turn off dry run and don't change permissions from "ask" to "auto" on your own. That's the owner's decision, from the settings page.
- The server must stay on `127.0.0.1` only.

If they ask to change any of these, explain the risk in two lines and do it once they confirm. After any change run the tests:
```bash
.venv/bin/pip install pytest && .venv/bin/python -m pytest -q
```

## Common problems

| Problem | Fix |
|---|---|
| Mac says a .command file "can't be opened" | Right-click the file → Open → Open. Or System Settings → Privacy & Security → Open Anyway. Or step 1 above. |
| "Test Claude" says not signed in | Double-click **Claude Login.command**, finish signing in in the browser, then `/exit`. |
| Port 8765 is in use | The app is probably already running: open http://127.0.0.1:8765/ . Otherwise: `lsof -i :8765`. |
| Meta refuses the token | The setup page explains why in Arabic. Usually the system user wasn't given the ad account, or `ads_management` is missing. |
| Agents don't run on schedule | The laptop must be awake with the app open. Start.command keeps the Mac awake while its window is open. |
| They want to try without their account | `.venv/bin/python run.py --demo` (fake data in `data-demo/`). |

## About the project

- Python + FastAPI; dashboard in `static/` (UI text is Egyptian Arabic on purpose); data in `data/sales.db` (SQLite).
- 3 agents: media buyer, customer follow-up, weekly review. They run on the owner's Claude subscription through the Claude Agent SDK.
- Full details in `README.md`. End-user guide (Arabic): `START-HERE.md`.
