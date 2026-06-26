import streamlit as st


def queue_rerun_reason(reason: str) -> None:
    if reason:
        st.session_state["pending_rerun_reason"] = reason


def rerun_with_reason(reason: str) -> None:
    queue_rerun_reason(reason)
    st.rerun()


def rerun_app_with_reason(reason: str) -> None:
    queue_rerun_reason(reason)
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


def finish_manage_popover(reason: str, base_key: str) -> None:
    """Close a manage popover and rerun the app (matches backlog save behavior)."""
    close_manage_popover(base_key)
    rerun_with_reason(reason)


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
        rerun_with_reason("delete_cancel")
    if confirmed:
        clear_delete_confirm(action_key)
        return True
    return False


def render_two_col_selector(key: str, options: list, format_func=None):
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
            col_left, col_right = st.columns(2)

            if col_left.button(
                left_label,
                key=f"{key}_btn_{idx}_left",
                type="primary" if selected_value == left_opt else "secondary",
                width="stretch",
            ):
                if selected_value != left_opt:
                    st.session_state[key] = left_opt
                    rerun_with_reason("selector_change")

            if col_right.button(
                right_label,
                key=f"{key}_btn_{idx}_right",
                type="primary" if selected_value == right_opt else "secondary",
                width="stretch",
            ):
                if selected_value != right_opt:
                    st.session_state[key] = right_opt
                    rerun_with_reason("selector_change")
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
                    rerun_with_reason("selector_change")

    return st.session_state.get(key)
