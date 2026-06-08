"""FirstPromoter API v2 client.

Thin wrapper around the FirstPromoter v2 "company" (admin) API used by the
Agency commission dashboard. Handles auth, nested query params and pagination,
and exposes typed-ish helpers that return plain lists of dicts.

Auth (per docs.firstpromoter.com):
    Authorization: Bearer <API_KEY>
    Account-ID: <ACCOUNT_ID>
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE_URL = "https://api.firstpromoter.com/api/v2/company"
DEFAULT_TIMEOUT = 30
MAX_PAGES = 500  # safety cap


class FirstPromoterError(Exception):
    """Raised when the FirstPromoter API returns an error."""


def _flatten(prefix: str, value: Any, out: Dict[str, Any]) -> None:
    """Flatten a nested dict/list into bracketed query-param keys.

    {"filters": {"status": "approved"}} -> {"filters[status]": "approved"}
    {"ids": [1, 2]}                      -> {"ids[]": [1, 2]}
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]", v, out)
    elif isinstance(value, (list, tuple)):
        out[f"{prefix}[]"] = list(value)
    else:
        out[prefix] = value


def _build_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not params:
        return {}
    out: Dict[str, Any] = {}
    for key, value in params.items():
        _flatten(key, value, out)
    return out


class FirstPromoterClient:
    def __init__(
        self,
        api_key: str,
        account_id: str,
        base_url: str = BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key or not account_id:
            raise ValueError("api_key and account_id are required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Account-ID": str(account_id),
                "Accept": "application/json",
            }
        )

    # ---- low level -----------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, params=_build_params(params), timeout=self.timeout)
        if resp.status_code == 401:
            raise FirstPromoterError(
                "Unauthorized (401). Check your API key."
            )
        if resp.status_code == 403:
            raise FirstPromoterError(
                "Forbidden (403). Check your Account-ID / key permissions."
            )
        if resp.status_code == 429:
            # basic backoff on rate limit
            time.sleep(2)
            resp = self.session.get(url, params=_build_params(params), timeout=self.timeout)
        if not resp.ok:
            raise FirstPromoterError(
                f"API error {resp.status_code} on {path}: {resp.text[:300]}"
            )
        return resp

    @staticmethod
    def _extract_rows(payload: Any) -> List[dict]:
        """Endpoints return either a bare array or {"data": [...]}."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                return payload["data"]
        return []

    def _paginate(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[dict]:
        """Fetch every page. FirstPromoter paginates via ?page=N (25/page).

        Stops when a page returns no rows or repeats the previous page.
        """
        params = dict(params or {})
        rows: List[dict] = []
        seen_first_ids: set = set()
        page = 1
        while page <= MAX_PAGES:
            params["page"] = page
            payload = self._get(path, params).json()
            batch = self._extract_rows(payload)
            if not batch:
                break
            # guard against APIs that ignore `page` and keep returning page 1
            first_id = batch[0].get("id")
            if first_id is not None:
                if first_id in seen_first_ids and page > 1:
                    break
                seen_first_ids.add(first_id)
            rows.extend(batch)
            if len(batch) < 25:  # last (short) page
                break
            page += 1
        return rows

    # ---- high level ----------------------------------------------------
    def get_commissions(self, filters: Optional[Dict[str, Any]] = None) -> List[dict]:
        """All commissions/rewards. Optionally pass server-side `filters`."""
        params: Dict[str, Any] = {}
        if filters:
            params["filters"] = filters
        return self._paginate("commissions", params)

    def get_promoters(self, filters: Optional[Dict[str, Any]] = None) -> List[dict]:
        """All promoters with profile, payout method and balances."""
        params: Dict[str, Any] = {}
        if filters:
            params["filters"] = filters
        return self._paginate("promoters", params)

    def get_payouts(self, filters: Optional[Dict[str, Any]] = None) -> List[dict]:
        params: Dict[str, Any] = {}
        if filters:
            params["filters"] = filters
        return self._paginate("payouts", params)

    def due_payout_stats(self) -> dict:
        return self._get("payouts/due_stats").json()

    def ping(self) -> bool:
        """Lightweight credential check."""
        self._get("promoters", {"page": 1})
        return True
