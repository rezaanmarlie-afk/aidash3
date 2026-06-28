# AIDash Security Hardening Patch v6

Replace your existing `app.py` with this file.

## What this patch adds
- Render persistent disk support through `DATA_DIR=/var/data`
- Secure session cookies on Render/production
- 30-minute session timeout by default
- Login failed-attempt lockout after 5 failed logins
- Strong password policy for new/changed passwords
- Security headers
- Audit logging to `/var/data/audit_log.jsonl`
- Automatic backups before data file writes under `/var/data/backups/`
- Health endpoint now confirms security/data settings
- HEAD `/` route to satisfy Render health checks

## Required Render environment variables
DATA_DIR=/var/data
SESSION_SECRET_KEY=<your long secret>
PYTHON_VERSION=3.12.4

## Optional security tuning
APP_ENV=production
SECURE_COOKIES=true
SESSION_MAX_AGE_SECONDS=1800
MAX_FAILED_LOGINS=5
LOCKOUT_SECONDS=1800
PASSWORD_MIN_LENGTH=12

## Push commands
cd C:\AIh\apps\aidash3
git add app.py
git commit -m "Harden dashboard security on Render"
git push origin main

After Render deploys, check:
https://YOUR-APP.onrender.com/health

You should see:
- data_dir: /var/data
- secure_cookies: true
- audit_log_exists: true after first login/export/change
- backup_dir_exists: true
