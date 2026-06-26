import streamlit as st


def queue_rerun_reason(reason: str) -> None:
    if reason:
        st.session_state["pending_rerun_reason"] = reason


def _track_rerun_scope(scope: str) -> None:
    counter_key = "fragment_rerun_count" if scope == "fragment" else "app_rerun_count"
    st.session_state[counter_key] = int(st.session_state.get(counter_key, 0)) + 1


def rerun_with_reason(reason: str, *, scope: str = "app") -> None:
    queue_rerun_reason(reason)
    _track_rerun_scope(scope)
    st.rerun(scope=scope)


def rerun_fragment_with_reason(reason: str) -> None:
    """Rerun only the active @st.fragment container (not the full app)."""
    rerun_with_reason(reason, scope="fragment")


def rerun_app_with_reason(reason: str) -> None:
    queue_rerun_reason(reason)
    _track_rerun_scope("app")
    st.rerun(scope="app")


def _popover_gen_state_key(base_key: str) -> str:
    return f"_popover_gen::{base_key}"


def manage_popover_key(base_key: str) -> str:
    """Stable-but-resettable popover widget key; bump via close_manage_popover() to force close."""
    generation = st.session_state.get(_popover_gen_state_key(base_key), 0)
    return f"popover::{base_key}::{generation}"


def close_manage_popover(base_key: str) -> None:
    gen_key = _popover_gen_state_key(base_key)
    st.session_state[gen_key] = int(st.session_state.get(gen_key, 0)) + 1


def finish_manage_popover(reason: str, base_key: str, *, scope: str = "app") -> None:
    """Close a manage popover and rerun (fragment or full app)."""
    close_manage_popover(base_key)
    rerun_with_reason(reason, scope=scope)


DELETE_CONFIRM_PREFIX = "delete_confirm_pending::"


def delete_confirm_key(action_key: str) -> str:
    return f"{DELETE_CONFIRM_PREFIX}{action_key}"


def arm_delete_confirm(action_key: str) -> None:
    st.session_state[delete_confirm_key(action_key)] = True


def clear_delete_confirm(action_key: str) -> None:
    st.session_state.pop(delete_confirm_key(action_key), None)


def is_delete_confirm_armed(action_key: str) -> bool:
    return bool(st.session_state.get(delete_confirm_key(action_key)))


def render_delete_confirmation(
    action_key: str,
    *,
    item_label: str = "this item",
    warning: str | None = None,
    rerun_scope: str = "app",
) -> bool:
    """When delete is armed, show confirm/cancel UI. Returns True if user confirmed."""
    if not is_delete_confirm_armed(action_key):
        return False

    message = warning or f"Are you sure you want to delete **{item_label}**? This cannot be undone."
    st.warning(message)
    confirm_col, cancel_col = st.columns(2)
    confirmed = confirm_col.button(
        "✅ Confirm Delete",
        key=f"delete_confirm_yes::{action_key}",
        type="primary",
        width="stretch",
    )
    cancelled = cancel_col.button(
        "Cancel",
        key=f"delete_confirm_no::{action_key}",
        width="stretch",
    )
    if cancelled:
        clear_delete_confirm(action_key)
        rerun_with_reason("delete_cancel", scope=rerun_scope)
    if confirmed:
        clear_delete_confirm(action_key)
        return True
    return False


def render_signed_currency_metric(label: str, amount: float) -> None:
    """Metric-style display with green for positive and red for negative amounts."""
    color = "#21c354" if amount >= 0 else "#ff4b4b"
    st.markdown(f"**{label}**")
    st.markdown(
        f'<p style="color:{color};font-size:1.75rem;font-weight:600;margin:0;line-height:1.2;">'
        f"${amount:,.2f}</p>",
        unsafe_allow_html=True,
    )


def render_metrics_grid(metrics: list, *, desktop_columns: int = 4) -> None:
    """Responsive HTML metric grid: desktop_columns on wide screens, 2-up on mobile.

    Each item is a dict with:
      - label (str, required)
      - value (str, optional) for plain metrics
      - help (str, optional)
      - signed_amount (float, optional) — colored currency display
    """
    import html as html_lib

    if not metrics:
        return

    cells = []
    for item in metrics:
        label = html_lib.escape(item["label"])
        help_text = item.get("help")
        help_attr = ""
        if help_text:
            help_attr = f' title="{html_lib.escape(help_text)}"'

        signed_amount = item.get("signed_amount")
        if signed_amount is not None:
            color = "#21c354" if signed_amount >= 0 else "#ff4b4b"
            value_html = (
                f'<div class="hs-metric-value signed" style="color:{color}">'
                f"${signed_amount:,.2f}</div>"
            )
        else:
            value_html = (
                f'<div class="hs-metric-value">{html_lib.escape(str(item.get("value", "")))}</div>'
            )

        cells.append(
            f'<div class="hs-metric-cell"{help_attr}>'
            f'<div class="hs-metric-label">{label}</div>{value_html}</div>'
        )

    desktop_columns = max(1, min(int(desktop_columns), 4))
    st.markdown(
        f'<div class="hs-metrics-grid cols-{desktop_columns}">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


def render_checkbox_grid(pairs: list[tuple[str, str]]) -> None:
    """Lay out checkboxes in paired rows (2 per row). CSS keeps side-by-side on mobile."""
    for row_start in range(0, len(pairs), 2):
        row_items = pairs[row_start : row_start + 2]
        cols = st.columns(2, gap="small")
        for col, (label, key) in zip(cols, row_items):
            with col:
                st.checkbox(label, key=key)


def render_two_col_selector(key: str, options: list, format_func=None, *, rerun_scope: str = "app"):
    """Exclusive option picker rendered as 2-column rows of buttons."""
    if not options:
        return None

    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]

    selected_value = st.session_state.get(key)

    for idx in range(0, len(options), 2):
        row_options = options[idx:idx + 2]

        if len(row_options) == 2:
            left_opt, right_opt = row_options
            left_label = format_func(left_opt) if format_func else str(left_opt)
            right_label = format_func(right_opt) if format_func else str(right_opt)
            col_left, col_right = st.columns(2, gap="small")

            if col_left.button(
                left_label,
                key=f"{key}_btn_{idx}_left",
                type="primary" if selected_value == left_opt else "secondary",
                width="stretch",
            ):
                if selected_value != left_opt:
                    st.session_state[key] = left_opt
                    rerun_with_reason("selector_change", scope=rerun_scope)

            if col_right.button(
                right_label,
                key=f"{key}_btn_{idx}_right",
                type="primary" if selected_value == right_opt else "secondary",
                width="stretch",
            ):
                if selected_value != right_opt:
                    st.session_state[key] = right_opt
                    rerun_with_reason("selector_change", scope=rerun_scope)
        else:
            only_opt = row_options[0]
            only_label = format_func(only_opt) if format_func else str(only_opt)
            if st.button(
                only_label,
                key=f"{key}_btn_{idx}_full",
                type="primary" if selected_value == only_opt else "secondary",
                width="stretch",
            ):
                if selected_value != only_opt:
                    st.session_state[key] = only_opt
                    rerun_with_reason("selector_change", scope=rerun_scope)

    return st.session_state.get(key)
