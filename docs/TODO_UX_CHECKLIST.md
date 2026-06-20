# To-Do UX Release Checklist

Use this quick pass before pushing task UI/UX changes.

## 1) App Smoke
- [ ] Open the app and go to the To-Do view.
- [ ] Confirm there are no Streamlit warnings/errors in terminal while interacting.
- [ ] Confirm household scoping looks correct for the logged-in user.

## 2) Card Interaction
- [ ] Tap/click a full-width task card once: inline edit opens.
- [ ] Tap/click the same task card again: inline edit closes.
- [ ] Tap/click a different task card: edit focus switches to that card.

## 3) Task Row Readability
- [ ] Task title is clearly visible.
- [ ] Status/category/due metadata appears directly below the title.
- [ ] Notes line appears only when notes exist.
- [ ] No description field is shown in task cards.

## 4) Mobile Layout
- [ ] On narrow mobile width, task cards remain full-width and readable.
- [ ] Edit/open interaction remains usable without layout overlap.
- [ ] Notification strip remains readable (horizontal, informational only).

## 5) Edit Form
- [ ] Save updates task title/priority/category/assignees/date/notes.
- [ ] Cancel closes edit mode without saving.
- [ ] Delete removes the task.
- [ ] Complete marks task done from the form.

## 6) Recurrence Behavior
- [ ] Create recurring tasks with: Daily, Weekly, Biweekly, Monthly, Quarterly, Every 6 Months, Yearly.
- [ ] Complete a recurring task and confirm the next occurrence is created.
- [ ] Confirm non-recurring task completion does not create a follow-up task.

## 7) Role Checks
- [ ] Member only sees permitted tasks.
- [ ] Developer/Admin can open inline edit and use all task actions.
- [ ] Notification summary is shown only for Developer/Admin.

## 8) Recently Completed
- [ ] Recently completed list shows expected items.
- [ ] Recall restores selected task to active.
- [ ] Older completed tasks remain out of the recent window.

## 9) Regression Commands
- [ ] Run unit tests:
  - `python -m unittest -q tests.test_auth_database`
- [ ] Run app locally:
  - `python -m streamlit run home_sync.py`

## 10) Pre-Push Safety
- [ ] Confirm no accidental schema dependency on removed `description` UI field.
- [ ] If schema changed, migration is prepared and documented.
- [ ] Final manual pass completed on desktop + mobile.
