import streamlit as st
import pandas as pd

from home_assist_api import fetch_ha_state
from ui_helpers import rerun_with_reason


HOME_MGMT_PERMISSIONS = {
    "solar": {
        "label": "Solar Production",
        "admin_label": "Solar:",
        "view_key": "can_view_home_solar",
        "edit_key": "can_edit_home_solar",
        "menu_title": "☀️ Solar Production",
        "menu_caption": "Live energy flow, inverter stats, and panel optimizers.",
        "open_label": "Open Solar Production",
        "open_key": "open_home_solar",
    },
    "security": {
        "label": "Security",
        "admin_label": "Security:",
        "view_key": "can_view_home_security",
        "edit_key": "can_edit_home_security",
        "menu_title": "🛡️ Security",
        "menu_caption": "Camera and sensor overview.",
        "open_label": "Open Security",
        "open_key": "open_home_security",
    },
    "garage": {
        "label": "Garage",
        "admin_label": "Garage:",
        "view_key": "can_view_home_garage",
        "edit_key": "can_edit_home_garage",
        "menu_title": "🚗 Garage",
        "menu_caption": "Open or close the garage door.",
        "open_label": "Open Garage Access",
        "open_key": "open_home_garage",
    },
    "logs": {
        "label": "System Logs",
        "admin_label": "System Logs:",
        "view_key": "can_view_home_logs",
        "edit_key": "can_edit_home_logs",
        "menu_title": "⚙️ System Logs",
        "menu_caption": "Database and automation event history.",
        "open_label": "Open System Logs",
        "open_key": "open_home_logs",
    },
}

HOME_MGMT_MODULE_ORDER = ("solar", "security", "garage", "logs")


def _is_home_mgmt_privileged() -> bool:
    return st.session_state.get("user_role", "member") in ["admin", "developer"]


def can_view_home_module(module_key: str) -> bool:
    if _is_home_mgmt_privileged():
        return True
    perm = HOME_MGMT_PERMISSIONS.get(module_key, {})
    return bool(st.session_state.get(perm.get("view_key", ""), False))


def can_edit_home_module(module_key: str) -> bool:
    if _is_home_mgmt_privileged():
        return True
    perm = HOME_MGMT_PERMISSIONS.get(module_key, {})
    return bool(st.session_state.get(perm.get("edit_key", ""), False))


def can_access_home_management() -> bool:
    if _is_home_mgmt_privileged():
        return True
    return any(can_view_home_module(module_key) for module_key in HOME_MGMT_MODULE_ORDER)


def _render_home_management_menu() -> None:
    st.subheader("🏡 Home Management")
    st.caption("Solar, security, garage access, and system event history.")
    st.write("")

    visible_modules = [key for key in HOME_MGMT_MODULE_ORDER if can_view_home_module(key)]
    if not visible_modules:
        st.info("You do not have access to any Home Management modules.")
        return

    left_modules = visible_modules[0::2]
    right_modules = visible_modules[1::2]
    col1, col2 = st.columns(2)

    def _render_module_card(column, module_key):
        perm = HOME_MGMT_PERMISSIONS[module_key]
        with column.container(border=True):
            st.markdown(f"### {perm['menu_title']}")
            st.caption(perm["menu_caption"])
            if st.button(perm["open_label"], key=perm["open_key"], type="secondary", width="stretch"):
                st.session_state["home_management_view"] = module_key
                rerun_with_reason("home_mgmt_nav")

    for module_key in left_modules:
        _render_module_card(col1, module_key)
    for module_key in right_modules:
        _render_module_card(col2, module_key)


