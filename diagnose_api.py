"""Quick FirstPromoter credential check.

Usage:
    python diagnose_api.py YOUR_API_KEY [ACCOUNT_ID]

Tests the key against both API versions and reports which one works.
"""

import sys

import requests


def test_v1(key: str) -> None:
    r = requests.get(
        "https://firstpromoter.com/api/v1/promoters/list",
        headers={"X-API-KEY": key, "Accept": "application/json"},
        params={"page": 1, "per_page": 1},
        timeout=20,
    )
    print(f"v1 (X-API-KEY):           HTTP {r.status_code}  {'OK - key works on v1' if r.ok else r.text[:200]}")


def test_v2(key: str, account_id: str | None) -> None:
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if account_id:
        headers["Account-ID"] = account_id
    r = requests.get(
        "https://api.firstpromoter.com/api/v2/company/promoters",
        headers=headers,
        params={"page": 1},
        timeout=20,
    )
    note = "OK - key works on v2" if r.ok else r.text[:200]
    if not account_id:
        note += "  (no Account-ID given - v2 requires it)"
    print(f"v2 (Bearer + Account-ID): HTTP {r.status_code}  {note}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    api_key = sys.argv[1]
    acct = sys.argv[2] if len(sys.argv) > 2 else None
    test_v1(api_key)
    test_v2(api_key, acct)
