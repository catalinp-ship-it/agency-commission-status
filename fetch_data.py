"""Standalone script to fetch FirstPromoter data and save as JSON.

Run locally or via GitHub Actions every 6 hours.

SAFETY:  If the API returns fewer than MIN_REWARDS records the script aborts
         and leaves the existing JSON files untouched.  This prevents a
         rate-limited or mis-configured run from wiping good data.

Usage:
    FP_API_KEY=your_key python fetch_data.py
    FP_API_KEY=your_key python fetch_data.py --force   # bypass MIN_REWARDS guard
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY  = os.environ.get("FP_API_KEY", "")
BASE_URL = "https://firstpromoter.com/api/v1"
PER_PAGE = 100
DATA_DIR = Path(__file__).parent / "data"

# Safety threshold — abort if the API returns fewer rewards than this.
# Set to 0 (or use --force) to skip the check.
MIN_REWARDS = int(os.environ.get("FP_MIN_REWARDS", "50"))

if not API_KEY:
    sys.exit("FP_API_KEY environment variable is required.")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})

_RETRYABLE = {429, 502, 503, 504}
_RETRY_DELAYS = [5, 15, 30]


def _get(path: str, params: dict) -> requests.Response:
    url = f"{BASE_URL}/{path}"
    resp = None
    for delay in [0] + _RETRY_DELAYS:
        if delay:
            print(f"  Retrying in {delay}s...")
            time.sleep(delay)
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code not in _RETRYABLE:
            break
    return resp


def fetch_all(path: str) -> list:
    rows: list = []
    seen_first_id = None
    page = 1
    while True:
        resp = _get(path, {"page": page, "per_page": PER_PAGE})
        if not resp.ok:
            print(f"  Error {resp.status_code} on page {page}: {resp.text[:200]}")
            break
        batch = resp.json()
        if isinstance(batch, dict):
            batch = batch.get("data", [])
        if not batch:
            break
        # Loop-guard: some endpoints repeat the last page forever.
        first_id = batch[0].get("id")
        if first_id is not None and first_id == seen_first_id and page > 1:
            print(f"  Duplicate first ID {first_id} detected -- stopping.")
            break
        seen_first_id = first_id

        rows.extend(batch)
        print(f"  Page {page}: {len(batch)} rows (total {len(rows)})")

        if len(batch) < PER_PAGE:
            break
        page += 1
    return rows


def _atomic_write(path: Path, data: list) -> None:
    """Write to a temp file then rename -- prevents partial writes on interruption."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the MIN_REWARDS safety check and overwrite even if count is low.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Rewards
    # ------------------------------------------------------------------
    print("Fetching all rewards (full history)...")
    rewards = fetch_all("rewards/list")
    print(f"Fetched {len(rewards)} rewards total.")

    if not args.force and MIN_REWARDS > 0 and len(rewards) < MIN_REWARDS:
        existing = DATA_DIR / "rewards.json"
        existing_count = 0
        if existing.exists():
            try:
                existing_count = len(json.loads(existing.read_text()))
            except Exception:
                pass
        print(
            f"\nABORTED: API returned only {len(rewards)} rewards "
            f"(minimum is {MIN_REWARDS}).\n"
            f"   Existing file has {existing_count} records and was NOT overwritten.\n"
            f"   Use --force to override this check."
        )
        sys.exit(1)

    _atomic_write(DATA_DIR / "rewards.json", rewards)
    print(f"Saved {len(rewards)} rewards -> data/rewards.json")

    # ------------------------------------------------------------------
    # Promoters
    # ------------------------------------------------------------------
    print("\nFetching promoters...")
    promoters = fetch_all("promoters/list")
    _atomic_write(DATA_DIR / "promoters.json", promoters)
    print(f"Saved {len(promoters)} promoters -> data/promoters.json")

    # ------------------------------------------------------------------
    # Manifest (last-write metadata used by the dashboard)
    # ------------------------------------------------------------------
    manifest = {
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
        "rewards_count":   len(rewards),
        "promoters_count": len(promoters),
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nDone -- {len(rewards)} rewards, {len(promoters)} promoters.")


if __name__ == "__main__":
    main()
