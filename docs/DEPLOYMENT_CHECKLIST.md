# Implementation Checklist - Home-Sync Backlog Enhancements

## Pre-Deployment Steps

- [x] **Apply Database Migration**
  - Run migration `003_add_backlog_release_management.sql` against your Supabase database
  - This adds: `work_notes`, `release_date`, `version` columns to backlog table
  - Command (via Supabase dashboard):
    ```sql
    ALTER TABLE backlog ADD COLUMN IF NOT EXISTS work_notes TEXT;
    ALTER TABLE backlog ADD COLUMN IF NOT EXISTS release_date DATE;
    ALTER TABLE backlog ADD COLUMN IF NOT EXISTS version TEXT;
    ```

- [x] **Apply Release Ledger Migration**
   - Run migration `004_create_app_release_ledger.sql` against your Supabase database
   - This creates authoritative cloud version ledger table: `app_release_ledger`
   - Command (via Supabase dashboard):
      ```sql
      -- run full migration file migrations/004_create_app_release_ledger.sql
      ```

- [x] **Test in Local Environment**
  - Set `environment: "local"` in `.streamlit/secrets.toml`
  - Run: `streamlit run home_sync.py`
  - Navigate to "📝 Master Ecosystem Backlog" tab (developer role required)

## Feature Testing Checklist

### Add New Ticket Form
- [x] All field labels appear correctly (Feature, Description, Work Notes)
- [x] "Global" appears in Target App dropdown alongside home_sync and get_fit
- [x] Form clears after submission
- [x] New items appear in the backlog

### Edit Ticket Form
- [x] Can load existing items for editing
- [x] work_notes field displays and saves
- [x] Global target app selection works
- [x] Description and Public Message fields separate properly

### Release Management
- [x] "Cut Release" button appears when items are Staged
- [x] Staged item count displays correctly
- [x] Clicking "Cut Release" updates items with version and release_date
- [x] Cutting release writes app versions to app_release_ledger
- [x] Current version preview reads from app_release_ledger

### Display Layout
- [x] Description field shows (external-facing notes)
- [x] Work Notes field shows (internal notes)
- [x] Released items show version tag with format "🏷️ Released as v2.1.0"
- [x] Global section appears in app groupings

## Version Calculation Test Cases

Test with different category combinations in Staged items:

- [x] Single Core item → version bumps MAJOR (e.g., 1.0.0 → 2.0.0)
- [x] Single UI item → version bumps MINOR (e.g., 1.0.0 → 1.1.0)
- [x] Single Bug item → version bumps PATCH (e.g., 1.0.0 → 1.0.1)
- [x] Core + UI + Bug → uses MAJOR only (e.g., 1.2.3 → 2.0.0)
- [x] UI + Bug → uses MINOR only (e.g., 1.0.0 → 1.1.0)

## Cross-App Verification

- [x] Global items appear in both home_sync and get_fit_together backlog (if viewing shared backlog)
- [x] Version numbers match between apps (since they share backlog table)
- [x] App-specific items (home_sync or get_fit) filter correctly

## Performance Checks

- [x] Backlog loads quickly with 50+ items
- [x] Sorting by Status → Category → Priority works correctly
- [x] Cut Release completes in <5 seconds

## Deployment Readiness

- [x] All syntax errors cleared (mcp_provides_tool_pylanceFileSyntaxErrors shows no errors)
- [x] imports are correct (utils, cut_release in home_sync.py)
- [x] database.py imports datetime and zoneinfo
- [x] No circular import issues

## Post-Deployment

- [ ] Test production (environment: "production")
- [ ] Create first test release with a backlog item
- [ ] Verify version appears in Done items
- [ ] Document new workflow for team

## Cross app changes testing

- [ ] Create three staged backlog items in Home Sync:
   - one with `app_name = home_sync`
   - one with `app_name = get_fit`
   - one with `app_name = Global`
- [ ] Add a `public_message` to each staged item so release notes can render user-facing text.
- [ ] In Home Sync Release Management, confirm Current -> Next previews update correctly for both apps.
- [ ] Cut `Home Sync` release only:
   - verify only Home Sync-targeted staged items are moved to Done
   - verify `app_release_ledger` gets a new `home_sync` row only
- [ ] Cut `Get Fit Together` release only:
   - verify only Get Fit-targeted staged items are moved to Done
   - verify `app_release_ledger` gets a new `get_fit` row only
- [ ] Cut `All Apps` release with at least one Global staged item:
   - verify Global items are moved to Done
   - verify both `home_sync` and `get_fit` ledger rows are written
- [ ] Validate release notes feed in Get Fit Together:
   - newest 3 versions appear directly
   - older versions appear only in closed expander
   - Global items are visible and labeled in the release feed
- [ ] Validate Home Sync backlog view still shows Global/app grouping and inline editing behavior.
- [ ] Run a final DB sanity check:
   - `backlog` has no unintended staged leftovers
   - `app_release_ledger` latest versions match expected values for both apps

## Troubleshooting

If features don't appear:

1. **work_notes field not showing**
   - Check if migration was applied to Supabase
   - Verify column exists: SELECT * FROM backlog LIMIT 1;

2. **"Global" option not in dropdown**
   - Check home_sync.py lines ~675-680
   - Verify target_app = c4.selectbox includes "Global"

3. **Cut Release button not working**
   - Check user_role is "developer"
   - Verify cut_release function exists in database.py
   - Check for Supabase connection errors
   - Verify `app_release_ledger` table exists (migration 004 applied)

4. **Version not calculated correctly**
   - Verify categories match exactly: "Core", "UI", "Bug", "Ops" (case-sensitive)
   - Check calculate_next_version logic in utils.py
   - Ensure Staged items exist before cutting release

## Files to Review Before Deployment

1. `utils.py` - New file, check imports are available
2. `database.py` - Added imports and cut_release function
3. `home_sync.py` - Updated imports and backlog section
4. `migrations/003_add_backlog_release_management.sql` - Review schema changes
5. `migrations/004_create_app_release_ledger.sql` - Review release ledger schema

## Quick Reference: Status Workflow

```
Backlog → In Progress → [Blocked] → Staged → [Cut Release] → Done
```

## Support Commands

If you need to troubleshoot:

```python
# Check Supabase connection
from database import supabase
result = supabase.table("backlog").select("*").limit(1).execute()
print(result.data)

# Check calculate_next_version
from utils import calculate_next_version
new_version = calculate_next_version("1.0.0", ["Core"])
print(new_version)  # Should print: 2.0.0

# Check staged items
from database import get_all_backlog_items
items = get_all_backlog_items()
staged = [i for i in items if i.get("status") == "Staged"]
print(f"Staged items: {len(staged)}")
```

---

**Expected Outcome After Deployment:**
- Global backlog management across home_sync and get_fit_together
- Automatic semantic versioning for releases
- Separate tracking of Description vs Work Notes
- Streamlined release workflow (Cut Release)
