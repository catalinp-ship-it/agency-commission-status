"""Agency Commission Status — Streamlit dashboard.

Run:  streamlit run app.py

This is a thin entry point. All logic and views live in the importable
`commission_dashboard` package so they can be embedded in a larger app.
"""

from __future__ import annotations

import streamlit as st

from commission_dashboard import (
    DashboardConfig,
    load_data,
    render_kpi_header,
    render_action_center,
    render_overdue_view,
    render_aging_view,
    render_tax_form_view,
    render_payout_method_view,
    render_time_period_view,
    render_concentration_view,
)
from commission_dashboard.fp_client import FirstPromoterError
from commission_dashboard.views import cache_age_hours, data_source_info

st.set_page_config(
    page_title="Agency Commission Status",
    page_icon="💸",
    layout="wide",
)


def sidebar() -> DashboardConfig:
    st.sidebar.title("💸 Commission Status")
    st.sidebar.caption("FirstPromoter agency payouts — Hyros team")

    cfg = DashboardConfig()
    # Streamlit secrets take priority over env vars.
    if hasattr(st, "secrets"):
        cfg.api_key = st.secrets.get("FP_API_KEY", cfg.api_key)
        cfg.account_id = st.secrets.get("FP_ACCOUNT_ID", cfg.account_id)

    with st.sidebar.expander("🔑 Credentials", expanded=not cfg.has_credentials):
        cfg.api_key = st.text_input("API key", value=cfg.api_key or "", type="password") or None
        cfg.account_id = st.text_input("Account ID (optional)", value=cfg.account_id or "") or None
        st.caption("Add FP_API_KEY to Streamlit secrets to persist your key.")

    cfg.threshold_days = st.sidebar.number_input(
        "Payout threshold (days)", min_value=1, max_value=365, value=cfg.threshold_days,
        help="Commissions become overdue after this many days from the sale date.",
    )
    cfg.currency_symbol = st.sidebar.text_input("Currency symbol", value=cfg.currency_symbol)

    cfg.demo_mode = st.sidebar.toggle(
        "Demo mode (sample data)",
        value=cfg.demo_mode or not cfg.has_credentials,
        help="Uses generated sample data so you can explore the layout without credentials.",
    )

    if st.sidebar.button("🔄 Refresh data", use_container_width=True):
        st.session_state["do_refresh"] = True
        st.rerun()

    return cfg


def _check_access() -> bool:
    """Optional password gate. Active only if an APP_PASSWORD secret/env is set,
    so the app stays fully public unless you opt in to protection."""
    import os

    password = None
    if hasattr(st, "secrets"):
        password = st.secrets.get("APP_PASSWORD", None)
    password = password or os.getenv("APP_PASSWORD")
    if not password:
        return True  # no gate configured -> open access

    if st.session_state.get("_authed"):
        return True

    st.title("🔒 Agency Commission Status")
    entered = st.text_input("Password", type="password")
    if entered and entered == password:
        st.session_state["_authed"] = True
        st.rerun()
    elif entered:
        st.error("Incorrect password.")
    else:
        st.caption("Enter the password to view the dashboard.")
    return False


def main() -> None:
    if not _check_access():
        return
    cfg = sidebar()
    st.title("Agency Commission Status")

    if cfg.demo_mode:
        st.warning("Demo mode — showing generated sample data, not your live FirstPromoter account.", icon="🧪")
    elif not cfg.has_credentials:
        st.info("Enter your FirstPromoter API key in the sidebar, or enable Demo mode.", icon="🔑")
        return

    force_refresh = bool(st.session_state.pop("do_refresh", False))
    try:
        data = load_data(cfg, force_refresh=force_refresh)
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to load data: {e}")
        return

    # Banner showing data freshness.
    info = data_source_info()
    age  = info["age_hours"]
    if info["rewards_count"] == 0:
        st.error(
            "**No commission data found.** "
            "The data pipeline hasn't run yet or the last fetch returned 0 records.\n\n"
            "**Fix:** Go to your GitHub repo → Actions → *Fetch FirstPromoter Data* → "
            "Run workflow. It will re-fetch all history and push the results.",
            icon="🚫",
        )
    elif age is not None:
        age_str = f"{age:.1f}h ago" if age < 48 else f"{age/24:.0f} days ago"
        st.info(
            f"📁 {info['rewards_count']:,} rewards · {info['promoters_count']:,} promoters · "
            f"Last updated {age_str} · Refreshes every 6h via GitHub Actions",
            icon=None,
        )
    else:
        st.warning("⚠️ No manifest — data may be stale. Trigger a workflow run in GitHub Actions.", icon=None)

    render_kpi_header(data, cfg)
    st.divider()

    tabs = st.tabs(
        [
            "🎯 Action center",
            "⏰ Overdue",
            "📊 Aging",
            "📄 Missing W8/W9",
            "💳 Missing payout",
            "🗓️ Time period",
            "🔝 Concentration",
        ]
    )
    with tabs[0]:
        render_action_center(data, cfg)
    with tabs[1]:
        render_overdue_view(data, cfg)
    with tabs[2]:
        render_aging_view(data, cfg)
    with tabs[3]:
        render_tax_form_view(data, cfg)
    with tabs[4]:
        render_payout_method_view(data, cfg)
    with tabs[5]:
        render_time_period_view(data, cfg)
    with tabs[6]:
        render_concentration_view(data, cfg)

    st.caption(
        f"Threshold: {cfg.threshold_days} days · "
        f"{len(data['commissions'])} commissions · {len(data['promoters'])} promoters loaded."
    )


if __name__ == "__main__":
    main()
