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
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE_URL = "https://api.firstpromoter.com/api/v2/company"
DEFAULT_TIMEOUT = 30
MAX_PAGES = 500  # safety cap


class FirstPromoterError(Exception):
    """Raised when the FirstPromoter API returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        built = _build_params(params)
        # Retry on transient errors (429 rate-limit, 503/502/504 server hiccups).
        _RETRYABLE = {429, 502, 503, 504}
        _DELAYS = [2, 5, 10]
        resp = None
        for attempt, delay in enumerate([0] + _DELAYS):
            if delay:
                time.sleep(delay)
            resp = self.session.get(url, params=built, timeout=self.timeout)
            if resp.status_code not in _RETRYABLE:
                break
        assert resp is not None
        if resp.status_code == 401:
            raise FirstPromoterError(
                "Unauthorized (401). Check your API key.", status_code=401
            )
        if resp.status_code == 403:
            raise FirstPromoterError(
                "Forbidden (403). Check your Account-ID / key permissions.", status_code=403
            )
        if not resp.ok:
            raise FirstPromoterError(
                f"API error {resp.status_code} on {path}: {resp.text[:300]}",
                status_code=resp.status_code,
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

    def _paginate(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        per_page: int = 100,
        cutoff_date: Optional[datetime] = None,
    ) -> List[dict]:
        """Fetch every page. Stops at cutoff_date (records are newest-first)."""
        params = dict(params or {})
        params.setdefault("per_page", per_page)
        page_size = params["per_page"]
        rows: List[dict] = []
        seen_first_ids: set = set()
        page = 1
        while page <= MAX_PAGES:
            params["page"] = page
            payload = self._get(path, params).json()
            batch = self._extract_rows(payload)
            if not batch:
                break
            first_id = batch[0].get("id")
            if first_id is not None:
                if first_id in seen_first_ids and page > 1:
                    break
                seen_first_ids.add(first_id)
            if cutoff_date:
                filtered = []
                stop = False
                for row in batch:
                    raw_date = row.get("created_at")
                    if raw_date:
                        try:
                            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt < cutoff_date:
                                stop = True
                                break
                        except (ValueError, AttributeError):
                            pass
                    filtered.append(row)
                rows.extend(filtered)
                if stop:
                    break
            else:
                rows.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return rows

    # ---- high level ----------------------------------------------------
    def get_commissions(
        self,
        filters: Optional[Dict[str, Any]] = None,
        cutoff_date: Optional[datetime] = None,
    ) -> List[dict]:
        """All commissions/rewards. Optionally pass server-side `filters`."""
        params: Dict[str, Any] = {}
        if filters:
            params["filters"] = filters
        return self._paginate("commissions", params, cutoff_date=cutoff_date)

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


V1_BASE_URL = "https://firstpromoter.com/api/v1"


def _make_cf_session(api_key: str, extra_headers: Optional[Dict[str, str]] = None):
    """Return a cloudscraper session that mimics a real browser to bypass Cloudflare WAF.

    Falls back to a plain requests.Session if cloudscraper is not installed.
    """
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        import cloudscraper  # type: ignore
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        scraper.headers.update(headers)
        return scraper
    except Exception:  # noqa: BLE001 — cloudscraper not installed, degrade gracefully
        session = requests.Session()
        session.headers.update(headers)
        return session


class FirstPromoterV1Client:
    """Client for the FirstPromoter v1 API (API-key only, no Account-ID required).

    Uses cloudscraper to mimic a real browser and bypass Cloudflare WAF rate-limiting,
    which blocks plain requests from shared hosting IPs (e.g. Streamlit Cloud).
    Endpoints live under https://firstpromoter.com/api/v1.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = V1_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        per_page: int = 100,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.per_page = per_page
        self.session = _make_cf_session(api_key)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        built = params or {}
        _RETRYABLE = {429, 502, 503, 504}
        _DELAYS = [2, 5, 10]
        resp = None
        for delay in [0] + _DELAYS:
            if delay:
                time.sleep(delay)
            resp = self.session.get(url, params=built, timeout=self.timeout)
            if resp.status_code not in _RETRYABLE:
                break
        assert resp is not None
        if resp.status_code == 401:
            raise FirstPromoterError(
                "Unauthorized (401). Check your API key.", status_code=401
            )
        if not resp.ok:
            raise FirstPromoterError(
                f"API v1 error {resp.status_code} on {path}: {resp.text[:300]}",
                status_code=resp.status_code,
            )
        return resp

    def _paginate(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        cutoff_date: Optional[datetime] = None,
    ) -> List[dict]:
        params = dict(params or {})
        params["per_page"] = self.per_page
        rows: List[dict] = []
        seen_first_ids: set = set()
        page = 1
        while page <= MAX_PAGES:
            params["page"] = page
            payload = self._get(path, params).json()
            batch = payload if isinstance(payload, list) else payload.get("data", [])
            if not batch:
                break
            first_id = batch[0].get("id")
            if first_id is not None:
                if first_id in seen_first_ids and page > 1:
                    break
                seen_first_ids.add(first_id)
            if cutoff_date:
                filtered = []
                stop = False
                for row in batch:
                    raw_date = row.get("created_at")
                    if raw_date:
                        try:
                            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt < cutoff_date:
                                stop = True
                                break
                        except (ValueError, AttributeError):
                            pass
                    filtered.append(row)
                rows.extend(filtered)
                if stop:
                    break
            else:
                rows.extend(batch)
            if len(batch) < self.per_page:
                break
            page += 1
        return rows

    def get_promoters(self) -> List[dict]:
        return self._paginate("promoters/list")

    def get_rewards(self, cutoff_date: Optional[datetime] = None) -> List[dict]:
        """All rewards/commissions (the v1 equivalent of commissions)."""
        return self._paginate("rewards/list", cutoff_date=cutoff_date)

    def ping(self) -> bool:
        self._get("promoters/list", {"page": 1, "per_page": 1})
        return True
