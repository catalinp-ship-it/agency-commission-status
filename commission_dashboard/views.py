"""Reusable Streamlit views for the commission dashboard.

Caching architecture (post-rebuild):
  - Single source of truth: data/*.json files written by GitHub Actions
  - @st.cache_data keyed by manifest `fetched_at` timestamp
    → cache auto-busts the moment GitHub Actions commits new data
  - NO disk pickle cache (it caused the empty-data-served-silently bug)
  - Refresh button calls st.cache_data.clear() — simple and correct

Every view is a standalone function that takes the already-fetched `data`
dict (commissions / promoters / summary DataFrames) plus a DashboardConfig,
and renders into the current Streamlit context.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import streamlit as st

from .config import DashboardConfig
from .data import (
    commissions_to_df,
    promoters_to_df,
    build_promoter_summary,
    _v1_reward_to_v2_commission,
    _v1_promoter_to_v2,
    fetch_demo,
)

_DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _read_manifest() -> dict:
    try:
        return json.loads((_DATA_DIR / "manifest.json").read_text())
    except Exception:
        return {}


def cache_age_hours() -> Optional[float]:
    m = _read_manifest()
    raw = m.get("fetched_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def data_source_info() -> dict:
    """Return metadata shown in the UI banner."""
    m = _read_manifest()
    return {
        "rewards_count": m.get("rewards_count", 0),
        "promoters_count": m.get("promoters_count", 0),
        "fetched_at": m.get("fetched_at", ""),
        "age_hours": cache_age_hours(),
    }


# ---------------------------------------------------------------------------
# Data loading — single, clean cache layer
# ---------------------------------------------------------------------------

@st.cache_data(
    ttl=300,  # 5-minute safety TTL; manifest key busts cache on new data
    show_spinner="Loading commission data…",
)
def _load_from_json(manifest_ts: str, threshold_days: int) -> Dict[str, pd.DataFrame]:
    """Read JSON files and build DataFrames.

    manifest_ts is ONLY a cache key — when GitHub Actions writes new data it
    updates fetched_at in manifest.json, which changes this key and busts
    the cache automatically.  We never actually use manifest_ts inside.
    """
    rewards_file = _DATA_DIR / "rewards.json"
    promoters_file = _DATA_DIR / "promoters.json"

    if not rewards_file.exists() or not promoters_file.exists():
        return _empty_data()

    try:
        raw_rewards = json.loads(rewards_file.read_text())
        raw_promoters = json.loads(promoters_file.read_text())
    except Exception as exc:
        st.error(f"Failed to parse data files: {exc}")
        return _empty_data()

    if not raw_rewards:
        # File exists but is empty — data pipeline hasn't run yet or failed.
        return _empty_data()

    rewards_v2 = [_v1_reward_to_v2_commission(r) for r in raw_rewards]
    promoters_v2 = [_v1_promoter_to_v2(p) for p in raw_promoters]

    commissions = commissions_to_df(rewards_v2, threshold_days)
    promoters = promoters_to_df(promoters_v2)

    # Backfill promoter display names from the promoters table.
    if not commissions.empty and not promoters.empty:
        name_map = promoters.set_index("promoter_id")["promoter_name"].to_dict()
        missing_name = commissions["promoter_name"].isna() | (
            commissions["promoter_name"] == commissions["promoter_email"]
        )
        commissions.loc[missing_name, "promoter_name"] = (
            commissions.loc[missing_name, "promoter_id"]
            .map(name_map)
            .fillna(commissions.loc[missing_name, "promoter_name"])
        )

    summary = build_promoter_summary(commissions, promoters, threshold_days)
    return {"commissions": commissions, "promoters": promoters, "summary": summary}


def _empty_data() -> Dict[str, pd.DataFrame]:
    return {
        "commissions": pd.DataFrame(),
        "promoters": pd.DataFrame(),
        "summary": pd.DataFrame(),
    }


def load_data(cfg: DashboardConfig, force_refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """Public entry point. Pass force_refresh=True to bust all caches."""
    if force_refresh:
        st.cache_data.clear()

    if cfg.demo_mode or not cfg.has_credentials:
        return fetch_demo(cfg.threshold_days)

    manifest = _read_manifest()
    ts = manifest.get("fetched_at", "missing")

    return _load_from_json(ts, cfg.threshold_days)


# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------

def _money_col(cfg: DashboardConfig):
    return st.column_config.NumberColumn(format=f"{cfg.currency_symbol}%.2f")


def _empty_view(msg: str = "No data to display.") -> None:
    st.info(msg)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def render_kpi_header(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    summary = data["summary"]
    commissions = data["commissions"]

    overdue_amt     = summary["overdue_amount"].sum()     if not summary.empty else 0
    outstanding_amt = summary["outstanding_amount"].sum() if not summary.empty else 0
    blocked_amt     = (
        summary.loc[summary["is_blocked"], "outstanding_amount"].sum()
        if not summary.empty else 0
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
        "Blocked (cannot pay)",
        cfg.money(blocked_amt),
        help="Money held up by a missing tax form, payout method, or invoice details.",
    )
    c4.metric(
        "Ready to pay now",
        cfg.money(ready_amt),
        help="Owed money with no compliance blockers.",
    )


def render_action_center(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("🎯 Action center — who's owed, and what's blocking payment")
    summary = data["summary"]
    if summary.empty:
        _empty_view("No promoter data loaded.")
        return

    owed = summary[summary["outstanding_amount"] > 0].copy()
    if owed.empty:
        _empty_view("No outstanding payouts. Everyone is paid up. 🎉")
        return

    only_blocked = st.toggle("Show only blocked affiliates", value=False)
    if only_blocked:
        owed = owed[owed["is_blocked"]]

    owed = owed.sort_values(["is_blocked", "overdue_amount"], ascending=[False, False])
    table = owed[
        [
            "promoter_name", "promoter_email", "country",
            "outstanding_amount", "overdue_amount", "oldest_age_days",
            "payment_status", "blocker_reasons", "payout_method",
        ]
    ].rename(columns={
        "promoter_name":      "Affiliate",
        "promoter_email":     "Email",
        "country":            "Country",
        "outstanding_amount": "Owed",
        "overdue_amount":     "Overdue",
        "oldest_age_days":    "Oldest (days)",
        "payment_status":     "Status",
        "blocker_reasons":    "Blocker / note",
        "payout_method":      "Payout method",
    })

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Owed":          _money_col(cfg),
            "Overdue":       _money_col(cfg),
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
        _empty_view()
        return

    overdue = c[c["is_overdue"]].copy()
    if overdue.empty:
        _empty_view("No commissions are past the payout threshold. ✅")
        return

    st.caption(
        f"{len(overdue)} commissions worth "
        f"{cfg.money(overdue['amount'].sum())} are past due "
        f"({cfg.threshold_days}+ days since the sale was created)."
    )
    table = overdue[[
        "promoter_name", "promoter_email", "campaign",
        "amount", "created_at", "due_date", "age_days", "status",
    ]].sort_values("age_days", ascending=False).rename(columns={
        "promoter_name":  "Affiliate",
        "promoter_email": "Email",
        "campaign":       "Campaign",
        "amount":         "Amount",
        "created_at":     "Sale created",
        "due_date":       "Was due",
        "age_days":       "Age (days)",
        "status":         "Status",
    })
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Amount":       _money_col(cfg),
            "Sale created": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
            "Was due":      st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        },
    )


def render_aging_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("📊 Payout aging buckets")
    c = data["commissions"]
    if c.empty:
        _empty_view()
        return

    out = c[c["is_outstanding"]].copy()
    if out.empty:
        _empty_view("Nothing outstanding.")
        return

    t = cfg.threshold_days
    bins   = [-1, t, t + 30, t + 90, t + 180, 10**9]
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
    st.dataframe(agg, use_container_width=True, column_config={"Amount": _money_col(cfg)})


def render_tax_form_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("📄 Missing W8 / W9 tax form")
    summary = data["summary"]
    if summary.empty:
        _empty_view()
        return

    flagged = summary[summary["blocked_missing_tax_form"]].copy()
    if flagged.empty:
        st.caption("Every affiliate who is owed money has a tax form on file.")
        _empty_view("Every affiliate who is owed money has a tax form. ✅")
        return

    st.caption(
        f"{len(flagged)} affiliates are owed "
        f"{cfg.money(flagged['outstanding_amount'].sum())} but have no W8/W9 on file."
    )
    table = flagged[[
        "promoter_name", "promoter_email", "country",
        "outstanding_amount", "overdue_amount",
    ]].sort_values("outstanding_amount", ascending=False).rename(columns={
        "promoter_name":      "Affiliate",
        "promoter_email":     "Email",
        "country":            "Country",
        "outstanding_amount": "Owed",
        "overdue_amount":     "Overdue",
    })
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={"Owed": _money_col(cfg), "Overdue": _money_col(cfg)},
    )


def render_payout_method_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("💳 Missing PayPal / payout details")
    summary = data["summary"]
    if summary.empty:
        _empty_view()
        return

    flagged = summary[summary["blocked_missing_payout"]].copy()
    if flagged.empty:
        _empty_view("Every affiliate who is owed money has a payout method. ✅")
        return

    st.caption(
        f"{len(flagged)} affiliates are owed "
        f"{cfg.money(flagged['outstanding_amount'].sum())} but have no usable payout method."
    )
    table = flagged[[
        "promoter_name", "promoter_email", "country",
        "outstanding_amount", "overdue_amount",
    ]].sort_values("outstanding_amount", ascending=False).rename(columns={
        "promoter_name":      "Affiliate",
        "promoter_email":     "Email",
        "country":            "Country",
        "outstanding_amount": "Owed",
        "overdue_amount":     "Overdue",
    })
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={"Owed": _money_col(cfg), "Overdue": _money_col(cfg)},
    )


def render_time_period_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("🗓️ Time-period breakdown")
    c = data["commissions"]
    if c.empty or c["created_at"].isna().all():
        _empty_view()
        return

    out = c[c["is_outstanding"]].dropna(subset=["created_at"]).copy()
    if out.empty:
        _empty_view("Nothing outstanding to break down.")
        return

    out["created_date"] = out["created_at"].dt.date
    min_d, max_d = out["created_date"].min(), out["created_date"].max()
    col1, col2, col3 = st.columns([2, 2, 1])
    start = col1.date_input("From (sale created)", value=min_d, min_value=min_d, max_value=max_d)
    end   = col2.date_input("To (sale created)",   value=max_d, min_value=min_d, max_value=max_d)
    grain = col3.selectbox("Group by", ["Week", "Month"], index=1)

    mask   = (out["created_date"] >= start) & (out["created_date"] <= end)
    window = out[mask].copy()
    if window.empty:
        _empty_view("No outstanding commissions in that range.")
        return

    freq = "W" if grain == "Week" else "ME"
    window["period"] = window["created_at"].dt.to_period(freq).dt.start_time
    series = window.groupby("period")["amount"].sum()
    st.bar_chart(series)
    st.caption(
        f"Outstanding {cfg.money(window['amount'].sum())} across "
        f"{len(window)} commissions, {start} → {end}."
    )


def render_concentration_view(data: Dict[str, pd.DataFrame], cfg: DashboardConfig) -> None:
    st.subheader("🔝 Top affiliates owed & compliance readiness")
    summary = data["summary"]
    if summary.empty:
        _empty_view()
        return

    owed = summary[summary["outstanding_amount"] > 0].copy()
    if owed.empty:
        _empty_view("Nothing outstanding.")
        return

    total    = owed["outstanding_amount"].sum()
    ready    = owed.loc[~owed["is_blocked"], "outstanding_amount"].sum()
    pct_ready = (ready / total * 100) if total else 0

    st.caption(f"Compliance readiness: {pct_ready:.0f}% of owed dollars are clear to pay.")
    st.progress(min(int(pct_ready), 100))

    top = (
        owed.nlargest(10, "outstanding_amount")[["promoter_name", "outstanding_amount"]]
        .set_index("promoter_name")["outstanding_amount"]
    )
    st.bar_chart(top)
