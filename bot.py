"""
Multi-stock trading bot (Alpaca).
Trades a small watchlist of US stocks using multi-indicator confluence
(RSI + MACD + Bollinger Bands + EMA + Volume).

One independent position per symbol. Shared daily risk limits across the book.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import config
from alpaca_client import AlpacaClient
from indicators import compute_indicators, ohlcv_to_df
from risk_manager import RiskManager
from strategy import Signal, generate_signal

DATA_FILE = Path("data/status.json")

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class Position:
    """Tracks one symbol's open position."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.qty: float = 0.0
        self.avg_entry: float = 0.0
        self.cost_usd: float = 0.0


class TradingBot:
    def __init__(self):
        self.client = AlpacaClient()
        self.risk = RiskManager()
        self.positions: dict[str, Position] = {s: Position(s) for s in config.WATCHLIST}

    # ── Data ──────────────────────────────────────────────────────────────────

    def _fetch_df(self, symbol: str):
        raw = self.client.fetch_ohlcv(symbol, config.TIMEFRAME, config.LOOKBACK_CANDLES)
        if not raw:
            return None
        df = ohlcv_to_df(raw)
        return compute_indicators(df)

    def _sync_position(self, pos: Position):
        """Pull live share qty + avg entry from Alpaca."""
        pos.qty = self.client.get_position_qty(pos.symbol)
        if pos.qty > 0:
            pos.avg_entry = self.client.get_position_avg_entry(pos.symbol)
            pos.cost_usd = pos.qty * pos.avg_entry

    # ── Orders ────────────────────────────────────────────────────────────────

    def _execute_buy(self, pos: Position, price: float, cash: float):
        amount = min(config.TRADE_AMOUNT_EUR, cash)
        if amount < 1:
            logger.warning(f"{pos.symbol}: not enough cash to buy (${cash:.2f}).")
            return
        order = self.client.create_market_buy(pos.symbol, amount, price)
        if order is None:
            return
        filled_qty = order.get("amount") or (amount / price)
        cost = order.get("cost") or amount
        pos.qty += filled_qty
        pos.avg_entry = price
        pos.cost_usd += cost
        self.risk.record_trade_open(cost)
        self.record_trade(pos.symbol, "BUY", filled_qty, price, cost)
        logger.info(f"{pos.symbol}: opened {filled_qty:.4f} sh @ ${price:.2f}")

    def _execute_sell(self, pos: Position, price: float, reason: str = "signal"):
        if pos.qty <= 0:
            return
        order = self.client.create_market_sell(pos.symbol, pos.qty, price)
        if order is None:
            return
        proceeds = order.get("cost") or (pos.qty * price)
        self.risk.record_trade_close(pos.cost_usd, proceeds)
        self.record_trade(pos.symbol, "SELL", pos.qty, price, proceeds)
        logger.info(f"{pos.symbol}: closed ({reason}) {pos.qty:.4f} sh @ ${price:.2f} -> ${proceeds:.2f}")
        pos.qty = 0.0
        pos.avg_entry = 0.0
        pos.cost_usd = 0.0

    # ── Status file (read by the dashboard) ───────────────────────────────────

    def _read_status(self) -> dict:
        try:
            return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
        except Exception:
            return {}

    def _write_status(self, cash: float, prices: dict, signals: dict):
        try:
            DATA_FILE.parent.mkdir(exist_ok=True)
            existing = self._read_status()

            holdings = []
            positions_value = 0.0
            for sym, pos in self.positions.items():
                price = prices.get(sym, 0.0)
                mv = pos.qty * price
                positions_value += mv
                unreal = ((price - pos.avg_entry) / pos.avg_entry * 100) if pos.avg_entry > 0 else 0.0
                sig = signals.get(sym)
                holdings.append({
                    "symbol":     sym,
                    "qty":        round(pos.qty, 4),
                    "avg_entry":  round(pos.avg_entry, 2),
                    "price":      round(price, 2),
                    "value":      round(mv, 2),
                    "unreal_pct": round(unreal, 2),
                    "signal":     sig.signal.value if sig else "HOLD",
                    "reason":     sig.reason if sig else "",
                })

            data = {
                "updated":       datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "paper":         config.ALPACA_PAPER,
                "cash":          round(cash, 2),
                "positions_value": round(positions_value, 2),
                "total_value":   round(cash + positions_value, 2),
                "daily_pnl":     round(self.risk.stats.realized_pnl_eur, 2),
                "daily_pnl_pct": round(self.risk.stats.realized_pnl_eur / self.risk.stats.starting_eur * 100
                                       if self.risk.stats.starting_eur else 0, 2),
                "daily_trades":  self.risk.stats.trade_count,
                "locked":        self.risk.stats.locked,
                "holdings":      holdings,
                "trades":        existing.get("trades", []),
            }
            DATA_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Failed to write status file: {e}")

    def record_trade(self, symbol: str, side: str, qty: float, price: float, cost: float):
        try:
            existing = self._read_status()
            trades = existing.get("trades", [])
            trades.insert(0, {
                "time":   datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                "symbol": symbol,
                "side":   side,
                "qty":    round(qty, 4),
                "price":  round(price, 2),
                "cost":   round(cost, 2),
            })
            existing["trades"] = trades[:50]
            DATA_FILE.write_text(json.dumps(existing, indent=2))
        except Exception as e:
            logger.warning(f"Failed to record trade: {e}")

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self):
        try:
            if not config.DRY_RUN and not self.client.is_market_open():
                logger.info("US market is closed. Skipping tick.")
                return

            cash = self.client.get_cash()

            # Sync all live positions first, so starting balance is accurate
            prices: dict[str, float] = {}
            for pos in self.positions.values():
                self._sync_position(pos)

            # Estimate total equity for daily starting balance
            total_equity = cash
            for pos in self.positions.values():
                px = self.client.fetch_ticker(pos.symbol)["last"]
                prices[pos.symbol] = px
                total_equity += pos.qty * px
            self.risk.set_starting_balance(total_equity)

            signals: dict = {}
            for sym, pos in self.positions.items():
                price = prices[sym]

                # Emergency stop-loss check per symbol (always, even if locked)
                if pos.qty > 0 and self.risk.should_emergency_sell(price, pos.avg_entry, pos.qty):
                    self._execute_sell(pos, price, reason="stop-loss")
                    continue

                if not self.risk.can_trade():
                    logger.info(f"Trading locked for the day. {self.risk.summary()}")
                    break

                df = self._fetch_df(sym)
                if df is None:
                    logger.warning(f"{sym}: no data, skipping.")
                    continue
                signal = generate_signal(df)
                signals[sym] = signal
                logger.info(f"{sym}: ${price:.2f} | {signal.signal.value} "
                            f"(conf={signal.confidence:.2f}) | {signal.reason}")

                if signal.signal == Signal.BUY and pos.qty == 0:
                    self._execute_buy(pos, price, cash)
                    cash = self.client.get_cash()  # refresh after spend
                elif signal.signal == Signal.SELL and pos.qty > 0:
                    self._execute_sell(pos, price, reason="signal")

            self._write_status(cash, prices, signals)

        except Exception as e:
            logger.error(f"Error in tick: {e}", exc_info=True)

    def run(self, interval_seconds: int = 60):
        logger.info(
            f"Starting multi-stock bot | Watchlist: {', '.join(config.WATCHLIST)} | "
            f"{'PAPER' if config.ALPACA_PAPER else 'LIVE'} | "
            f"Target +{config.DAILY_PROFIT_TARGET*100:.0f}% / Stop -{config.DAILY_STOP_LOSS*100:.1f}%"
        )
        while True:
            self.tick()
            time.sleep(interval_seconds)


if __name__ == "__main__":
    TradingBot().run(interval_seconds=60)
