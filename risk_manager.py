import logging
from datetime import date
from dataclasses import dataclass, field
from typing import Optional
import config

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    date: date = field(default_factory=date.today)
    starting_eur: float = 0.0
    realized_pnl_eur: float = 0.0
    open_position_cost_eur: float = 0.0
    trade_count: int = 0
    locked: bool = False  # True when daily target or stop-loss hit


class RiskManager:
    def __init__(self):
        self.stats = DailyStats()
        self._reset_if_new_day()

    def _reset_if_new_day(self):
        today = date.today()
        if self.stats.date != today:
            logger.info(f"New trading day {today}. Resetting daily stats.")
            self.stats = DailyStats(date=today)

    def set_starting_balance(self, eur_balance: float):
        self._reset_if_new_day()
        if self.stats.starting_eur == 0.0:
            self.stats.starting_eur = eur_balance
            logger.info(f"Daily starting balance: {eur_balance:.2f} EUR")

    def record_trade_open(self, cost_eur: float):
        self.stats.open_position_cost_eur += cost_eur

    def record_trade_close(self, cost_eur: float, proceeds_eur: float):
        pnl = proceeds_eur - cost_eur
        self.stats.realized_pnl_eur += pnl
        self.stats.open_position_cost_eur = max(0.0, self.stats.open_position_cost_eur - cost_eur)
        self.stats.trade_count += 1
        logger.info(
            f"Trade closed. PnL: {pnl:+.2f} EUR | "
            f"Daily PnL: {self.stats.realized_pnl_eur:+.2f} EUR"
        )
        self._check_daily_limits()

    def _check_daily_limits(self):
        if self.stats.starting_eur == 0:
            return
        pnl_pct = self.stats.realized_pnl_eur / self.stats.starting_eur

        if pnl_pct >= config.DAILY_PROFIT_TARGET:
            logger.info(
                f"Daily profit target reached: {pnl_pct*100:.2f}% "
                f"(target {config.DAILY_PROFIT_TARGET*100:.0f}%). Locking trading."
            )
            self.stats.locked = True

        elif pnl_pct <= -config.DAILY_STOP_LOSS:
            logger.warning(
                f"Daily stop-loss triggered: {pnl_pct*100:.2f}% "
                f"(limit -{config.DAILY_STOP_LOSS*100:.0f}%). Locking trading."
            )
            self.stats.locked = True

    def can_trade(self) -> bool:
        self._reset_if_new_day()
        if self.stats.locked:
            return False
        return True

    def unrealized_pnl_pct(self, current_price: float, avg_entry_price: float, xrp_held: float) -> float:
        if avg_entry_price == 0 or xrp_held == 0:
            return 0.0
        market_value = xrp_held * current_price
        cost = xrp_held * avg_entry_price
        return (market_value - cost) / cost

    def should_emergency_sell(
        self,
        current_price: float,
        avg_entry_price: float,
        xrp_held: float,
    ) -> bool:
        """Trigger an intra-day emergency sell if unrealized loss would breach stop-loss."""
        if xrp_held == 0 or avg_entry_price == 0 or self.stats.starting_eur == 0:
            return False
        unrealized_pnl_eur = (current_price - avg_entry_price) * xrp_held
        total_loss = self.stats.realized_pnl_eur + unrealized_pnl_eur
        total_loss_pct = total_loss / self.stats.starting_eur
        if total_loss_pct <= -config.DAILY_STOP_LOSS:
            logger.warning(
                f"Emergency sell: total P&L would reach {total_loss_pct*100:.2f}% "
                f"(stop-loss -{config.DAILY_STOP_LOSS*100:.0f}%)"
            )
            return True
        return False

    def summary(self) -> str:
        pnl_pct = (
            self.stats.realized_pnl_eur / self.stats.starting_eur * 100
            if self.stats.starting_eur else 0
        )
        return (
            f"Date: {self.stats.date} | "
            f"Starting: {self.stats.starting_eur:.2f} EUR | "
            f"PnL: {self.stats.realized_pnl_eur:+.2f} EUR ({pnl_pct:+.2f}%) | "
            f"Trades: {self.stats.trade_count} | "
            f"Locked: {self.stats.locked}"
        )
