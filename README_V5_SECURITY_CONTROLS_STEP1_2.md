# ExecAir V5 Security Controls – Step 1 and Step 2

This code-only patch adds the next security increment on top of V5 MFA.

## Included

### Step 1: Super Admin security controls
- User list now shows MFA status per user.
- Super Admin can reset MFA for a user.
- Super Admin can force MFA re-enrolment for a user.
- Super Admin can unlock a locked user account.
- Super Admin can disable or enable users directly from User Management.
- User Management now displays lockout state and failed login count.

### Step 2: Account lockout strengthening
- Failed password and failed MFA attempts increment the same lockout counter.
- Default lockout: 5 failed attempts, 30 minutes.
- Lock/unlock events are written to the audit log.
- Health endpoint now reports locked user count and lockout settings.

## Files changed
- `app.py`
- `templates/users.html`

## Required environment variables on Render
Keep your current values:

```text
DATA_DIR=/var/data
STORAGE_BACKEND=sqlite
SECURE_COOKIES=true
APP_ENV=production
MFA_REQUIRED=true
MFA_ISSUER=ASOC AI Dashboard
```

Optional lockout tuning:

```text
MAX_FAILED_LOGINS=5
LOCKOUT_SECONDS=1800
```

## Test plan
1. Deploy the patch.
2. Log in as a super admin.
3. Open `/admin/users`.
4. Confirm the table shows MFA and Lockout columns.
5. Enter a wrong password 5 times for a test user.
6. Confirm the user shows as locked.
7. Click Unlock.
8. Confirm the user can log in again.
9. Click Reset MFA or Force MFA for a test user.
10. Confirm the next login asks that user to scan/setup Authenticator again.

## Push commands

```powershell
cd C:\AIh\apps\aidash3

git add app.py templates/users.html README_V5_SECURITY_CONTROLS_STEP1_2.md
git commit -m "Add super admin security controls and account lockout UI"
git push origin main
```
