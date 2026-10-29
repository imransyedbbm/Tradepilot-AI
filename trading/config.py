"""Validated operator configuration. Defaults are conservative engineering defaults, not advice."""
from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskLimits(BaseSettings):
    per_trade_risk_pct: Decimal = Decimal("0.25")
    max_daily_loss_pct: Decimal = Decimal("1.0")
    max_open_risk_pct: Decimal = Decimal("2.0")
    max_gross_exposure_pct: Decimal = Decimal("80")
    max_single_position_pct: Decimal = Decimal("15")
    max_positions: int = 5
    max_entries_per_day: int = 10
    max_consecutive_losses: int = 3
    max_spread_pct: Decimal = Decimal("0.15")
    stale_data_seconds: int = 10
    intraday_entry_cutoff: str = "14:45"
    intraday_squareoff: str = "15:10"

    @model_validator(mode="after")
    def consistent(self):
        pos = lambda v, n: (_ for _ in ()).throw(ValueError(f"{n} must be > 0")) if v <= 0 else None  # noqa: E731
        for n in ("per_trade_risk_pct", "max_daily_loss_pct", "max_open_risk_pct",
                  "max_gross_exposure_pct", "max_single_position_pct", "max_spread_pct"):
            pos(getattr(self, n), n)
        if self.max_positions < 1 or self.max_entries_per_day < 1:
            raise ValueError("position/entry limits must be >= 1")
        if self.per_trade_risk_pct > self.max_daily_loss_pct:
            raise ValueError("per-trade risk cannot exceed max daily loss")
        if self.per_trade_risk_pct > self.max_open_risk_pct:
            raise ValueError("per-trade risk cannot exceed max open risk")
        if self.max_single_position_pct > self.max_gross_exposure_pct:
            raise ValueError("single-position cap cannot exceed gross exposure cap")
        if self.max_gross_exposure_pct > 100:
            raise ValueError("leverage is not permitted in the initial release (gross exposure <= 100%)")
        if self.intraday_entry_cutoff >= self.intraday_squareoff:
            raise ValueError("entry cutoff must be before square-off")
        return self


class CapitalAllocation(BaseSettings):
    research_capital: Decimal = Decimal("1000000")
    intraday_pct: Decimal = Decimal("40")
    swing_pct: Decimal = Decimal("50")

    @model_validator(mode="after")
    def no_double_allocation(self):
        if self.research_capital <= 0:
            raise ValueError("capital must be positive")
        if self.intraday_pct < 0 or self.swing_pct < 0:
            raise ValueError("allocations must be non-negative")
        if self.intraday_pct + self.swing_pct > 100:
            raise ValueError("intraday + swing allocation exceeds 100% (double allocation)")
        return self

    @property
    def reserve_pct(self) -> Decimal:
        return Decimal("100") - self.intraday_pct - self.swing_pct


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./tradepilot.db"
    api_token: str = Field(default="dev-only-change-me", min_length=8)
    timezone: str = "Asia/Kolkata"
    # Kite credentials are read only by the adapter, never returned by the API.
    kite_api_key: str = ""
    kite_api_secret: str = ""
    risk: RiskLimits = Field(default_factory=RiskLimits)
    capital: CapitalAllocation = Field(default_factory=CapitalAllocation)
