"""Settings. The common ones can also be changed from the command line."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    # The SEC blocks requests without a real contact. Put yours here or pass --sec-agent.
    sec_user_agent: str = "Your Name your.email@example.com"
    sec_max_rps: float = 8.0          # SEC allows 10 requests/second; stay under it

    data_dir: Path = Path("data")

    # which filings make it into the daily brief (open-market trades only)
    min_buy_usd: float = 25_000
    min_sell_usd: float = 250_000
    max_companies: int = 25           # biggest trades first; keeps runs to a few minutes

    lookback_days: int = 90           # related insiders + news window
    chart_months: int = 6
    news_items: int = 10
    holdings_filings: int = 25        # how many of an insider's own filings to scan for holdings

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "briefs"

    def ensure_dirs(self):
        for d in (self.cache_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
