import streamlit as st

from database import (
    get_household_users_for_admin,
    home_mgmt_permissions_available,
    update_user_module_permissions,
)
from home_management_module import HOME_MGMT_PERMISSIONS, HOME_MGMT_MODULE_ORDER
from ui_helpers import rerun_with_reason

MODULE_ACCESS_PAGE_KEY = "show_module_access_page"

BUDGET_PERMISSION_ROWS = (
    {
        "label": "Projects",
        "view_key": "can_view_projects",
        "edit_key": "can_edit_projects",
        "legacy_view": "can_view_budget",
        "legacy_edit": "can_view_budget",
    },
)


def is_module_access_page_active() -> bool:
    return bool(st.session_state.get(MODULE_ACCESS_PAGE_KEY))


def open_module_access_page() -> None:
    st.session_state[MODULE_ACCESS_PAGE_KEY] = True


def close_module_access_page() -> None:
    st.session_state.pop(MODULE_ACCESS_PAGE_KEY, None)


def _can_manage_module_access() -> bool:
    return st.session_state.get("user_role", "member") in ["admin", "developer"]


def _clear_admin_widget_state(user_id: str) -> None:
    suffix = f"_{user_id}"
    for key in list(st.session_state.keys()):
        if key.startswith("admin_") and key.endswith(suffix):
            del st.session_state[key]


def _normalize_view_edit_pair(can_view: bool, can_edit: bool) -> tuple[bool, bool]:
    if can_edit and not can_view:
        can_view = True
    if not can_view:
        can_edit = False
    return can_view, can_edit


def _current_bool(user: dict, key: str, fallback_key: str | None = None, default: bool = False) -> bool:
    if fallback_key:
        return bool(user.get(key, user.get(fallback_key, default)))
    return bool(user.get(key, default))


def _render_section_header(label: str) -> None:
    st.markdown(
        f'<p style="margin:0.6rem 0 0.2rem;font-size:0.78rem;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.06em;opacity:0.6;">{label}</p>',
        unsafe_allow_html=True,
    )


def _render_view_edit_row(
    *,
    label: str,
    user_id: str,
    view_key: str,
    edit_key: str,
    can_view: bool,
    can_edit: bool,
) -> tuple[bool, bool]:
    """Render a View + Edit toggle pair with module label embedded for mobile clarity."""
    new_view = st.toggle(
        f"View — {label}",
        value=can_view,
        key=f"admin_{view_key}_{user_id}",
    )
    new_edit = st.toggle(
        f"Edit — {label}",
        value=can_edit,
        key=f"admin_{edit_key}_{user_id}",
        disabled=not new_view,
    )
    return _normalize_view_edit_pair(new_view, new_edit)


def _build_permission_updates(user: dict, user_id: str, include_home: bool) -> dict:
    updates = {}
    legacy_view = bool(user.get("can_view_budget", False))

    for row in BUDGET_PERMISSION_ROWS:
        view_key = row["view_key"]
        edit_key = row["edit_key"]
        fallback_view = row.get("legacy_view")
        fallback_edit = row.get("legacy_edit")

        current_view = _current_bool(user, view_key, fallback_view, legacy_view)
        current_edit = _current_bool(user, edit_key, fallback_edit, legacy_view if fallback_edit else False)

        new_view = st.session_state.get(f"admin_{view_key}_{user_id}", current_view)
        new_edit = st.session_state.get(f"admin_{edit_key}_{user_id}", current_edit)
        new_view, new_edit = _normalize_view_edit_pair(bool(new_view), bool(new_edit))

        if new_view != current_view:
            updates[view_key] = new_view
        if new_edit != current_edit:
            updates[edit_key] = new_edit

    wishlist_rows = (
        ("can_view_wishlist_members", True),
        ("can_view_wishlist_admin", False),
    )
    for key, default in wishlist_rows:
        current = bool(user.get(key, default))
        new_val = bool(st.session_state.get(f"admin_{key}_{user_id}", current))
        if new_val != current:
            updates[key] = new_val

    if include_home:
        for module_key in HOME_MGMT_MODULE_ORDER:
            perm = HOME_MGMT_PERMISSIONS[module_key]
            view_key = perm["view_key"]
            edit_key = perm["edit_key"]
            current_view = bool(user.get(view_key, False))
            current_edit = bool(user.get(edit_key, False))

            new_view = st.session_state.get(f"admin_{view_key}_{user_id}", current_view)
            new_edit = st.session_state.get(f"admin_{edit_key}_{user_id}", current_edit)
            new_view, new_edit = _normalize_view_edit_pair(bool(new_view), bool(new_edit))

            if new_view != current_view:
                updates[view_key] = new_view
            if new_edit != current_edit:
                updates[edit_key] = new_edit

    if "can_view_projects" in updates:
        projects_view = updates.get(
            "can_view_projects",
            bool(user.get("can_view_projects", legacy_view)),
        )
        monthly_view = bool(user.get("can_view_monthly_budget", legacy_view))
        rollup = bool(projects_view or monthly_view)
        if rollup != bool(user.get("can_view_budget", False)):
            updates["can_view_budget"] = rollup

    return updates


def render_admin_sidebar_entry() -> None:
    """Compact sidebar shortcut to the main-page admin view."""
    if not _can_manage_module_access():
        return

    st.divider()
    if st.button("⚙️ Admin Settings", key="sidebar_open_admin", width="stretch"):
        open_module_access_page()
        rerun_with_reason("admin_nav")


