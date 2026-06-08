# Agency Commission Status

A real-time Streamlit dashboard for the Hyros agency affiliate program on **FirstPromoter**. It surfaces commission payouts that are past the payout threshold (commissions are paid **90 days** after the sale is created, so the dashboard flags anything **older than 90 days**) and — more importantly — shows exactly *what is blocking* each payout: a missing W8/W9 tax form, no PayPal/payout method, or unapproved invoice details.

The data layer is read live from the FirstPromoter v2 API, and everything is packaged so individual views can be embedded inside a larger Streamlit app.

## What's in it

- **Action center** — one row per affiliate owed money, with amount owed, amount overdue, oldest commission age, and the precise blocker(s). Exportable to CSV. *(This is the headline view, tailored for the team — not something FirstPromoter shows directly.)*
- **Overdue** — every commission past the threshold, oldest first.
- **Aging** — AR-style aging buckets (not-yet-due, 90–120d, 120–180d, 180d+).
- **Missing W8/W9** — affiliates owed money with no tax form on file.
- **Missing payout** — affiliates owed money with no usable payout method.
- **Time period** — filter outstanding payouts by sale-creation date, grouped by week or month.
- **Concentration** — top affiliates by amount owed + a compliance-readiness score (% of owed dollars clear to pay).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in FP_API_KEY and FP_ACCOUNT_ID
streamlit run app.py
```

Get your credentials in FirstPromoter under **Settings → Integrations → Manage API Keys** (the Account ID is on the same panel).

You can also paste the key/Account ID straight into the sidebar, or put them in `.streamlit/secrets.toml`:

```toml
FP_API_KEY = "..."
FP_ACCOUNT_ID = "..."
```

No credentials handy? Toggle **Demo mode** in the sidebar to explore the layout with generated sample data.

## Configuration

All settings come from environment variables (overridable in the sidebar):

| Variable | Default | Meaning |
|---|---|---|
| `FP_API_KEY` | — | FirstPromoter API key (Bearer token) |
| `FP_ACCOUNT_ID` | — | FirstPromoter Account ID |
| `FP_THRESHOLD_DAYS` | `90` | Days after the sale before a payout is overdue |
| `FP_CURRENCY` | `$` | Currency symbol for display |
| `FP_CACHE_TTL` | `300` | Seconds to cache API data before refetch |
| `FP_DEMO` | — | Set to `true` to force sample data |

## Embedding in another app

The dashboard is a proper Python package. Drop the `commission_dashboard/` folder into your project and import only the pieces you want:

```python
import streamlit as st
from commission_dashboard import DashboardConfig, load_data, render_action_center

cfg = DashboardConfig(api_key="...", account_id="...", threshold_days=90)
data = load_data(cfg)               # {"commissions", "promoters", "summary"} DataFrames
render_action_center(data, cfg)     # render just this view in your own layout
```

Pure-logic pieces have **no Streamlit dependency** and can run server-side (e.g. in a scheduled job or API):

```python
from commission_dashboard import FirstPromoterClient, fetch_live, build_promoter_summary

data = fetch_live(api_key="...", account_id="...", threshold_days=90)
overdue = data["summary"].query("overdue_amount > 0")
```

## Architecture

```
app.py                       # thin Streamlit entry point (sidebar + tabs)
commission_dashboard/
  __init__.py                # public API
  config.py                  # DashboardConfig (env-driven, no Streamlit)
  fp_client.py               # FirstPromoter v2 API client (auth, pagination)
  data.py                    # fetch + reshape into tidy DataFrames (no Streamlit)
  views.py                   # reusable Streamlit render_* functions
```

## Notes

- The FirstPromoter v2 API authenticates with `Authorization: Bearer <key>` and an `Account-ID` header.
- "Outstanding" = a monetary (`cash`) commission that is `approved` and not yet paid. "Overdue" = outstanding **and** older than the threshold.
- Commission `amount` is returned in minor units (cents); it's divided by 100 for display. Adjust `AMOUNT_DIVISOR` in `data.py` if your account is configured differently.
