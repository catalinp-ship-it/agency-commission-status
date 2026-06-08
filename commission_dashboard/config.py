"""Shared configuration for the commission dashboard.

Importable by other apps so they can reuse the same settings object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:  # Load a local .env if python-dotenv is installed (optional dependency).
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


@dataclass
class DashboardConfig:
    """All tunables for the dashboard in one place.

    Credentials default to environment variables so the module can be
    imported and configured by a parent app without code changes:
        FP_API_KEY, FP_ACCOUNT_ID, FP_THRESHOLD_DAYS, FP_CURRENCY
    """

    api_key: Optional[str] = field(default_factory=lambda: os.getenv("FP_API_KEY"))
    account_id: Optional[str] = field(default_factory=lambda: os.getenv("FP_ACCOUNT_ID"))
    threshold_days: int = field(
        default_factory=lambda: int(os.getenv("FP_THRESHOLD_DAYS", "90"))
    )
    currency_symbol: str = field(default_factory=lambda: os.getenv("FP_CURRENCY", "$"))
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("FP_CACHE_TTL", "300"))
    )
    demo_mode: bool = field(
        default_factory=lambda: os.getenv("FP_DEMO", "").lower() in {"1", "true", "yes"}
    )

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.account_id)

    def money(self, value: float) -> str:
        try:
            return f"{self.currency_symbol}{value:,.2f}"
        except (TypeError, ValueError):
            return f"{self.currency_symbol}0.00"
