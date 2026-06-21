import streamlit as st
from database import get_household_users_for_admin, update_user_module_permissions

def render_admin_sidebar_panel():
    """
    Renders the Admin Settings panel inside the sidebar.
    """
    user_role = st.session_state.get("user_role", "member")
    
    # 🔒 Strict check: Only render this UI if the user is an admin or developer
    if user_role in ["admin", "developer"]:
        st.divider()
        
        with st.expander("⚙️ Admin Settings"):
            
            # --- CATEGORY: MODULE ACCESS ---
            st.markdown("### 🔐 Module Access")
            st.caption("Set explicit view/edit permissions for each household member.")

            household_users = get_household_users_for_admin()

            if household_users:
                # Filter the list so we ONLY show standard members
                members = [u for u in household_users if u.get('role') not in ['admin', 'developer']]

                if not members:
                    st.info("No standard members found in this household.")
                else:
                    for u in members:
                        user_id = u["auth_user_id"]
                        st.markdown(f"**👤 {u['username']}**")

                        legacy_view = bool(u.get("can_view_budget", False))
                        current_view_projects = bool(u.get("can_view_projects", legacy_view))
                        current_edit_projects = bool(u.get("can_edit_projects", legacy_view))
                        current_view_monthly = bool(u.get("can_view_monthly_budget", legacy_view))
                        current_edit_monthly = bool(u.get("can_edit_monthly_budget", False))

                        p_mod_col, p_view_label_col, p_view_toggle_col, p_edit_label_col, p_edit_toggle_col = st.columns([2.2, 1, 0.9, 1, 0.9])
                        p_mod_col.markdown("Projects:")
                        p_view_label_col.caption("View")
                        new_view_projects = p_view_toggle_col.toggle(
                            "Projects View",
                            value=current_view_projects,
                            key=f"perm_view_projects_{user_id}",
                            label_visibility="collapsed",
                        )
                        p_edit_label_col.caption("Edit")
                        new_edit_projects = p_edit_toggle_col.toggle(
                            "Projects Edit",
                            value=current_edit_projects,
                            key=f"perm_edit_projects_{user_id}",
                            label_visibility="collapsed",
                        )

                        m_mod_col, m_view_label_col, m_view_toggle_col, m_edit_label_col, m_edit_toggle_col = st.columns([2.2, 1, 0.9, 1, 0.9])
                        m_mod_col.markdown("Monthly Budget:")
                        m_view_label_col.caption("View")
                        new_view_monthly = m_view_toggle_col.toggle(
                            "Monthly Budget View",
                            value=current_view_monthly,
                            key=f"perm_view_monthly_{user_id}",
                            label_visibility="collapsed",
                        )
                        m_edit_label_col.caption("Edit")
                        new_edit_monthly = m_edit_toggle_col.toggle(
                            "Monthly Budget Edit",
                            value=current_edit_monthly,
                            key=f"perm_edit_monthly_{user_id}",
                            label_visibility="collapsed",
                        )

                        # UI-side safety guard; database function also enforces this.
                        if new_edit_projects and not new_view_projects:
                            new_view_projects = True
                        if not new_view_projects and new_edit_projects:
                            new_edit_projects = False
                        if new_edit_monthly and not new_view_monthly:
                            new_view_monthly = True
                        if not new_view_monthly and new_edit_monthly:
                            new_edit_monthly = False

                        updates = {}
                        if new_view_projects != current_view_projects:
                            updates["can_view_projects"] = new_view_projects
                        if new_edit_projects != current_edit_projects:
                            updates["can_edit_projects"] = new_edit_projects
                        if new_view_monthly != current_view_monthly:
                            updates["can_view_monthly_budget"] = new_view_monthly
                        if new_edit_monthly != current_edit_monthly:
                            updates["can_edit_monthly_budget"] = new_edit_monthly

                        if updates:
                            if update_user_module_permissions(user_id, updates):
                                st.toast(f"Updated permissions for {u['username']}", icon="✅")
                            else:
                                st.toast("Failed to update permissions.", icon="❌")

                        st.divider()
                                
            # (In the future, you can easily add another sub-category here like "**To-Do List**")