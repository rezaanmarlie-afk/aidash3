# ASOC AI ExecAir Dashboard - Render Ready

This package is a complete working FastAPI app for Render.com.

## Included

- Working dashboard app
- PowerPoint export
- Embedded Vodacom logo support
- `static/vodacom_logo.png`
- Render config
- Render-safe requirements
- Health endpoint: `/health`

## Local run

```powershell
cd C:\code\asoc-ai-execair-dashboard
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Test export:

```text
http://127.0.0.1:8000/export/pptx
```

## Render settings

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Health check path:

```text
/health
```

## Important

Render's free filesystem is temporary. If you change data through the app and Render restarts or redeploys, JSON data can reset to the committed repo version. For a production version, move `data/*.json` to PostgreSQL or another persistent store.
