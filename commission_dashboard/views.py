"""Reusable Streamlit views for the commission dashboard.

Every view is a standalone function that takes the already-fetched `data`
dict (commissions / promoters / summary DataFrames) plus a DashboardConfig,
and renders into the current Streamlit context. A parent app can import and
embed any single view:

    import streamlit as st
    from dashboard import load_data, render_overdue_view, DashboardConfig

    cfg = DashboardConfig(api_key=..., account_id=..., threshold_days=90)
    data = load_data(cfg)
    render_overdue_view(data, cfg)
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import time
from typing import Dict

import pandas as pd
import streamlit as st

from .config import DashboardConfig
from .data import fetch_demo, fetch_live

# Disk cache lives next to the package; survives Streamlit restarts.
_DISK_CACHE_DIR = os.path.join(tempfile.gettempdir(), "commission_dashboard_cache")
_DISK_CACHE_TTL = 3600  # seconds — 1 hour
os.makedirs(_DISK_CACHE_DIR, exist_ok=True)


def _disk_cache_path(cache_key: str) -> str:
    h = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    return os.path.join(_DISK_CACHE_DIR, f"{h}.pkl")


def _disk_cache_load(cache_key: str):
    path = _disk_cache_path(cache_key)
    try:
        if os.path.exists(path):
            age = time.time() - os.path.getmtime(path)
            if age < _DISK_CACHE_TTL:
                with open(path, "rb") as f:
                    return pickle.load(f)
    except Exception:  # corrupted / unreadable — ignore
        pass
    return None


def _disk_cache_save(cache_key: str, data) -> None:
    path = _disk_cache_path(cache_key)
    try:
        with open(path, "wb") as f:
            pickle.dump(data, f)
    except Exception:
        pass


def _disk_cache_invalidate(cache_key: str) -> None:
    path = _disk_cache_path(cache_key)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Data loading (cached — in-memory + disk)
# --------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Loading FirstPromoter data… (first load only — cached for 1 hour after)")
def _cached_fetch(api_key, account_id, threshold_days, demo, api_version, lookback_months, _buster):
    # Build a stable cache key (exclude the buster so disk hits survive page reloads).
    cache_key = f"{api_key}|{account_id}|{threshold_days}|{demo}|{api_version}|{lookback_months}"

    # Try disk cache first (survives Streamlit restarts).
    cached = _disk_cache_load(cache_key)
    if cached is not None:
        return cached

    if demo or not api_key:
        result = fetch_demo(threshold_days)
    else:
        result = fetch_live(api_key, account_id, threshold_days, api_version, lookback_months)

    _disk_cache_save(cache_key, result)
    return result


def load_data(cfg: DashboardConfig, refresh_token: int = 0) -> Dict[str, pd.DataFrame]:
    """Load (and cache) dashboard data. Bump `refresh_token` to force a refresh."""
    if refresh_token:
        cache_key = f"{cfg.api_key}|{cfg.account_id}|{cfg.threshold_days}|{cfg.demo_mode}|{cfg.api_version}|{cfg.lookback_months}"
        _disk_cache_invalidate(cache_key)
    return _cached_fetch(
        cfg.api_key, cfg.account_id, cfg.threshold_days, cfg.demo_mode,
        cfg.api_version, cfg.lookback_months, refresh_token,
    )


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _money_col(cfg: DashboardConfig):
    return st.column_config.NumberColumn(format=f"{cfg.currency_symbol}%.2f")


def _empty(msg: str = "No data to display.") -> bool:
    st.info(msg)
    return True


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
def render_kpi_header(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    summary = data["summary"]
    commissions = data["commissions"]

    overdue_amt = summary["overdue_amount"].sum() if not summary.empty else 0
    outstanding_amt = summary["outstanding_amount"].sum() if not summary.empty else 0
    blocked_amt = (
        summary.loc[summary["is_blocked"], "outstanding_amount"].sum()
        if not summary.empty
        else 0
    )
    affiliates_overdue = int((summary["overdue_amount"] > 0).sum()) if not summary.empty else 0
    ready_amt = outstanding_amt - blocked_amt

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        f"Overdue payouts (>{cfg.threshold_days}d)",
        cfg.money(overdue_amt),
        help="Approved, unpaid cash commissions older than the threshold.",
    )
    c2.metric("Affiliates with overdue", affiliates_overdue)
    c3.metric(
        "Blocked $ (cannot pay)",
        cfg.money(blocked_amt),
        help="Owed money held up by a missing tax form, payout method or invoice details.",
    )
    c4.metric(
        "Ready to pay now",
        cfg.money(ready_amt),
        help="Owed money with no compliance blockers.",
    )


def render_action_center(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    """The headline view: one row per affiliate who is owed money, with the
    exact blocker(s) standing between them and a payout. Tailored for the
    Hyros agency team — not something FirstPromoter shows directly."""
    st.subheader("🎯 Action center — who's owed, and what's blocking payment")
    summary = data["summary"]
    if summary.empty:
        return None
    owed = summary[summary["outstanding_amount"] > 0].copy()
    if owed.empty:
        _empty("No outstanding payouts. Everyone is paid up. 🎉")
        return None

    only_blocked = st.toggle("Show only blocked affiliates", value=False)
    if only_blocked:
        owed = owed[owed["is_blocked"]]

    owed = owed.sort_values(["is_blocked", "overdue_amount"], ascending=[False, False])
    table = owed[
        [
            "promoter_name",
            "promoter_email",
            "country",
            "outstanding_amount",
            "overdue_amount",
            "oldest_age_days",
            "payment_status",
            "blocker_reasons",
            "payout_method",
        ]
    ].rename(
        columns={
            "promoter_name": "Affiliate",
            "promoter_email": "Email",
            "country": "Country",
            "outstanding_amount": "Owed",
            "overdue_amount": "Overdue",
            "oldest_age_days": "Oldest (days)",
            "payment_status": "Status",
            "blocker_reasons": "Blocker / note",
            "payout_method": "Payout method",
        }
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Owed": _money_col(cfg),
            "Overdue": _money_col(cfg),
            "Oldest (days)": st.column_config.NumberColumn(format="%d"),
        },
    )
    st.download_button(
        "⬇️ Export action list (CSV)",
        table.to_csv(index=False).encode(),
        file_name="commission_action_list.csv",
        mime="text/csv",
    )


def render_overdue_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader(f"⏰ Overdue payouts (older than {cfg.threshold_days} days)")
    c = data["commissions"]
    if c.empty:
        _empty()
        return None
    overdue = c[c["is_overdue"]].copy()
    if overdue.empty:
        _empty("No commissions are past the payout threshold. ✅")
        return None

    st.caption(
        f"{len(overdue)} commissions worth "
        f"{cfg.money(overdue['amount'].sum())} are past due "
        f"({cfg.threshold_days}+ days since the sale was created)."
    )
    table = overdue[
        [
            "promoter_name",
            "promoter_email",
            "campaign",
            "amount",
            "created_at",
            "due_date",
            "age_days",
            "status",
        ]
    ].sort_values("age_days", ascending=False)
    table = table.rename(
        columns={
            "promoter_name": "Affiliate",
            "promoter_email": "Email",
            "campaign": "Campaign",
            "amount": "Amount",
            "created_at": "Sale created",
            "due_date": "Was due",
            "age_days": "Age (days)",
            "status": "Status",
        }
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Amount": _money_col(cfg),
            "Sale created": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
            "Was due": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        },
    )


def render_aging_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    """AR-style aging buckets — an extra view beyond FirstPromoter's defaults."""
    st.subheader("📊 Payout aging buckets")
    c = data["commissions"]
    if c.empty:
        _empty()
        return None
    out = c[c["is_outstanding"]].copy()
    if out.empty:
        _empty("Nothing outstanding.")
        return None
    t = cfg.threshold_days
    bins = [-1, t, t + 30, t + 90, t + 180, 10**9]
    labels = [
        f"0–{t}d (not yet due)",
        f"{t}–{t+30}d overdue",
        f"{t+30}–{t+90}d overdue",
        f"{t+90}–{t+180}d overdue",
        f"{t+180}d+ overdue",
    ]
    out["bucket"] = pd.cut(out["age_days"], bins=bins, labels=labels)
    agg = (
        out.groupby("bucket", observed=False)["amount"]
        .agg(["sum", "count"])
        .reindex(labels)
        .fillna(0)
    )
    agg.columns = ["Amount", "Commissions"]
    st.bar_chart(agg["Amount"])
    st.dataframe(
        agg,
        use_container_width=True,
        column_config={"Amount": _money_col(cfg)},
    )


