# Persistent Login Validation Runbook (Home Sync)

This runbook validates persistent-login encryption safely.

Scope:
- Home Sync only
- Read-only checks first
- No token values are ever printed

Important:
- Do not rotate `ENCRYPTION_KEY` during validation.
- Home Sync and Get Fit Together must use the same production `ENCRYPTION_KEY` because both apps use the shared `user_sessions` table.

## What "staging" means in this workspace

For your setup, staging means the cloud `dev` environment (Supabase dev project / dev cloud tables), not local Streamlit runtime.

- Local is your machine process and local `.env` / `.streamlit` config.
- Staging is cloud dev data and cloud auth behavior.

Use local only as the execution host for scripts. Validate against cloud dev first, then cloud prod.

## Environment matrix (what runs where)

- Local machine terminal:
  - Run Step 1 and Step 2 scripts.
  - `.env` decides whether you are checking dev cloud or prod cloud.
- Deployed Home Sync dev URL (HTTPS):
  - Run Step 3 canary browser behavior test for staging.
- Deployed Home Sync prod URL (HTTPS):
  - Run Step 3 canary browser behavior test for production.

Do not use localhost as the source of truth for Step 3 cookie persistence. Localhost is useful for development, but production-like cookie behavior should be validated on deployed HTTPS URLs.

## Prerequisites

1. Activate venv.
2. Ensure `.env` has:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `ENCRYPTION_KEY`
3. Point `.env` to the target environment before each run:
   - First run against dev cloud.
   - Second run against prod cloud.

## Step 1: Read-only encryption validation

Run full-table validation:

```powershell
.\venv\Scripts\python.exe maintenance/validate_user_sessions_encryption.py --strict
```

Optional active-session-only check:

```powershell
.\venv\Scripts\python.exe maintenance/validate_user_sessions_encryption.py --active-only --strict
```

Interpretation:
- `PASS`: no decrypt failures and no plaintext-shape tokens detected.
- `ISSUES DETECTED`: at least one token failed decryption or appears plaintext.

If issues are detected:
1. Stop and investigate key mismatch first.
2. Do not rotate keys.
3. Do not run write migrations yet.

## Step 2: Legacy plaintext token audit (still safe)

Dry-run migration audit:

```powershell
.\venv\Scripts\python.exe maintenance/migrate_user_sessions_refresh_tokens.py
```

Interpretation:
- If plaintext count is zero, no backfill needed.
- If plaintext count is non-zero, plan controlled backfill in Step 5.

## Step 3: App behavior canary (manual)

In target environment (dev cloud first), run this in the deployed app URL:
1. Sign into Home Sync with one canary account.
2. Hard refresh browser tab.
3. Close/reopen browser and return to app.
4. Wait 5-10 minutes and refresh again.
5. Confirm user is still logged in after each action.

Expected result:
- User remains authenticated each time.

Failure signal:
- User is sent back to login unexpectedly.

### Canary account definition and setup

A canary account is a low-risk, dedicated test user used to detect auth/session regressions before broad user impact.

Recommended canary account rules:
- Use a dedicated non-admin member account (not your main developer account).
- Keep it in the same household/permission model as normal users.
- Do not use it for regular daily work.
- Give it a known label, for example: `canary_home_sync_member`.

Why this matters:
- You test real login persistence behavior with minimal blast radius.
- If a session issue appears, only the canary user is affected during validation.
- Results are repeatable across dev cloud and prod cloud.

Canary test cadence:
1. Run in dev cloud first after script checks pass.
2. If dev cloud passes, repeat in prod cloud.
3. Keep one browser profile dedicated to canary tests for consistent cookie behavior.

## Step 4: Promote validation from dev cloud to prod cloud

Repeat Steps 1-3 with prod `.env` values.

Promotion gate:
- Proceed only if Steps 1-3 pass in dev cloud.

## Step 5: Controlled write action (only if needed)

Run this only when Step 2 shows plaintext rows.

```powershell
.\venv\Scripts\python.exe maintenance/migrate_user_sessions_refresh_tokens.py --apply
```

Immediately re-run Step 1 and Step 2 afterward.

Expected post-apply state:
- `validate_user_sessions_encryption.py --strict` returns pass.
- Dry-run migrate script reports zero plaintext tokens.

## Quick troubleshooting map

Symptom: decrypt failures > 0
- Most likely: wrong `ENCRYPTION_KEY` for this environment.
- Action: restore correct key and re-run Step 1.

Symptom: plaintext-shape tokens > 0
- Most likely: legacy rows not yet backfilled.
- Action: run Step 5 (controlled apply), then re-validate.

Symptom: validation pass, but persistent login still fails
- Most likely: cookie persistence/import/runtime issue.
- Action: verify cookie dependency loads in runtime and add temporary categorized auth logging.

## Commands summary

```powershell
# read-only encryption validation
.\venv\Scripts\python.exe maintenance/validate_user_sessions_encryption.py --strict

# read-only active sessions only
.\venv\Scripts\python.exe maintenance/validate_user_sessions_encryption.py --active-only --strict

# read-only plaintext audit
.\venv\Scripts\python.exe maintenance/migrate_user_sessions_refresh_tokens.py

# controlled write backfill (only when needed)
.\venv\Scripts\python.exe maintenance/migrate_user_sessions_refresh_tokens.py --apply
```
