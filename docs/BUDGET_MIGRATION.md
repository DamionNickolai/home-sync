# 🏦 MONTHLY BUDGET MIGRATION: MASTER PLAN

## Phase 1: Database Architecture & Encryption
**Objective:** Establish the foundational tables in Supabase and ensure all financial data is locked behind the `security.py` zero-knowledge encryption engine.

* **Create the Tables:** Execute the master SQL script to build the 4 core dev tables (`budget_categories_dev`, `household_incomes_dev`, `expenses_dev`, `cash_flow_routing_dev`) and alter the shared `users` table.
* **Apply Row Level Security (RLS):** Ensure all new tables are filtered by the user's specific `household_id`.
* **The Security Engine:** All textual names, categories, and dollar amounts will be passed through `encrypt_data()` and `decrypt_float()` before touching the database.

## Phase 2: The Core Modules (UI & Logic)
**Objective:** Build the Streamlit interface replacing the Excel environment.

### 1. The Household Dashboard (Admin/Dev Only)
* **Income Stream:** Dynamic list of encrypted incomes, calculating Gross vs. Net and Taxable vs. Non-Taxable automatically.
* **The Master Ledger:** Real-time metrics showing total income vs. total household expenses.
* **Cash Flow & Treasury Tab:** A dedicated tab recreating the Q13:Y32 routing table and calculating the Y39 "Spend Money" algorithm dynamically.

### 2. The Individual Budget (Member Access)
* A localized view showing only the user's specific allowed funds (their half of the Y39 math).
* **The Privacy Toggle:** A UI-level switch allowing members to hide their granular spending from the master Household Rollup.

### 3. The Expense Tracker (Standalone Event Stream)
* A unified logging screen. Select the month, the category, the amount, and type the details.
* **Live Overbudget Tracking:** Every expense instantly updates progress bars on the dashboards. If a category exceeds its limit, it turns red.

## Phase 3: Cross-Module Automations
**Objective:** Eliminate double-entry through event-driven logic in `database.py`.

* **The Project Dual-Write:**
    * If an expense is logged under a "Project" category, Streamlit dynamically prompts the user for the specific Active Project.
    * Python encrypts and saves the expense to the ledger, then safely pulls, decrypts, increments, and re-encrypts the `actual_spent` value in `project_budgets`.
    * Python appends an audit log to the project's notes: `[Date] Auto-Expense Logged: $X at Y`.
* **Monthly Lazy Initialization:**
    * On the first login of a new month, Python automatically duplicates recurring incomes, categories, and active routing targets from the previous month to set up the new ledger.

## Phase 4: Deployment & Testing
**Objective:** Validate math and launch.

* **Local Sandbox Testing:** Build the UI and test the math against the Excel sheet to ensure pennies match perfectly.
* **The "Honor System" Check:** Ensure the Member Privacy Toggle effectively drops data from the Household Rollup.
* **Production Rollout:** Push to the cloud, run the production SQL script, and launch!