def render_tax_form_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("📄 Missing W8 / W9 tax form")
    summary = data["summary"]
    if summary.empty:
        _empty()
        return None
    flagged = summary[summary["blocked_missing_tax_form"]].copy()
    st.caption(
        f"{len(flagged)} affiliates are owed "
        f"{cfg.money(flagged['outstanding_amount'].sum())} but have no W8/W9 on file."
    )
    if flagged.empty:
        _empty("Every affiliate who is owed money has a tax form. ✅")
        return None
    table = flagged[
        ["promoter_name", "promoter_email", "country", "outstanding_amount", "overdue_amount"]
    ].sort_values("outstanding_amount", ascending=False)
    table = table.rename(
        columns={
            "promoter_name": "Affiliate",
            "promoter_email": "Email",
            "country": "Country",
            "outstanding_amount": "Owed",
            "overdue_amount": "Overdue",
        }
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={"Owed": _money_col(cfg), "Overdue": _money_col(cfg)},
    )


def render_payout_method_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("💳 Missing PayPal / payout details")
    summary = data["summary"]
    if summary.empty:
        _empty()
        return None
    flagged = summary[summary["blocked_missing_payout"]].copy()
    st.caption(
        f"{len(flagged)} affiliates are owed "
        f"{cfg.money(flagged['outstanding_amount'].sum())} but have no usable payout method."
    )
    if flagged.empty:
        _empty("Every affiliate who is owed money has a payout method. ✅")
        return None
    table = flagged[
        ["promoter_name", "promoter_email", "country", "outstanding_amount", "overdue_amount"]
    ].sort_values("outstanding_amount", ascending=False)
    table = table.rename(
        columns={
            "promoter_name": "Affiliate",
            "promoter_email": "Email",
            "country": "Country",
            "outstanding_amount": "Owed",
            "overdue_amount": "Overdue",
        }
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={"Owed": _money_col(cfg), "Overdue": _money_col(cfg)},
    )


