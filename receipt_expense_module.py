"""Scan Receipt tab for the Quick Expense overlay.

UI flow:
  1. Upload — file picker + optional receipt date override + draft list
  2. Extract — OCR call (spinner); manual fallback on failure
  3. Review & Post — per-line form with ledger target, category picker,
     inline category creation, and Save Draft / Post actions
"""

from __future__ import annotations

import streamlit as st
from datetime import date, datetime
from zoneinfo import ZoneInfo

from database import (
    create_receipt_upload,
    delete_receipt_upload,
    download_receipt_file_bytes,
    get_draft_receipt_uploads,
    get_receipt_file_url,
    get_receipt_line_items,
    get_receipt_upload,
    post_all_receipt_lines,
    update_receipt_upload,
    upload_receipt_file_to_storage,
    upsert_receipt_line_items,
    get_budget_categories,
    get_project_budgets,
    get_member_obligation_expense_categories,
    insert_budget_category,
    insert_obligation_subcategory,
    ensure_personal_taxes_category,
    ensure_household_taxes_category,
    ensure_personal_uncategorized_category,
    ensure_household_uncategorized_category,
    _can_edit_monthly_budget_server_side,
    _can_edit_projects_server_side,
    _is_budget_privileged,
)
from receipt_ocr import ReceiptOcrError, extract_receipt_data_or_raise, render_pdf_preview_png

# Session-state keys
_ACTIVE_RECEIPT_KEY = "scan_receipt_active_id"
_LINES_KEY = "scan_receipt_lines"
_ALLOW_UNCATEGORIZED_KEY = "scan_receipt_allow_uncategorized"

