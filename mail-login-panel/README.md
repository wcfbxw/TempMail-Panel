# Mail Login Panel

This optional panel lets users sign in to an existing POP3 mailbox and view
messages through a web UI. It is kept separate from the main TempMail panel so
it can run on its own port and domain.

## Files

- `main.py` - FastAPI application.
- `index.html` - user mailbox UI.
- `admin.html` - admin UI for allowed mailbox domains and stats.
- `.env.example` - copy to `.env` and edit before running.

## Run

```bash
cd mail-login-panel
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8899
```

Put Nginx or another reverse proxy in front of `127.0.0.1:8899` for public
HTTPS access.