def render_time_period_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("🗓️ Time-period breakdown")
    c = data["commissions"]
    if c.empty or c["created_at"].isna().all():
        _empty()
        return None

    out = c[c["is_outstanding"]].dropna(subset=["created_at"]).copy()
    if out.empty:
        _empty("Nothing outstanding to break down.")
        return None

    out["created_date"] = out["created_at"].dt.date
    min_d, max_d = out["created_date"].min(), out["created_date"].max()
    c1, c2, c3 = st.columns([2, 2, 1])
    start = c1.date_input("From (sale created)", value=min_d, min_value=min_d, max_value=max_d)
    end = c2.date_input("To (sale created)", value=max_d, min_value=min_d, max_value=max_d)
    grain = c3.selectbox("Group by", ["Week", "Month"], index=1)

    mask = (out["created_date"] >= start) & (out["created_date"] <= end)
    window = out[mask].copy()
    if window.empty:
        _empty("No outstanding commissions in that range.")
        return None

    freq = "W" if grain == "Week" else "M"
    window["period"] = window["created_at"].dt.to_period(freq).dt.start_time
    series = window.groupby("period")["amount"].sum()
    st.bar_chart(series)
    st.caption(
        f"Outstanding {cfg.money(window['amount'].sum())} across "
        f"{len(window)} commissions, {start} → {end}."
    )


def render_concentration_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    """Extra view: where the money is concentrated + compliance readiness."""
    st.subheader("🔝 Top affiliates owed & compliance readiness")
    summary = data["summary"]
    if summary.empty:
        _empty()
        return None
    owed = summary[summary["outstanding_amount"] > 0].copy()
    if owed.empty:
        _empty("Nothing outstanding.")
        return None

    total = owed["outstanding_amount"].sum()
    ready = owed.loc[~owed["is_blocked"], "outstanding_amount"].sum()
    pct_ready = (ready / total * 100) if total else 0
    st.caption(f"Compliance readiness: {pct_ready:.0f}% of owed dollars are clear to pay.")
    st.progress(min(int(pct_ready), 100))

    top = owed.nlargest(10, "outstanding_amount")[
        ["promoter_name", "outstanding_amount"]
    ].set_index("promoter_name")["outstanding_amount"]
    st.bar_chart(top)
