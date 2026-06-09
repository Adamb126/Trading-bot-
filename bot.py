"""
XRP/EUR Trading Bot
Exchanges: Kraken
Strategy:  Multi-indicator confluence (RSI + MACD + Bollinger Bands + EMA + Volume)
Risk:      10% daily profit target | 7.5% daily stop-loss
"""

import logging
import time
from typing import Optional

import config
from exchange import ExchangeClient
from indicators import compute_indicators, ohlcv_to_df
from risk_manager import RiskManager
from strategy import Signal, generate_signal

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

        logger.info(
            f"Position closed ({reason}): {self.position_xrp:.4f} XRP @ {current_price:.4f} EUR "
            f"| Proceeds: {proceeds_eur:.2f} EUR"
        )

        # Reset position
        self.position_xrp = 0.0
        self.avg_entry_price = 0.0
        self.position_cost_eur = 0.0

    # ── Main loop tick ────────────────────────────────────────────────────────

    def tick(self):
        try:
            current_price = self._get_current_price()
            eur_balance = self.client.get_free_balance(config.QUOTE_CURRENCY)

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
