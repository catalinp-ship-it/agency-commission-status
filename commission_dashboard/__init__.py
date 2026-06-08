"""Agency commission status dashboard — reusable package.

Public API for embedding in other Streamlit apps:

    from commission_dashboard import (
        DashboardConfig, load_data,
        render_kpi_header, render_action_center, render_overdue_view,
        render_aging_view, render_tax_form_view, render_payout_method_view,
        render_time_period_view, render_concentration_view,
    )

    cfg = DashboardConfig(api_key="...", account_id="...", threshold_days=90)
    data = load_data(cfg)
    render_action_center(data, cfg)

Pure-logic pieces (no Streamlit dependency) live in `fp_client` and `data`
and can be reused server-side:

    from commission_dashboard import (
        FirstPromoterClient, fetch_live, fetch_demo, build_promoter_summary,
    )
"""

from .config import DashboardConfig
from .fp_client import FirstPromoterClient, FirstPromoterError
from .data import (
    fetch_live,
    fetch_demo,
    commissions_to_df,
    promoters_to_df,
    build_promoter_summary,
)
from .views import (
    load_data,
    render_kpi_header,
    render_action_center,
    render_overdue_view,
    render_aging_view,
    render_tax_form_view,
    render_payout_method_view,
    render_time_period_view,
    render_concentration_view,
)

__all__ = [
    "DashboardConfig",
    "FirstPromoterClient",
    "FirstPromoterError",
    "fetch_live",
    "fetch_demo",
    "commissions_to_df",
    "promoters_to_df",
    "build_promoter_summary",
    "load_data",
    "render_kpi_header",
    "render_action_center",
    "render_overdue_view",
    "render_aging_view",
    "render_tax_form_view",
    "render_payout_method_view",
    "render_time_period_view",
    "render_concentration_view",
]

__version__ = "1.0.0"
