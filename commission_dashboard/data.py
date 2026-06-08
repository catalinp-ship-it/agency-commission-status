"""Data layer: fetch from FirstPromoter (or generate demo data) and reshape
into tidy pandas DataFrames with the derived fields the dashboard needs
(commission age, overdue flag, per-promoter blockers, etc.).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .fp_client import FirstPromoterClient

# Commission amounts come back as integer minor units (cents).
AMOUNT_DIVISOR = 100.0


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _to_dt(value) -> Optional[pd.Timestamp]:
    if not value:
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


# --------------------------------------------------------------------------
# Reshape raw API rows -> DataFrames
# --------------------------------------------------------------------------
def commissions_to_df(raw: List[dict], threshold_days: int) -> pd.DataFrame:
    now = _now()
    rows = []
    for c in raw:
        pc = c.get("promoter_campaign") or {}
        promoter = pc.get("promoter") or {}
        campaign = pc.get("campaign") or {}
        referral = c.get("referral") or {}
        created = _to_dt(c.get("created_at"))
        age_days = (now - created).days if created is not None else None
        amount = (c.get("amount") or 0) / AMOUNT_DIVISOR
        unit = c.get("unit") or "cash"
        status = c.get("status")
        is_paid = bool(c.get("is_paid"))
        # An outstanding payable = monetary, approved, not yet paid.
        is_cash = unit == "cash"
        outstanding = is_cash and (not is_paid) and status == "approved"
        rows.append(
            {
                "commission_id": c.get("id"),
                "promoter_id": promoter.get("id") or pc.get("promoter_id"),
                "promoter_name": promoter.get("name"),
                "promoter_email": promoter.get("email"),
                "campaign": campaign.get("name"),
                "referral_email": referral.get("email"),
                "status": status,
                "unit": unit,
                "amount": amount,
                "sale_amount": (c.get("sale_amount") or 0) / AMOUNT_DIVISOR,
                "is_paid": is_paid,
                "commission_type": c.get("commission_type"),
                "created_at": created,
                "age_days": age_days,
                "is_outstanding": outstanding,
                "is_overdue": bool(
                    outstanding and age_days is not None and age_days > threshold_days
                ),
                "due_date": (created + timedelta(days=threshold_days))
                if created is not None
                else None,
            }
        )
    df = pd.DataFrame(rows)
    return df


def _payout_method(p: dict) -> Tuple[Optional[str], bool]:
    spm = p.get("selected_payout_method") or {}
    method = spm.get("method")
    disabled = bool(spm.get("is_disabled"))
    has_method = bool(method) and not disabled
    return method, has_method


def promoters_to_df(raw: List[dict]) -> pd.DataFrame:
    rows = []
    for p in raw:
        profile = p.get("profile") or {}
        balances = p.get("balances") or {}
        has_w8 = bool(profile.get("w8_form_url"))
        has_w9 = bool(profile.get("w9_form_url"))
        method, has_method = _payout_method(p)
        rows.append(
            {
                "promoter_id": p.get("id"),
                "promoter_name": p.get("name"),
                "promoter_email": p.get("email"),
                "country": profile.get("country"),
                "company_name": profile.get("company_name"),
                "is_confirmed": bool(p.get("is_confirmed")),
                "has_w8": has_w8,
                "has_w9": has_w9,
                "has_tax_form": has_w8 or has_w9,
                "payout_method": method,
                "has_payout_method": has_method,
                "is_paypal": (method or "").lower() == "paypal",
                "invoice_details_status": p.get("invoice_details_status"),
                "balance_cash": balances.get("cash") or 0,
                "joined_at": _to_dt(p.get("joined_at")),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Build the per-promoter "action" table (the core of the dashboard)
# --------------------------------------------------------------------------
def build_promoter_summary(
    commissions: pd.DataFrame, promoters: pd.DataFrame, threshold_days: int
) -> pd.DataFrame:
    if commissions.empty:
        agg = pd.DataFrame(
            columns=[
                "promoter_id",
                "outstanding_amount",
                "overdue_amount",
                "outstanding_count",
                "overdue_count",
                "oldest_age_days",
            ]
        )
    else:
        out = commissions[commissions["is_outstanding"]]
        agg = (
            out.groupby("promoter_id")
            .apply(
                lambda g: pd.Series(
                    {
                        "outstanding_amount": g["amount"].sum(),
                        "overdue_amount": g.loc[g["is_overdue"], "amount"].sum(),
                        "outstanding_count": len(g),
                        "overdue_count": int(g["is_overdue"].sum()),
                        "oldest_age_days": g["age_days"].max(),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )

    summary = promoters.merge(agg, on="promoter_id", how="left")
    for col in [
        "outstanding_amount",
        "overdue_amount",
        "outstanding_count",
        "overdue_count",
        "oldest_age_days",
    ]:
        if col not in summary:
            summary[col] = 0
        summary[col] = summary[col].fillna(0)

    # Blockers: only matter when money is actually owed.
    owed = summary["outstanding_amount"] > 0
    summary["blocked_missing_tax_form"] = owed & (~summary["has_tax_form"])
    summary["blocked_missing_payout"] = owed & (~summary["has_payout_method"])
    summary["blocked_invoice_details"] = owed & (
        summary["invoice_details_status"].isin(["pending", "denied", "rejected", "not_submitted"])
    )
    summary["is_blocked"] = (
        summary["blocked_missing_tax_form"]
        | summary["blocked_missing_payout"]
        | summary["blocked_invoice_details"]
    )

    def _reasons(r) -> str:
        reasons = []
        if r["blocked_missing_tax_form"]:
            reasons.append("No W8/W9")
        if r["blocked_missing_payout"]:
            reasons.append("No payout method")
        if r["blocked_invoice_details"]:
            reasons.append(f"Invoice details: {r['invoice_details_status']}")
        return ", ".join(reasons) if reasons else ("Ready to pay" if r["outstanding_amount"] > 0 else "")

    summary["blocker_reasons"] = summary.apply(_reasons, axis=1)
    summary["payment_status"] = summary.apply(
        lambda r: "Blocked"
        if r["is_blocked"]
        else ("Ready" if r["outstanding_amount"] > 0 else "Nothing due"),
        axis=1,
    )
    return summary


# --------------------------------------------------------------------------
# Fetch orchestration
# --------------------------------------------------------------------------
def fetch_live(
    api_key: str, account_id: str, threshold_days: int
) -> Dict[str, pd.DataFrame]:
    client = FirstPromoterClient(api_key, account_id)
    commissions_raw = client.get_commissions()
    promoters_raw = client.get_promoters()
    commissions = commissions_to_df(commissions_raw, threshold_days)
    promoters = promoters_to_df(promoters_raw)
    summary = build_promoter_summary(commissions, promoters, threshold_days)
    return {"commissions": commissions, "promoters": promoters, "summary": summary}


# --------------------------------------------------------------------------
# Demo data (used when no credentials are supplied)
# --------------------------------------------------------------------------
def fetch_demo(threshold_days: int, seed: int = 7) -> Dict[str, pd.DataFrame]:
    random.seed(seed)
    now = datetime.now(timezone.utc)
    first_names = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley",
                   "Jamie", "Avery", "Quinn", "Drew", "Cameron", "Reese", "Skyler",
                   "Hayden", "Emerson", "Finley", "Rowan", "Sawyer", "Parker"]
    last_names = ["Lee", "Patel", "Garcia", "Nguyen", "Kim", "Silva", "Müller",
                  "Rossi", "Dubois", "Andersson", "Costa", "Haddad", "Novak",
                  "Okafor", "Tanaka", "Petrov", "Singh", "Walsh", "Romano", "Cruz"]
    countries = ["US", "US", "US", "GB", "CA", "DE", "RO", "AU", "BR", "IN", "FR"]
    methods = ["paypal", "paypal", "paypal", "wise", "bank_transfer", None, None]

    promoters_raw: List[dict] = []
    for i in range(1, 41):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        country = random.choice(countries)
        method = random.choice(methods)
        # US promoters more likely to need W9; foreign need W8.
        has_form = random.random() > 0.30
        w9 = profile_url = None
        w8 = None
        if has_form:
            if country == "US":
                w9 = f"https://files.example.com/w9/{i}.pdf"
            else:
                w8 = f"https://files.example.com/w8/{i}.pdf"
        inv_status = random.choice(
            ["approved", "approved", "approved", "pending", "not_submitted", "denied"]
        )
        promoters_raw.append(
            {
                "id": i,
                "name": f"{fn} {ln}",
                "email": f"{fn.lower()}.{ln.lower().encode('ascii','ignore').decode()}{i}@example.com",
                "is_confirmed": random.random() > 0.1,
                "invoice_details_status": inv_status,
                "joined_at": (now - timedelta(days=random.randint(120, 900))).isoformat(),
                "profile": {
                    "country": country,
                    "company_name": f"{ln} Media" if random.random() > 0.6 else None,
                    "w8_form_url": w8,
                    "w9_form_url": w9,
                },
                "balances": {"cash": 0},
                "selected_payout_method": (
                    {"method": method, "is_disabled": False} if method else None
                ),
            }
        )

    commissions_raw: List[dict] = []
    cid = 1000
    for p in promoters_raw:
        n = random.randint(0, 12)
        for _ in range(n):
            cid += 1
            age = random.randint(1, 240)
            created = now - timedelta(days=age)
            amount_cents = random.choice([1500, 2500, 4900, 9900, 14900, 29900]) * \
                random.randint(1, 3)
            # Older commissions are more likely already paid.
            if age > threshold_days:
                is_paid = random.random() < 0.55
                status = random.choice(["approved", "approved", "approved", "pending"])
            else:
                is_paid = False
                status = random.choice(["approved", "approved", "pending"])
            commissions_raw.append(
                {
                    "id": cid,
                    "status": status,
                    "unit": "cash",
                    "amount": amount_cents,
                    "sale_amount": amount_cents * random.randint(3, 8),
                    "is_paid": is_paid,
                    "commission_type": "sale",
                    "created_at": created.isoformat(),
                    "promoter_campaign": {
                        "promoter_id": p["id"],
                        "promoter": {"id": p["id"], "name": p["name"], "email": p["email"]},
                        "campaign": {"name": random.choice(["Agency", "Affiliate", "Partner"])},
                    },
                    "referral": {"email": f"customer{cid}@client.com"},
                }
            )

    commissions = commissions_to_df(commissions_raw, threshold_days)
    promoters = promoters_to_df(promoters_raw)
    summary = build_promoter_summary(commissions, promoters, threshold_days)
    return {"commissions": commissions, "promoters": promoters, "summary": summary}