LEDGER_OPTIONS = ["personal", "hh_obligation", "hh_shared", "project"]
LEDGER_LABELS = {
    "personal": "Personal",
    "hh_obligation": "HH Obligation",
    "hh_shared": "HH Shared",
    "project": "Project",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_scan_receipt_tab(household_id: str, auth_user_id: str, username: str) -> None:
    """Render the Scan Receipt tab content."""
    st.caption(
        "Upload a receipt or invoice image/PDF. Each line can be posted to your "
        "Personal ledger, a Household Obligation, the shared HH budget, or a Project."
    )
    st.info(
        "Receipt images are sent to Gemini (same provider as Get Fit Together) "
        "for text extraction. Do not upload documents containing passwords or "
        "unrelated sensitive data.",
        icon="ℹ️",
    )

    active_id = st.session_state.get(_ACTIVE_RECEIPT_KEY)

    if active_id:
        # Resume existing receipt
        receipt = get_receipt_upload(active_id)
        if receipt is None:
            st.session_state.pop(_ACTIVE_RECEIPT_KEY, None)
            st.session_state.pop(_LINES_KEY, None)
            st.rerun()
        _render_review_screen(receipt, household_id, auth_user_id, username)
        return

    # --- Draft list ---
    _render_draft_list(household_id, username)

    # --- Upload form ---
    _render_upload_form(household_id, auth_user_id, username)


# ---------------------------------------------------------------------------
# Draft list
# ---------------------------------------------------------------------------

def _render_draft_list(household_id: str, username: str) -> None:
    drafts = get_draft_receipt_uploads(household_id, username)
    if not drafts:
        return

    st.markdown("**Resume a draft receipt:**")
    for d in drafts:
        created = str(d.get("created_at") or "")[:10]
        merchant = d.get("merchant") or d.get("file_name") or "Receipt"
        label = f"{merchant}  ({created})"
        col_label, col_resume, col_del = st.columns([3, 1, 1])
        col_label.write(label)
        if col_resume.button("Resume", key=f"resume_{d['id']}"):
            st.session_state[_ACTIVE_RECEIPT_KEY] = d["id"]
            st.session_state.pop(_LINES_KEY, None)
            st.rerun()
        if col_del.button("🗑", key=f"del_{d['id']}", help="Archive this draft"):
            delete_receipt_upload(d["id"])
            st.rerun()

    st.divider()


# ---------------------------------------------------------------------------
# Upload form
# ---------------------------------------------------------------------------

def _render_upload_form(household_id: str, auth_user_id: str, username: str) -> None:
    with st.form("scan_receipt_upload_form", clear_on_submit=False):
        uploaded_file = st.file_uploader(
            "Receipt image or PDF",
            type=["jpg", "jpeg", "png", "webp", "pdf"],
            help="Max ~20 MB. Images work best for OCR.",
        )
        receipt_date = st.date_input("Receipt date", value=date.today())
        submitted = st.form_submit_button("📷 Upload & Extract", type="primary", width="stretch")

    if not submitted or uploaded_file is None:
        return

    file_bytes = uploaded_file.read()
    mime_type = uploaded_file.type or "application/octet-stream"
    file_name = uploaded_file.name

    # Create the DB row first (get an ID for the storage path)
    receipt_id = create_receipt_upload(household_id, file_name, mime_type)
    if not receipt_id:
        st.error("Failed to create receipt record. Please try again.")
        return

    # Upload to storage
    with st.spinner("Uploading…"):
        storage_path = upload_receipt_file_to_storage(
            file_bytes, file_name, mime_type, household_id, receipt_id
        )
        if storage_path:
            update_receipt_upload(receipt_id, storage_path=storage_path)

    # OCR extraction
    lines: list[dict] = []
    merchant = None
    total_amount = None
    ocr_status = "failed"
    ocr_message = None

    with st.spinner("Extracting receipt data…"):
        try:
            result = extract_receipt_data_or_raise(file_bytes, mime_type)
            merchant = result.get("merchant")
            total_amount = result.get("total")
            ocr_status = "done"
            for i, ln in enumerate(result.get("lines") or []):
                lines.append({
                    "line_index": i,
                    "description": str(ln.get("description") or "").strip(),
                    "line_amount": float(ln["amount"]) if ln.get("amount") is not None else None,
                    "ledger_target": "personal",
                    "category_id": None,
                    "project_budget_id": None,
                    "status": "draft",
                })
        except ReceiptOcrError as exc:
            ocr_message = str(exc)
            print(f"OCR failed for receipt {receipt_id}: {exc}")
        except Exception as exc:
            ocr_message = f"OCR failed unexpectedly: {exc}"
            print(f"OCR failed for receipt {receipt_id}: {exc}")

    # Persist OCR results
    update_fields: dict = {"ocr_status": ocr_status}
    if merchant:
        update_fields["merchant"] = merchant
    if total_amount is not None:
        update_fields["total_amount"] = total_amount
    if receipt_date:
        update_fields["receipt_date"] = receipt_date.isoformat()
    update_receipt_upload(receipt_id, **update_fields)

    if not lines:
        if ocr_message:
            st.warning(ocr_message)
        else:
            st.warning(
                "OCR could not extract line items. "
                "Add lines manually in the review screen below."
            )
        lines = [_blank_line(0)]

    upsert_receipt_line_items(receipt_id, lines)

    st.session_state[_ACTIVE_RECEIPT_KEY] = receipt_id
    st.session_state[_LINES_KEY] = lines
    st.rerun()


# ---------------------------------------------------------------------------
# Review screen
# ---------------------------------------------------------------------------

def _render_review_screen(
    receipt: dict,
    household_id: str,
    auth_user_id: str,
    username: str,
) -> None:
    receipt_id = receipt["id"]

    if st.button("← Back to Upload", key="scan_back_btn"):
        st.session_state.pop(_ACTIVE_RECEIPT_KEY, None)
        st.session_state.pop(_LINES_KEY, None)
        st.rerun()

    # --- Header info ---
    st.markdown("#### Review Receipt")
    col_a, col_b, col_c = st.columns(3)
    merchant = col_a.text_input(
        "Merchant", value=receipt.get("merchant") or "", key=f"merchant_{receipt_id}"
    )
    raw_date = receipt.get("receipt_date")
    default_date = date.fromisoformat(str(raw_date)) if raw_date else date.today()
    rcpt_date = col_b.date_input("Date", value=default_date, key=f"rdate_{receipt_id}")
    total_str = col_c.text_input(
        "Total ($)",
        value=str(receipt.get("total_amount") or ""),
        key=f"rtotal_{receipt_id}",
    )

    # Receipt preview (image or first page of PDF)
    storage_path = receipt.get("storage_path")
    mime_type = receipt.get("mime_type") or ""
    if storage_path:
        with st.expander("Receipt preview", expanded=False):
            if mime_type.startswith("image/"):
                url = get_receipt_file_url(storage_path)
                if url:
                    st.image(url)
                else:
                    st.caption("Could not load receipt image preview.")
            elif mime_type == "application/pdf" or str(receipt.get("file_name", "")).lower().endswith(".pdf"):
                pdf_bytes = download_receipt_file_bytes(storage_path)
                if pdf_bytes:
                    preview_png = render_pdf_preview_png(pdf_bytes)
                    if preview_png:
                        st.image(preview_png, caption="Page 1")
                    else:
                        st.caption("Could not render PDF preview.")
                    url = get_receipt_file_url(storage_path)
                    if url:
                        st.link_button("Open original PDF", url)
                else:
                    st.caption("Could not download receipt file for preview.")

    # --- Load lines from DB or session cache ---
    if _LINES_KEY not in st.session_state:
        st.session_state[_LINES_KEY] = get_receipt_line_items(receipt_id)
    lines: list[dict] = st.session_state[_LINES_KEY]

    # Filter out already-posted lines for editing
    editable = [ln for ln in lines if ln.get("status") != "posted"]
    posted_count = len(lines) - len(editable)

    if posted_count > 0:
        st.caption(f"{posted_count} line(s) already posted.")

    # Build permission-aware ledger options for this user
    allowed_targets = _allowed_ledger_targets(household_id, username)

    # Build category caches
    personal_cats = _load_personal_cats(household_id, username)
    hh_cats = _load_hh_cats(household_id)
    obl_cats = _load_obligation_cats(household_id, username)
    projects = _load_projects()

    # Allow-uncategorized toggle
    if _ALLOW_UNCATEGORIZED_KEY not in st.session_state:
        st.session_state[_ALLOW_UNCATEGORIZED_KEY] = True
    allow_uncat = st.toggle(
        "Allow uncategorized posting",
        key=_ALLOW_UNCATEGORIZED_KEY,
        help=(
            "When enabled, lines without a category are posted to "
            "'Receipt / Uncategorized'. Disable to require a category for every line."
        ),
    )

    st.divider()

    # Render per-line widgets
    updated_lines = []
    for i, ln in enumerate(editable):
        updated = _render_line_editor(
            line=ln,
            index=i,
            receipt_id=receipt_id,
            allowed_targets=allowed_targets,
            personal_cats=personal_cats,
            hh_cats=hh_cats,
            obl_cats=obl_cats,
            projects=projects,
            household_id=household_id,
            username=username,
        )
        updated_lines.append(updated)

    # Add line button
    if st.button("＋ Add line", key="add_receipt_line"):
        updated_lines.append(_blank_line(len(updated_lines)))
        st.session_state[_LINES_KEY] = updated_lines
        st.rerun()

    st.divider()

    # --- Actions ---
    col_save, col_post = st.columns(2)
    if col_save.button("💾 Save Draft", key="save_receipt_draft", width="stretch"):
        # Persist header updates
        _save_header(receipt_id, merchant, rcpt_date, total_str)
        upsert_receipt_line_items(receipt_id, updated_lines)
        st.session_state[_LINES_KEY] = updated_lines
        st.success("Draft saved.")

    if col_post.button("✅ Post to Ledger", key="post_receipt_lines", type="primary", width="stretch"):
        _save_header(receipt_id, merchant, rcpt_date, total_str)
        upsert_receipt_line_items(receipt_id, updated_lines)
        # Re-fetch lines (they now have DB ids for _mark_line_posted)
        fresh_lines = get_receipt_line_items(receipt_id)
        result = post_all_receipt_lines(
            receipt_id,
            fresh_lines,
            receipt_date=rcpt_date,
            household_id=household_id,
            allow_uncategorized=allow_uncat,
        )
        for msg in result["messages"]:
            if msg.startswith("Failed") or msg.startswith("No ") or msg.startswith("Category"):
                st.error(msg)
            else:
                st.success(msg)
        if result["failed"] == 0 and result["posted"] > 0:
            st.success(f"All {result['posted']} line(s) posted successfully!")
            st.session_state.pop(_ACTIVE_RECEIPT_KEY, None)
            st.session_state.pop(_LINES_KEY, None)
            st.rerun()
        elif result["posted"] > 0:
            # Partial success — refresh lines
            st.session_state[_LINES_KEY] = get_receipt_line_items(receipt_id)
            st.warning(
                f"{result['posted']} posted, {result['failed']} failed. "
                "Review errors above, then post again to retry failed lines."
            )


# ---------------------------------------------------------------------------
# Per-line editor
# ---------------------------------------------------------------------------

def _render_line_editor(
    *,
    line: dict,
    index: int,
    receipt_id: str,
    allowed_targets: list[str],
    personal_cats: list[tuple[str, str]],
    hh_cats: list[tuple[str, str]],
    obl_cats: list[tuple[str, str]],
    projects: list[tuple[str, str]],
    household_id: str,
    username: str,
) -> dict:
    """Render one line-item row. Returns updated line dict."""
    uid = f"line_{receipt_id}_{index}"

    with st.container(border=True):
        col_desc, col_amt, col_tgt = st.columns([3, 1, 1])

        description = col_desc.text_input(
            "Description", value=str(line.get("description") or ""), key=f"{uid}_desc"
        )
        amount_raw = col_amt.text_input(
            "Amount ($)", value=str(line.get("line_amount") or ""), key=f"{uid}_amt"
        )
        try:
            line_amount = float(amount_raw.replace(",", "").replace("$", "").strip())
        except (ValueError, AttributeError):
            line_amount = None

        target_labels = [LEDGER_LABELS.get(t, t) for t in allowed_targets]
        current_target = str(line.get("ledger_target") or "personal")
        default_idx = allowed_targets.index(current_target) if current_target in allowed_targets else 0
        selected_label = col_tgt.selectbox(
            "Ledger", target_labels, index=default_idx, key=f"{uid}_ledger"
        )
        ledger_target = allowed_targets[target_labels.index(selected_label)]

        # Category / project selector
        category_id = line.get("category_id")
        project_budget_id = line.get("project_budget_id")

        if ledger_target == "project":
            project_budget_id = _render_project_picker(uid, projects, project_budget_id)
            category_id = None
        else:
            cat_options = _cat_options_for_target(ledger_target, personal_cats, hh_cats, obl_cats)
            category_id = _render_category_picker(uid, cat_options, category_id)

            # Inline category creation
            with st.expander("Add new category", expanded=False):
                _render_inline_category_create(
                    uid=uid,
                    ledger_target=ledger_target,
                    household_id=household_id,
                    username=username,
                    obl_cats=obl_cats,
                )

    return {
        **line,
        "description": description,
        "line_amount": line_amount,
        "ledger_target": ledger_target,
        "category_id": category_id,
        "project_budget_id": project_budget_id,
        "status": line.get("status", "draft"),
    }


# ---------------------------------------------------------------------------
# Category / project picker helpers
# ---------------------------------------------------------------------------

def _cat_options_for_target(
    ledger_target: str,
    personal_cats: list[tuple[str, str]],
    hh_cats: list[tuple[str, str]],
    obl_cats: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    if ledger_target == "personal":
        return personal_cats
    if ledger_target == "hh_shared":
        return hh_cats
    if ledger_target == "hh_obligation":
        return obl_cats
    return []


def _render_category_picker(
    uid: str,
    cat_options: list[tuple[str, str]],
    current_id: str | int | None,
) -> str | None:
    """Renders a selectbox for (label, id) pairs; returns selected id or None."""
    if not cat_options:
        st.caption("No categories available for this ledger target.")
        return None

    none_label = "— select category —"
    labels = [none_label] + [label for label, _ in cat_options]
    ids = [None] + [cat_id for _, cat_id in cat_options]

    current_idx = 0
    if current_id is not None:
        try:
            current_idx = ids.index(str(current_id))
        except ValueError:
            pass

    chosen = st.selectbox("Category", labels, index=current_idx, key=f"{uid}_cat")
    chosen_idx = labels.index(chosen)
    return ids[chosen_idx]


def _render_project_picker(
    uid: str,
    projects: list[tuple[str, str]],
    current_id: str | None,
) -> str | None:
    if not projects:
        st.caption("No active projects found.")
        return None

    none_label = "— select project —"
    labels = [none_label] + [label for label, _ in projects]
    ids = [None] + [pid for _, pid in projects]

    current_idx = 0
    if current_id is not None:
        try:
            current_idx = ids.index(str(current_id))
        except ValueError:
            pass

    chosen = st.selectbox("Project", labels, index=current_idx, key=f"{uid}_proj")
    chosen_idx = labels.index(chosen)
    return ids[chosen_idx]


# ---------------------------------------------------------------------------
# Inline category creation
# ---------------------------------------------------------------------------

def _render_inline_category_create(
    *,
    uid: str,
    ledger_target: str,
    household_id: str,
    username: str,
    obl_cats: list[tuple[str, str]],
) -> None:
    """Inline mini-form for creating a new category, scoped to ledger_target."""
    if ledger_target == "project":
        st.caption("Projects are managed in the Projects module.")
        return

    if ledger_target == "hh_shared" and not _can_edit_monthly_budget_server_side():
        st.caption("Only admins can create Household categories.")
        return

    parent = st.text_input("Parent category", key=f"{uid}_new_parent")
    sub = st.text_input("Sub-category (optional)", key=f"{uid}_new_sub")
    budget = st.number_input("Monthly budget ($)", min_value=0.0, step=10.0, key=f"{uid}_new_budget")

    if st.button("Create category", key=f"{uid}_create_cat"):
        if not parent.strip():
            st.error("Parent category name is required.")
            return

        if ledger_target == "hh_obligation":
            # Find a valid parent assignment id
            parent_id = None
            for label, cat_id in obl_cats:
                if label.startswith(parent.strip()):
                    parent_id = cat_id
                    break
            if not parent_id:
                st.error("Parent must match one of your assigned obligation categories.")
                return
            ok = insert_obligation_subcategory(
                household_id=household_id,
                username=username,
                parent_category_id=parent_id,
                sub_category_name=sub.strip() or parent.strip(),
                target_budget=budget,
            )
        else:
            is_personal = ledger_target == "personal"
            ok = insert_budget_category(
                household_id,
                parent.strip(),
                sub_category_name=sub.strip() or None,
                is_personal=is_personal,
                username=username if is_personal else None,
                target_budget=budget,
            )

        if ok:
            st.success(f"Category '{parent}' created. Reload to see it in the picker.")
        else:
            st.error("Failed to create category. Check permissions.")


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_personal_cats(household_id: str, username: str) -> list[tuple[str, str]]:
    ensure_personal_taxes_category(household_id, username)
    df = get_budget_categories(household_id, is_personal=True, username=username)
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        label = f"{row.get('category_name', '')} → {row.get('sub_category_name', '')}".strip(" →")
        out.append((label, str(row["id"])))
    return out


def _load_hh_cats(household_id: str) -> list[tuple[str, str]]:
    if not _can_edit_monthly_budget_server_side():
        return []
    ensure_household_taxes_category(household_id)
    df = get_budget_categories(household_id, is_personal=False)
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        label = f"{row.get('category_name', '')} → {row.get('sub_category_name', '')}".strip(" →")
        out.append((label, str(row["id"])))
    return out


def _load_obligation_cats(household_id: str, username: str) -> list[tuple[str, str]]:
    df = get_member_obligation_expense_categories(household_id, username)
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        label = f"{row.get('category_name', '')} → {row.get('sub_category_name', '')}".strip(" →")
        out.append((label, str(row["id"])))
    return out


def _load_projects() -> list[tuple[str, str]]:
    if not _can_edit_projects_server_side() and not _is_budget_privileged():
        return []
    try:
        rows = get_project_budgets()
        if not rows:
            return []
        out = []
        for r in (rows if isinstance(rows, list) else rows.to_dict("records")):
            label = str(r.get("item") or r.get("name") or r.get("id") or "Project")
            pid = str(r.get("id"))
            out.append((label, pid))
        return out
    except Exception:
        return []


def _allowed_ledger_targets(household_id: str, username: str) -> list[str]:
    """Return the subset of ledger targets this user may post to."""
    targets = ["personal"]
    obl = get_member_obligation_expense_categories(household_id, username)
    if obl is not None and not obl.empty:
        targets.append("hh_obligation")
    if _can_edit_monthly_budget_server_side():
        targets.append("hh_shared")
    if _can_edit_projects_server_side() or _is_budget_privileged():
        targets.append("project")
    return targets


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def _blank_line(index: int) -> dict:
    return {
        "line_index": index,
        "description": "",
        "line_amount": None,
        "ledger_target": "personal",
        "category_id": None,
        "project_budget_id": None,
        "status": "draft",
    }


def _save_header(receipt_id: str, merchant: str, rcpt_date, total_str: str) -> None:
    fields: dict = {}
    if merchant.strip():
        fields["merchant"] = merchant.strip()
    if rcpt_date:
        fields["receipt_date"] = rcpt_date.isoformat() if hasattr(rcpt_date, "isoformat") else str(rcpt_date)
    if total_str.strip():
        try:
            fields["total_amount"] = float(total_str.replace(",", "").replace("$", "").strip())
        except ValueError:
            pass
    if fields:
        update_receipt_upload(receipt_id, **fields)
