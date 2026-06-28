# AIDash Version 4 - SQLite + Render Persistent Disk

This version keeps the existing dashboard UI and permissions, but moves persistent app data to SQLite. It still works locally and on Render.

## What changed

- SQLite database on persistent storage: `aidash_v4.db`
- Automatic migration from existing JSON files on first startup
- JSON shadow files are still kept in sync for emergency inspection/export
- Secure cookies, session timeout, audit log and backups remain enabled
- Viewer users can still export PowerPoint
- Admins can create/edit/delete own or assigned-domain use cases
- Super admins manage users and all data

## Render environment variables

Required:

```text
DATA_DIR=/var/data
SESSION_SECRET_KEY=<your-long-secret>
SECURE_COOKIES=true
APP_ENV=production
STORAGE_BACKEND=sqlite
```

Optional:

```text
SESSION_MAX_AGE_SECONDS=1800
MAX_FAILED_LOGINS=5
LOCKOUT_SECONDS=1800
PASSWORD_MIN_LENGTH=12
```

## Render service settings

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Persistent disk

Mount path must remain:

```text
/var/data
```

## Health check

Open:

```text
/health
```

Expected values:

```json
"data_dir": "/var/data",
"storage_backend": "sqlite",
"sqlite_db_exists": true
```

## Safe deployment process

1. Keep the existing Render disk mounted at `/var/data`.
2. Deploy this code.
3. The first startup creates `/var/data/aidash_v4.db`.
4. Existing `/var/data/users.json`, `/var/data/use_cases.json`, `/var/data/settings.json` are migrated into SQLite only if the database does not already contain those keys.
5. Future saves go to SQLite and also update JSON shadow files.

## Local run

Do not set `DATA_DIR` locally unless you want to. It will use the local `data/` folder.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```