def _render_solar_production() -> None:
    if can_edit_home_module("solar"):
        if st.button("🔄 Refresh Telemetry", type="primary", width="stretch"):
            rerun_with_reason("telemetry_refresh")
    st.subheader("☀️ Live Energy Flow")

    solar_data = fetch_ha_state("sensor.solaredge_current_power")
    net_data = fetch_ha_state("sensor.solaredge_meter_power")
    inv1_data = fetch_ha_state("sensor.solaredge_inverter_1")
    inv2_data = fetch_ha_state("sensor.solaredge_inverter_2")

    try:
        cur_solar_w = float(solar_data.get("state", 0))
        net_w = float(net_data.get("state", 0))
        inv1_w = float(inv1_data.get("state", 0))
        inv2_w = float(inv2_data.get("state", 0))
    except ValueError:
        cur_solar_w, net_w, inv1_w, inv2_w = 0.0, 0.0, 0.0, 0.0

    home_cons_w = cur_solar_w + net_w

    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Panels Generating",
        f"{(cur_solar_w / 1000):.2f} kW",
        "Producing" if cur_solar_w > 0 else "Offline",
    )
    col2.metric("Home Consuming", f"{(home_cons_w / 1000):.2f} kW", "Load", delta_color="off")
    col3.metric(
        "Grid Status",
        f"{abs(net_w / 1000):.2f} kW",
        "Exporting" if net_w < 0 else "Importing",
        delta_color="inverse" if net_w < 0 else "normal",
    )

    st.divider()

    st.markdown("#### 🔌 Inverter Performance")
    inv_col1, inv_col2 = st.columns(2)
    inv_col1.metric("Inverter 1", f"{(inv1_w / 1000):.2f} kW", "Active" if inv1_w > 0 else "Offline")
    inv_col2.metric("Inverter 2", f"{(inv2_w / 1000):.2f} kW", "Active" if inv2_w > 0 else "Offline")

    st.write("")

    with st.expander("🔍 View Individual Panel Optimizers (67)"):
        panel_data = fetch_ha_state("sensor.solaredge_panel_array")
        panels = panel_data.get("attributes", {}).get("panels", {})

        if panels:
            df = pd.DataFrame(list(panels.items()), columns=["Panel ID", "Power (W)"])
            df.set_index("Panel ID", inplace=True)
            try:
                st.dataframe(
                    df.style.background_gradient(cmap="Greens", vmin=50, vmax=350),
                    width="stretch",
                )
            except ImportError:
                st.dataframe(df, width="stretch")
                st.caption("Install matplotlib to enable heatmap styling for panel power.")
        else:
            st.warning("Panel data currently unavailable.")


def _render_security_overview() -> None:
    st.subheader("🛡️ Security Overview")
    st.write("Camera and sensor feeds will render here...")


def _render_garage_access() -> None:
    st.subheader("🚗 Garage Access")

    if "mock_garage_state" not in st.session_state:
        st.session_state["mock_garage_state"] = "closed"

    current_state = st.session_state["mock_garage_state"]

    with st.container(border=True):
        if current_state == "closed":
            st.markdown("### 🟢 Status: **CLOSED**")
            st.caption("The garage is secured.")
            action_text = "📤 OPEN Garage Door"
            btn_type = "secondary"
        else:
            st.markdown("### 🔴 Status: **OPEN**")
            st.caption("Warning: The garage is exposed.")
            action_text = "📥 CLOSE Garage Door"
            btn_type = "primary"

        st.write("")

        if can_edit_home_module("garage"):
            if st.button(action_text, type=btn_type, width="stretch"):
                if current_state == "closed":
                    st.session_state["mock_garage_state"] = "open"
                else:
                    st.session_state["mock_garage_state"] = "closed"
                rerun_with_reason("garage_toggle")
        else:
            st.caption("View-only access. Garage controls are restricted.")


def _render_system_logs() -> None:
    st.subheader("⚙️ System Logs")
    st.write("Supabase database logs will render here...")


def render_home_management_module() -> None:
    if "home_management_view" not in st.session_state:
        st.session_state["home_management_view"] = "menu"

    view = st.session_state.get("home_management_view", "menu")

    if view == "menu":
        _render_home_management_menu()
        return

    if not can_view_home_module(view):
        st.warning("You do not have access to this Home Management module.")
        st.session_state["home_management_view"] = "menu"
        return

    if st.button("⬅️ Back to Home Management", key="back_home_management_menu"):
        st.session_state["home_management_view"] = "menu"
        rerun_with_reason("home_mgmt_nav")

    st.divider()

    if view == "solar":
        _render_solar_production()
    elif view == "security":
        _render_security_overview()
    elif view == "garage":
        _render_garage_access()
    elif view == "logs":
        _render_system_logs()
    else:
        st.session_state["home_management_view"] = "menu"
        rerun_with_reason("home_mgmt_nav")


def is_home_management_drilldown_active() -> bool:
    return st.session_state.get("home_management_view", "menu") != "menu"
