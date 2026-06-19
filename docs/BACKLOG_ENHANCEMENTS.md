# Enhanced Backlog Management System

## Overview

This document describes the new backlog management features added to home-sync, ported from and enhanced based on get-fit-together's versioning system.

## Key Features Added

### 1. **Global/Ecosystem Target App Option**
- **New Target App options:** `home_sync`, `get_fit`, `Global`
- **Purpose:** Allows you to create backlog items that apply across your entire app ecosystem
- **Use Case:** When a feature or fix needs to be implemented in multiple apps, mark it as "Global" instead of duplicating it
- **UI Location:** Target App dropdown in both "Add New Ticket" and "Edit Ticket" forms

### 2. **Separate Description and Work Notes Fields**
- **Description (notes):** External-facing description of the feature/fix (visible to users)
- **Work Notes (work_notes):** Internal implementation notes and technical details
- **Benefit:** Keeps public-facing release notes separate from internal implementation details
- **UI Layout:**
  ```
  Feature Name
  Status | Category | Priority
  Description
  Work Notes
  Public Message
  Version (if released)
  ```

### 3. **Semantic Versioning**
- **Automatic Version Calculation** based on item categories:
  - `Core` changes → MAJOR version bump (e.g., 1.0.0 → 2.0.0)
  - `UI` changes → MINOR version bump (e.g., 1.0.0 → 1.1.0)
  - `Bug` fixes → PATCH version bump (e.g., 1.0.0 → 1.0.1)
  - `Ops` items → No version bump

- **Intelligent Bumping:**
  - Higher priority changes reset lower priority numbers to 0
  - Example: 1.2.3 + Core changes = 2.0.0 (not 2.2.3)
  - Never includes multiple bump types (always uses the highest priority)

### 4. **Dual-Button Release System**

#### **Button 1: 💾 Save Daily Work (Keep Staged)**
- **Action:** Saves all changes to backlog items
- **Does NOT:** Change version, release date, or move items to Done
- **Use Case:** End-of-day progress checkpoint without committing to a release
- **Workflow:**
  ```
  Make edits → Click "Save Daily Work" → Changes saved, items stay Staged
  ```

#### **Button 2: 🚀 Cut Release & Move Staged to Done**
- **Action:** 
  1. Scans all "Staged" items for their categories
  2. Calculates appropriate version bump
  3. Stamps `release_date` (today's date in Chicago timezone)
  4. Stamps `version` (calculated semantic version)
  5. Moves all Staged items to "Done" status
- **Use Case:** Official production release
- **Workflow:**
  ```
  Staged items ready? → Click "Cut Release" → Version auto-calculated and items released
  → Run your deployment script with new version number
  ```

## New Database Columns

The following columns were added to the `backlog` table:

| Column | Type | Description |
|--------|------|-------------|
| `work_notes` | TEXT | Internal implementation notes |
| `release_date` | DATE | Date the item was released to production (ISO format) |
| `version` | TEXT | Semantic version when released (e.g., "1.2.3") |

## Status Workflow

```
Backlog
  ↓
In Progress
  ↓
Blocked (optional, if stuck)
  ↓
Staged (ready for release)
  ↓ [Click "Cut Release"]
Done (released with version stamp)
```

- **Items in "Staged" status** are candidates for the next release
- **Items in "Done" status** have been released and have a version number
- **Items in other statuses** are not displayed in the main backlog view

## Usage Examples

### Example 1: Add a Core Feature

1. Go to "📝 Master Ecosystem Backlog" tab (developer-only)
2. Click "➕ Add New Backlog Ticket"
3. Fill in:
   - Status: `Backlog`
   - Category: `Core`
   - Priority: `High`
   - Target App: `home_sync`
   - Feature: "Implement multi-tenant auth"
   - Description: "Add support for multiple household accounts"
   - Work Notes: "Requires refactoring user_sessions table"

### Example 2: Cut a Release

1. Move several items to `Staged` status
2. Edit each item to fill in accurate categories:
   - 2 items with `Core` category
   - 1 item with `UI` category
   - 0 items with `Bug` category
3. Below the edit form, see "📦 2 item(s) ready for release"
4. Click "🚀 Cut Release & Move Staged to Done"
5. System calculates: Since "Core" is in the release, bump MAJOR → 1.0.0 → 2.0.0
6. All Staged items get `release_date` = today and `version` = "2.0.0"
7. All Staged items move to `Done` status
8. UI displays: "✅ Release 2.0.0 Cut! 2 items moved to Done."
9. Next step: Update APP_VERSION in your code and run deployment

### Example 3: Global/Ecosystem Item

1. Create a ticket with Target App = `Global`
2. Title: "Refactor to use Supabase v2 client"
3. Since it affects both home_sync and get_fit, mark it as Global
4. When released, it appears in the "Global" section of your backlog
5. Reference this ticket from both apps' changelogs

## Integration with get-fit-together

Both apps now share:
- Same `backlog` table (with `app_name` to differentiate)
- Same versioning logic via `calculate_next_version()` utility function
- Same release workflow (Backlog → Staged → Done)
- Same semantic versioning rules

**Difference:** get-fit-together has more sophisticated UI/UX for the Admin Panel, but the core logic is identical across both systems.

## Version Calculation Examples

| Current | Release Contains | New Version | Notes |
|---------|------------------|-------------|-------|
| 1.0.0 | Core | 2.0.0 | Major bump |
| 1.0.0 | UI | 1.1.0 | Minor bump |
| 1.0.0 | Bug | 1.0.1 | Patch bump |
| 1.2.3 | Core, UI, Bug | 2.0.0 | Takes highest priority (Core) |
| 1.2.3 | UI, Bug | 1.3.0 | Takes highest priority (UI) |
| 1.2.3 | Bug | 1.2.4 | Patch bump |
| 2.0.0 | (none, Ops only) | 2.0.0 | No bump if only Ops |

## Troubleshooting

### Q: "No staged items found" error when cutting release
**A:** You need to move backlog items to "Staged" status before cutting a release. The release system only processes Staged items.

### Q: Version didn't bump as expected
**A:** Check that your items have the correct `category` value. Valid categories are: `Core`, `UI`, `Bug`, `Ops`. Version calculation uses the highest-priority category present.

### Q: Can I release items across multiple apps at once?
**A:** Yes! Set Target App to `Global` for cross-app features, then use the same release system. The version applies to both apps' release notes.

### Q: How do I see released items?
**A:** Released items (status = "Done") are hidden from the main backlog view. They appear with a version tag like "🏷️ Released as v2.1.0" and are sorted by app section.

## Future Enhancements

Potential improvements to consider:
1. Changelog generation from backlog items
2. Release notes auto-generation from public_message field
3. Git tag auto-creation matching version numbers
4. Backlog analytics (velocity, release frequency)
5. Cross-app dependency tracking

## Files Modified

- `utils.py` - NEW: Added `calculate_next_version()` function
- `database.py` - Updated backlog functions to support new columns
- `home_sync.py` - Enhanced backlog UI with new features
- `migrations/003_add_backlog_release_management.sql` - NEW: Schema changes

## Related Functions

### In `database.py`:
- `add_backlog_item()` - Now accepts `work_notes` parameter
- `update_backlog_item()` - Now accepts `work_notes` parameter
- `cut_release()` - NEW: Handles release cutting logic

### In `utils.py`:
- `calculate_next_version()` - NEW: Semantic versioning calculation

### In `home_sync.py`:
- Backlog tab (tab6) - Completely revamped with new UI
