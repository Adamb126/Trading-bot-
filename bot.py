"""
XRP/EUR Trading Bot
Exchanges: Kraken
Strategy:  Multi-indicator confluence (RSI + MACD + Bollinger Bands + EMA + Volume)
Risk:      10% daily profit target | 7.5% daily stop-loss
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from exchange import ExchangeClient
from indicators import compute_indicators, ohlcv_to_df
from risk_manager import RiskManager
from strategy import Signal, generate_signal

DATA_FILE = Path("data/status.json")

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        self.client = ExchangeClient()
        self.risk = RiskManager()

        # Open position tracking
        self.position_xrp: float = 0.0      # XRP held
        self.avg_entry_price: float = 0.0   # Average EUR cost per XRP
        self.position_cost_eur: float = 0.0  # Total EUR spent on current position

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_current_price(self) -> float:
        ticker = self.client.fetch_ticker(config.SYMBOL)
        return float(ticker["last"])

    def _fetch_df(self):
        raw = self.client.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, config.LOOKBACK_CANDLES)
        df = ohlcv_to_df(raw)
        return compute_indicators(df)

    def _sync_position(self):
        """Read actual XRP balance from the exchange."""
        if not config.DRY_RUN:
            self.position_xrp = self.client.get_free_balance(config.BASE_CURRENCY)

    # ── Order execution ───────────────────────────────────────────────────────

    def _execute_buy(self, current_price: float):
        eur_available = self.client.get_free_balance(config.QUOTE_CURRENCY)
        amount_eur = min(config.TRADE_AMOUNT_EUR, eur_available)
        if amount_eur < 5:
            logger.warning("Not enough EUR balance to trade (minimum 5 EUR).")
            return

        order = self.client.create_market_buy(config.SYMBOL, amount_eur, current_price)
        if order is None:
            return

        filled_price = order.get("price") or current_price
        filled_xrp = order.get("amount") or (amount_eur / current_price)
        cost_eur = order.get("cost") or amount_eur

        # Update position tracking (weighted average entry)
        total_xrp = self.position_xrp + filled_xrp
        if total_xrp > 0:
            self.avg_entry_price = (
                (self.avg_entry_price * self.position_xrp + filled_price * filled_xrp)
                / total_xrp
            )
        self.position_xrp = total_xrp
        self.position_cost_eur += cost_eur
        self.risk.record_trade_open(cost_eur)
        self.record_trade("BUY", filled_xrp, filled_price, cost_eur)
        logger.info(
            f"Position opened: {filled_xrp:.4f} XRP @ {filled_price:.4f} EUR "
            f"| Total held: {self.position_xrp:.4f} XRP"
        )

    def _execute_sell(self, current_price: float, reason: str = "signal"):
        if self.position_xrp <= 0:
            logger.info("No position to sell.")
            return

        order = self.client.create_market_sell(config.SYMBOL, self.position_xrp, current_price)
        if order is None:
            return

        proceeds_eur = order.get("cost") or (self.position_xrp * current_price)
        self.risk.record_trade_close(self.position_cost_eur, proceeds_eur)
        self.record_trade("SELL", self.position_xrp, current_price, proceeds_eur)

        logger.info(
            f"Position closed ({reason}): {self.position_xrp:.4f} XRP @ {current_price:.4f} EUR "
            f"| Proceeds: {proceeds_eur:.2f} EUR"
        )

        # Reset position
        self.position_xrp = 0.0
        self.avg_entry_price = 0.0
        self.position_cost_eur = 0.0

    # ── Status file ───────────────────────────────────────────────────────────

    def _write_status(self, current_price: float, signal, eur_balance: float):
        try:
            DATA_FILE.parent.mkdir(exist_ok=True)
            unreal_pct = 0.0
            if self.position_xrp > 0 and self.avg_entry_price > 0:
                unreal_pct = (current_price - self.avg_entry_price) / self.avg_entry_price * 100
            data = {
                "updated":        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "price":          current_price,
                "dry_run":        config.DRY_RUN,
                "eur_balance":    round(eur_balance, 2),
                "xrp_held":       round(self.position_xrp, 4),
                "avg_entry":      round(self.avg_entry_price, 4),
                "position_value": round(self.position_xrp * current_price, 2),
                "unreal_pct":     round(unreal_pct, 2),
                "total_value":    round(eur_balance + self.position_xrp * current_price, 2),
                "daily_pnl":      round(self.risk.stats.realized_pnl_eur, 2),
                "daily_pnl_pct":  round(self.risk.stats.realized_pnl_eur / self.risk.stats.starting_eur * 100
                                        if self.risk.stats.starting_eur else 0, 2),
                "daily_trades":   self.risk.stats.trade_count,
                "daily_starting": round(self.risk.stats.starting_eur, 2),
                "locked":         self.risk.stats.locked,
                "signal":         signal.signal.value,
                "signal_conf":    round(signal.confidence, 2),
                "signal_reason":  signal.reason,
            }
            # Append trade to history list
            existing = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
            data["trades"] = existing.get("trades", [])
            DATA_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Failed to write status file: {e}")

    def record_trade(self, side: str, xrp: float, price: float, cost: float):
        try:
            existing = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
            trades = existing.get("trades", [])
            trades.insert(0, {
                "time":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                "side":  side,
                "xrp":   round(xrp, 4),
                "price": round(price, 4),
                "cost":  round(cost, 2),
            })
            existing["trades"] = trades[:50]  # keep last 50
            DATA_FILE.write_text(json.dumps(existing, indent=2))
        except Exception as e:
            logger.warning(f"Failed to record trade: {e}")

    # ── Main loop tick ────────────────────────────────────────────────────────

    def _load_position(self):
        """Restore position state from status.json and sync XRP balance from exchange."""
        self._sync_position()  # get actual XRP held from Kraken
        if self.position_xrp > 0 and DATA_FILE.exists():
            try:
                saved = json.loads(DATA_FILE.read_text())
                self.avg_entry_price = float(saved.get("avg_entry", 0.0))
                self.position_cost_eur = float(saved.get("position_value", 0.0))
            except Exception:
                pass

    def tick(self):
        try:
            current_price = self._get_current_price()
            eur_balance = self.client.get_free_balance(config.QUOTE_CURRENCY)

            # Restore position from exchange + saved state on every tick
            self._load_position()

            # Initialise daily starting balance on first tick of the day
            self.risk.set_starting_balance(eur_balance + self.position_xrp * current_price)

            # Always check for emergency sell regardless of locked state
            if self.position_xrp > 0:
                if self.risk.should_emergency_sell(
                    current_price, self.avg_entry_price, self.position_xrp
                ):
                    self._execute_sell(current_price, reason="stop-loss")
                    return

            if not self.risk.can_trade():
                logger.info(f"Trading locked. {self.risk.summary()}")
                return

            df = self._fetch_df()
            signal = generate_signal(df)

            logger.info(
                f"Price: {current_price:.4f} EUR | Signal: {signal.signal.value} "
                f"(conf={signal.confidence:.2f}) | {signal.reason}"
            )

            if signal.signal == Signal.BUY and self.position_xrp == 0:
                self._execute_buy(current_price)

            elif signal.signal == Signal.SELL and self.position_xrp > 0:
                self._execute_sell(current_price, reason="signal")

            logger.debug(self.risk.summary())
            self._write_status(current_price, signal, eur_balance)

        except Exception as e:
            logger.error(f"Error in tick: {e}", exc_info=True)

    def run(self, interval_seconds: int = 60):
        logger.info(
            f"Starting XRP/EUR trading bot | "
            f"Target: +{config.DAILY_PROFIT_TARGET*100:.0f}% | "
            f"Stop-loss: -{config.DAILY_STOP_LOSS*100:.1f}% | "
            f"Dry-run: {config.DRY_RUN}"
        )
        while True:
            self.tick()
            time.sleep(interval_seconds)


if __name__ == "__main__":
    bot = TradingBot()
    # Run every 60 seconds (one tick per minute; analysis uses 15m candles)
    bot.run(interval_seconds=60)