def render_admin_module_access_page() -> None:
    """Full-width module access editor for household members."""
    if not _can_manage_module_access():
        st.warning("You do not have permission to manage module access.")
        return

    reset_user_id = st.session_state.pop("admin_perm_pending_reset_user_id", None)
    if reset_user_id:
        _clear_admin_widget_state(reset_user_id)

    if st.button("⬅️ Back to Dashboard", key="admin_module_access_back"):
        close_module_access_page()
        rerun_with_reason("admin_nav_back")

    st.subheader("🔐 Module Access")
    st.caption(
        "Grant household members view and edit access by module. "
        "Changes apply when you save."
    )

    save_notice = st.session_state.pop("admin_perm_save_notice", None)
    save_notice_level = st.session_state.pop("admin_perm_save_notice_level", "success")
    if save_notice:
        if save_notice_level == "error":
            st.error(save_notice)
        elif save_notice_level == "info":
            st.info(save_notice)
        else:
            st.success(save_notice)

    household_users = get_household_users_for_admin()
    members = [u for u in household_users if u.get("role") not in ["admin", "developer"]]

    if not members:
        st.info("No standard members found in this household.")
        return

    home_mgmt_ready = home_mgmt_permissions_available()
    user_role = st.session_state.get("user_role", "member")

    summary_col, member_col = st.columns([1.2, 2])
    with summary_col:
        st.metric("Members", len(members))
    with member_col:
        member_options = {u["username"]: u for u in members}
        selected_username = st.selectbox(
            "Editing permissions for",
            options=list(member_options.keys()),
            key="admin_perm_member_select",
        )

    user = member_options[selected_username]
    user_id = user["auth_user_id"]
    legacy_view = bool(user.get("can_view_budget", False))

    with st.container(border=True):
        st.markdown(f"#### {selected_username}")
        st.caption("Toggle access below, then save. Edit is only available when View is enabled.")

        with st.form(key=f"admin_module_access_form_{user_id}", clear_on_submit=False):
            with st.expander("Budget", expanded=True):
                _render_section_header("Projects")
                for row in BUDGET_PERMISSION_ROWS:
                    view_key = row["view_key"]
                    edit_key = row["edit_key"]
                    current_view = _current_bool(user, view_key, row.get("legacy_view"), legacy_view)
                    current_edit = _current_bool(
                        user,
                        edit_key,
                        row.get("legacy_edit"),
                        legacy_view if row.get("legacy_edit") else False,
                    )
                    _render_view_edit_row(
                        label=row["label"],
                        user_id=user_id,
                        view_key=view_key,
                        edit_key=edit_key,
                        can_view=current_view,
                        can_edit=current_edit,
                    )

                st.divider()
                _render_section_header("Wish List")
                st.toggle(
                    "Wish List — Members",
                    value=bool(user.get("can_view_wishlist_members", True)),
                    key=f"admin_can_view_wishlist_members_{user_id}",
                )
                st.toggle(
                    "Wish List — Admin",
                    value=bool(user.get("can_view_wishlist_admin", False)),
                    key=f"admin_can_view_wishlist_admin_{user_id}",
                )

            if home_mgmt_ready:
                with st.expander("Home Management", expanded=False):
                    for idx, module_key in enumerate(HOME_MGMT_MODULE_ORDER):
                        perm = HOME_MGMT_PERMISSIONS[module_key]
                        if idx > 0:
                            st.divider()
                        _render_section_header(perm["label"])
                        view_key = perm["view_key"]
                        edit_key = perm["edit_key"]
                        current_view = bool(user.get(view_key, False))
                        current_edit = bool(user.get(edit_key, False))
                        _render_view_edit_row(
                            label=perm["label"],
                            user_id=user_id,
                            view_key=view_key,
                            edit_key=edit_key,
                            can_view=current_view,
                            can_edit=current_edit,
                        )
            elif user_role == "developer":
                st.caption(
                    "Home Management permissions require migration 015 "
                    "(migrations/015_add_home_management_permissions.sql)."
                )

            save_col, reset_col = st.columns([1, 3])
            with save_col:
                submitted = st.form_submit_button("Save permissions", type="primary", width="stretch")
            with reset_col:
                reset_clicked = st.form_submit_button("Reset form", width="stretch")

        if reset_clicked:
            st.session_state["admin_perm_pending_reset_user_id"] = user_id
            rerun_with_reason("admin_perm_reset")

        if submitted:
            updates = _build_permission_updates(user, user_id, include_home=home_mgmt_ready)

            if not updates:
                st.session_state["admin_perm_save_notice"] = f"No changes to save for {selected_username}."
                st.session_state["admin_perm_save_notice_level"] = "info"
            elif update_user_module_permissions(user_id, updates):
                st.session_state["admin_perm_save_notice"] = (
                    f"Saved permissions for {selected_username}."
                )
                st.session_state["admin_perm_save_notice_level"] = "success"
            else:
                st.session_state["admin_perm_save_notice"] = (
                    f"Failed to save permissions for {selected_username}. Please try again."
                )
                st.session_state["admin_perm_save_notice_level"] = "error"
            rerun_with_reason("admin_perm_save")
