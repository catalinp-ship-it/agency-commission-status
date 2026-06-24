"""Standalone script to fetch FirstPromoter data and save as JSON.

Run locally or via GitHub Actions. Saves to data/rewards.json and data/promoters.json
so the Streamlit app can load them without hitting the API directly.

Usage:
    FP_API_KEY=your_key python fetch_data.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.environ.get("FP_API_KEY", "")
BASE_URL = "https://firstpromoter.com/api/v1"
PER_PAGE = 100
LOOKBACK_MONTHS = int(os.environ.get("FP_LOOKBACK_MONTHS", "6"))
DATA_DIR = Path(__file__).parent / "data"

if not API_KEY:
    sys.exit("FP_API_KEY environment variable is required.")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})


def fetch_all(path: str, cutoff_date=None) -> list:
    rows = []
    page = 1
    seen = set()
    while True:
        resp = session.get(f"{BASE_URL}/{path}", params={"page": page, "per_page": PER_PAGE}, timeout=30)
        if not resp.ok:
            print(f"  Error {resp.status_code} on page {page}: {resp.text[:200]}")
            break
        batch = resp.json()
        if isinstance(batch, dict):
            batch = batch.get("data", [])
        if not batch:
            break
        first_id = batch[0].get("id")
        if first_id in seen and page > 1:
            break
        seen.add(first_id)
        if cutoff_date:
            filtered = []
            stop = False
            for row in batch:
                raw = row.get("created_at", "")
                if raw:
                    try:
                        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff_date:
                            stop = True
                            break
                    except Exception:
                        pass
                filtered.append(row)
            rows.extend(filtered)
            print(f"  Page {page}: {len(filtered)} rows (total {len(rows)})")
            if stop:
                print(f"  Reached cutoff date, stopping.")
                break
        else:
            rows.extend(batch)
            print(f"  Page {page}: {len(batch)} rows (total {len(rows)})")
        if len(batch) < PER_PAGE:
            break
        page += 1
    return rows


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    month = now.month - LOOKBACK_MONTHS
    year = now.year + month // 12
    month = month % 12 or 12
    cutoff = now.replace(year=year, month=month)
    print(f"Fetching rewards since {cutoff.date()} ({LOOKBACK_MONTHS} months)...")

    rewards = fetch_all("rewards/list", cutoff_date=cutoff)
    out = DATA_DIR / "rewards.json"
    out.write_text(json.dumps(rewards, indent=2))
    print(f"Saved {len(rewards)} rewards to {out}")

    print("Fetching promoters...")
    promoters = fetch_all("promoters/list")
    out = DATA_DIR / "promoters.json"
    out.write_text(json.dumps(promoters, indent=2))
    print(f"Saved {len(promoters)} promoters to {out}")

    # Write a manifest so the app knows when data was last fetched.
    import datetime
    manifest = {
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "rewards_count": len(rewards),
        "promoters_count": len(promoters),
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("Done.")


if __name__ == "__main__":
    main()
