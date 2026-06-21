# 🛡️ Database Encryption Master Rollout Plan

## 📌 Phase 1: Home Sync Development Environment (`_dev`)
*Objective: Safely test schema changes, migrations, and UI functionality in isolated development tables.*

### `wish_list_dev` & `wish_list`
- [x] **Complete:** Encryption engine (`security.py`) built and tested.
- [x] **Complete:** UI data editor wired up and verified against Supabase dashboard.

### `project_budgets_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit (`information_schema.columns`).
- [ ] Step 2: Classify columns into Buckets (A: Structural, B: Functional, C: Sensitive).
- [ ] Step 3: Cross-check Bucket C against `database.py` insert/update payloads.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update (SQL `ALTER TABLE` to convert sensitive numerics to `TEXT`).
- [ ] Step 2: Run Data Migration Script (`migrate_projects.py` to scramble existing rows).
- [ ] Step 3: Code Injection (Update `database.py` budget functions).
- [ ] Step 4: Local UI Test (Verify Streamlit UI looks normal; Supabase dashboard shows ciphertext).

### `household_tasks_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit.
- [ ] Step 2: Classify columns (Likely sensitive: `task_name`, `notes`).
- [ ] Step 3: Cross-check Bucket C against `database.py`.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update (if necessary).
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Code Injection (Update `database.py` task functions).
- [ ] Step 4: Local UI Test.

### `household_finance_settings_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit.
- [ ] Step 2: Classify columns (Likely sensitive: `projects_funds`).
- [ ] Step 3: Cross-check Bucket C against `database.py`.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update (Convert numeric funds to `TEXT`).
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Code Injection.
- [ ] Step 4: Local UI Test.

### `history_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit.
- [ ] Step 2: Classify columns.
- [ ] Step 3: Cross-check Bucket C against Python logs.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Code Injection.
- [ ] Step 4: Local UI Test.

---

## 🚦 Phase 1.5: Final Dev Validation Steps
- [ ] **Cross-Module Integration Test:** Navigate through all Home Sync tabs locally to ensure no unencrypted data calls are crashing the app.
- [ ] **Data Editor Stress Test:** Add, Edit, and Delete a row in every module to verify `update` and `delete` functions correctly handle the encrypted keys/data.
- [ ] **Dashboard Verification:** Log into Supabase, open every `_dev` table, and visually confirm zero plain-text sensitive data exists.

---

## 🚀 Phase 2: Home Sync Production Rollout
*Objective: Replicate the successful dev loops on the live production tables.*

### `project_budgets`
- [ ] Step 1: Execute Schema Update (`ALTER TABLE ... TYPE TEXT`).
- [ ] Step 2: Run Data Migration Script (Targeting prod table).
- [ ] Step 3: Verify production data loads cleanly in the app.

### `household_tasks`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify production data loads cleanly.

### `household_finance_settings`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify production data loads cleanly.

### `history`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify production data loads cleanly.

### Remaining Shared/Structural Tables Audit
*(Audit to ensure no sensitive text fields exist; do not encrypt structural IDs or Auth constraints)*
- [ ] `backlog` (Audit `feature`, `notes`, `work_notes` — implement loop if desired).
- [ ] `app_release_ledger` (Audit only).
- [ ] `users` (Audit metadata/preferences).
- [ ] `user_sessions` (Audit only).

### 🎯 Home Sync Production Smoke Test
- [ ] Log in as a standard Member; verify budget viewing restrictions and data decryption.
- [ ] Log in as Admin; verify data renders correctly and Admin panel functions.
- [ ] Confirm no app slowdowns or `Fernet` decryption errors in the logs.

---

## 🏋️‍♂️ Phase 3: Get Fit Together App (Dev Environment)
*Objective: Extend the security engine into the fitness module.*

### Engine Integration
- [ ] Step 1: Verify `security.py` imports correctly into Get Fit Together Python files.
- [ ] Step 2: Add dynamic routing for GFT tables (e.g., `GARMIN_TABLE = garmin_metrics_dev if local...`).

### `coach_chat_history_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit.
- [ ] Step 2: Classify columns (Likely sensitive: `message_body`, `coach_notes`).
- [ ] Step 3: Cross-check against GFT database functions.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Code Injection.
- [ ] Step 4: Local UI Test (Send a chat message, verify encryption).

### `garmin_metrics_dev`
**3-Step Verification**
- [ ] Step 1: Run SQL Schema Audit.
- [ ] Step 2: Classify columns (Likely sensitive: `weight`, `sleep_score`, `stress_level`).
- [ ] Step 3: Cross-check against GFT database functions.
**Encryption Implementation**
- [ ] Step 1: Execute Schema Update (Convert health numerics to `TEXT`).
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Code Injection.
- [ ] Step 4: Local UI Test (Check Garmin dashboard rendering and math).

### `gym_user_profiles_dev` *(If applicable)*
- [ ] Execute standard 3-Step Verification.
- [ ] Execute standard Encryption Implementation.

### 🚦 GFT Final Dev Validation
- [ ] Check math on Garmin metrics (ensure Pandas handles the decrypted string-to-float conversions correctly).
- [ ] Verify coach chat history loads chronologically without breaking.

---

## 🏁 Phase 4: Get Fit Together Production Rollout
*Objective: Lock down live health and coaching data.*

### `coach_chat_history`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify in app.

### `garmin_metrics`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify in app.

### `gym_user_profiles`
- [ ] Step 1: Execute Schema Update.
- [ ] Step 2: Run Data Migration Script.
- [ ] Step 3: Verify in app.

### 🎯 Full Ecosystem Production Smoke Test
- [ ] Ensure seamless navigation between Home Sync and Get Fit Together apps.
- [ ] Database is fully blinded; a compromised Supabase account yields only structural IDs and `gAAAA...` ciphertext.