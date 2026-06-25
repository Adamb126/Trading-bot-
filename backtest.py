"""
Backtester for XRP/EUR strategy.

Usage:
    python backtest.py                  # backtest current settings, last 90 days
    python backtest.py --days 60        # shorter window
    python backtest.py --sweep          # find best RSI / threshold combo
    python backtest.py --compare        # old loose vs new strict settings side-by-side
"""

import argparse
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from exchange import ExchangeClient
from indicators import compute_indicators, ohlcv_to_df
from strategy import Signal, TradeSignal, generate_signal
import config

logging.basicConfig(level=logging.WARNING)

KRAKEN_TAKER_FEE = 0.0026  # 0.26% per market order on Kraken


# ── Core simulation ────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    label: str
    days: int
    trades: list
    starting_eur: float
    final_eur: float

    @property
    def total_return_pct(self):
        return (self.final_eur - self.starting_eur) / self.starting_eur * 100

    @property
    def wins(self):
        return [t for t in self.trades if t["pnl_eur"] > 0]

    @property
    def losses(self):
        return [t for t in self.trades if t["pnl_eur"] <= 0]

    @property
    def win_rate(self):
        return len(self.wins) / len(self.trades) * 100 if self.trades else 0.0

    @property
    def avg_win_eur(self):
        return sum(t["pnl_eur"] for t in self.wins) / max(len(self.wins), 1)

    @property
    def avg_loss_eur(self):
        return sum(t["pnl_eur"] for t in self.losses) / max(len(self.losses), 1)

    @property
    def max_drawdown_pct(self):
        """Largest peak-to-trough drop in running capital."""
        if not self.trades:
            return 0.0
        peak = self.starting_eur
        max_dd = 0.0
        capital = self.starting_eur
        for t in self.trades:
            capital += t["pnl_eur"]
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def profit_factor(self):
        gross_win = sum(t["pnl_eur"] for t in self.wins)
        gross_loss = abs(sum(t["pnl_eur"] for t in self.losses))
        return gross_win / gross_loss if gross_loss > 0 else float("inf")


def _simulate(df_full: pd.DataFrame, rsi_oversold: float, rsi_overbought: float,
              min_score: int, require_crossover: bool, starting_eur: float) -> list:
    """Run one simulation pass and return list of closed trades."""
    import config as cfg

    # Temporarily patch config values for this run
    orig_os = cfg.RSI_OVERSOLD
    orig_ob = cfg.RSI_OVERBOUGHT
    cfg.RSI_OVERSOLD = rsi_oversold
    cfg.RSI_OVERBOUGHT = rsi_overbought

    position_xrp = 0.0
    avg_entry = 0.0
    position_cost = 0.0
    available_eur = starting_eur
    trades = []

    for i in range(50, len(df_full)):
        window = df_full.iloc[: i + 1]
        row = window.iloc[-1]
        current_price = row["close"]

        signal = _generate_signal_custom(window, min_score, require_crossover)

        if signal.signal == Signal.BUY and position_xrp == 0 and available_eur >= 5:
            fee = available_eur * KRAKEN_TAKER_FEE
            xrp_bought = (available_eur - fee) / current_price
            position_xrp = xrp_bought
            avg_entry = current_price
            position_cost = available_eur
            available_eur = 0.0

        elif signal.signal == Signal.SELL and position_xrp > 0:
            gross = position_xrp * current_price
            fee = gross * KRAKEN_TAKER_FEE
            proceeds = gross - fee
            pnl = proceeds - position_cost
            trades.append({
                "time":    row.name,
                "entry":   avg_entry,
                "exit":    current_price,
                "xrp":     round(position_xrp, 4),
                "pnl_eur": round(pnl, 4),
                "pnl_pct": round(pnl / position_cost * 100, 2),
                "reason":  signal.reason,
            })
            available_eur = proceeds
            position_xrp = 0.0
            avg_entry = 0.0
            position_cost = 0.0

    # Close open position at last price
    if position_xrp > 0:
        last_price = df_full.iloc[-1]["close"]
        gross = position_xrp * last_price
        proceeds = gross - gross * KRAKEN_TAKER_FEE
        pnl = proceeds - position_cost
        trades.append({
            "time":    df_full.index[-1],
            "entry":   avg_entry,
            "exit":    last_price,
            "xrp":     round(position_xrp, 4),
            "pnl_eur": round(pnl, 4),
            "pnl_pct": round(pnl / position_cost * 100, 2),
            "reason":  "position still open at end",
        })
        available_eur = proceeds

    # Restore config
    cfg.RSI_OVERSOLD = orig_os
    cfg.RSI_OVERBOUGHT = orig_ob

    return trades, available_eur


def _generate_signal_custom(df, min_score: int, require_crossover: bool) -> TradeSignal:
    """Like generate_signal() but with configurable thresholds."""
    from strategy import _score_buy, _score_sell

    required_cols = ["rsi", "macd", "macd_signal", "macd_hist", "bb_pct", "ema_trend", "vol_ratio"]
    if df.shape[0] < 50 or not all(c in df.columns for c in required_cols):
        return TradeSignal(Signal.HOLD, "Insufficient data", 0.0)

    row = df.iloc[-1]
    prev = df.iloc[-2]
    macd_crossed_up = prev["macd"] <= prev["macd_signal"] and row["macd"] > row["macd_signal"]
    macd_crossed_down = prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]

    buy_score, buy_reasons = _score_buy(row)
    sell_score, sell_reasons = _score_sell(row)

    buy_ok = buy_score >= min_score and (macd_crossed_up if require_crossover else True)
    sell_ok = sell_score >= min_score and (macd_crossed_down if require_crossover else True)

    if buy_ok:
        return TradeSignal(Signal.BUY, " | ".join(buy_reasons), buy_score / 5.0)
    if sell_ok:
        return TradeSignal(Signal.SELL, " | ".join(sell_reasons), sell_score / 5.0)
    return TradeSignal(Signal.HOLD, "No clear signal", 0.0)


# ── Display helpers ────────────────────────────────────────────────────────────

def _print_result(r: BacktestResult, show_trades: bool = True):
    win_str = f"{len(r.wins)}W / {len(r.losses)}L"
    print(f"\n{'='*62}")
    print(f"  {r.label}")
    print(f"{'='*62}")
    print(f"  Period:        Last {r.days} days  ({config.TIMEFRAME} candles)")
    print(f"  Starting:      €{r.starting_eur:.2f}")
    print(f"  Final:         €{r.final_eur:.2f}")
    print(f"  Total return:  {r.total_return_pct:+.2f}%")
    print(f"  Trades:        {len(r.trades)}  ({win_str})")
    if r.trades:
        print(f"  Win rate:      {r.win_rate:.1f}%")
        print(f"  Avg win:       +€{r.avg_win_eur:.3f}")
        print(f"  Avg loss:       €{r.avg_loss_eur:.3f}")
        print(f"  Profit factor: {r.profit_factor:.2f}x")
        print(f"  Max drawdown:  -{r.max_drawdown_pct:.2f}%")

    if show_trades and r.trades:
        print(f"\n  {'Time':<20} {'Entry':>8} {'Exit':>8} {'P&L EUR':>9} {'P&L %':>7}")
        print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*9} {'-'*7}")
        for t in r.trades:
            sign = "+" if t["pnl_eur"] >= 0 else ""
            print(
                f"  {str(t['time'])[:19]:<20} "
                f"{t['entry']:>8.4f} "
                f"{t['exit']:>8.4f} "
                f"{sign}{t['pnl_eur']:>8.3f}€ "
                f"{sign}{t['pnl_pct']:>6.2f}%"
            )


# ── Modes ─────────────────────────────────────────────────────────────────────

def run_backtest(days: int, label: str = "Current settings", show_trades: bool = True) -> BacktestResult:
    client = ExchangeClient()
    # Fetch enough candles: days * (candles per day on 15m timeframe) + 50 warmup
    limit = min(days * 96 + 100, 720)
    raw = client.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit=limit)
    df_full = ohlcv_to_df(raw)
    df_full = compute_indicators(df_full)

    cutoff = datetime.utcnow() - timedelta(days=days)
    df_full = df_full[df_full.index >= pd.Timestamp(cutoff)]

    if df_full.empty:
        print("Not enough historical data for the requested period.")
        return

    trades, final_eur = _simulate(
        df_full,
        rsi_oversold=config.RSI_OVERSOLD,
        rsi_overbought=config.RSI_OVERBOUGHT,
        min_score=3,
        require_crossover=True,
        starting_eur=config.TRADE_AMOUNT_EUR,
    )

    result = BacktestResult(label, days, trades, config.TRADE_AMOUNT_EUR, final_eur)
    _print_result(result, show_trades=show_trades)
    return result


def run_compare(days: int):
    """Side-by-side: old loose settings vs current strict settings."""
    client = ExchangeClient()
    limit = min(days * 96 + 100, 720)
    raw = client.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit=limit)
    df_full = ohlcv_to_df(raw)
    df_full = compute_indicators(df_full)

    cutoff = datetime.utcnow() - timedelta(days=days)
    df_full = df_full[df_full.index >= pd.Timestamp(cutoff)]

    starting = config.TRADE_AMOUNT_EUR

    configs = [
        dict(label="OLD (RSI 45/55, no crossover)", rsi_oversold=45, rsi_overbought=55,
             min_score=3, require_crossover=False),
        dict(label="NEW (RSI 38/62, crossover required)", rsi_oversold=38, rsi_overbought=62,
             min_score=3, require_crossover=True),
    ]

    print(f"\nComparing strategies — last {days} days on {config.TIMEFRAME} candles\n")
    for c in configs:
        trades, final_eur = _simulate(df_full, c["rsi_oversold"], c["rsi_overbought"],
                                      c["min_score"], c["require_crossover"], starting)
        r = BacktestResult(c["label"], days, trades, starting, final_eur)
        _print_result(r, show_trades=False)


def run_sweep(days: int):
    """Grid search over RSI thresholds to find best settings."""
    client = ExchangeClient()
    limit = min(days * 96 + 100, 720)
    raw = client.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit=limit)
    df_full = ohlcv_to_df(raw)
    df_full = compute_indicators(df_full)

    cutoff = datetime.utcnow() - timedelta(days=days)
    df_full = df_full[df_full.index >= pd.Timestamp(cutoff)]

    starting = config.TRADE_AMOUNT_EUR
    results = []

    oversold_range  = [30, 33, 35, 38, 40, 42, 45]
    overbought_range = [55, 58, 60, 62, 65, 67, 70]

    print(f"\nParameter sweep — last {days} days | {len(oversold_range)*len(overbought_range)} combos\n")
    print(f"{'RSI_OS':>6} {'RSI_OB':>6} {'Trades':>7} {'Win%':>6} {'Return%':>9} {'MaxDD%':>8} {'PF':>6}")
    print("-" * 56)

    for os_val in oversold_range:
        for ob_val in overbought_range:
            if ob_val <= os_val:
                continue
            trades, final_eur = _simulate(df_full, os_val, ob_val, 3, True, starting)
            r = BacktestResult("", days, trades, starting, final_eur)
            results.append((r, os_val, ob_val))
            pf = f"{r.profit_factor:.2f}" if r.trades else "  -  "
            print(
                f"{os_val:>6} {ob_val:>6} {len(trades):>7} "
                f"{r.win_rate:>6.1f} {r.total_return_pct:>+9.2f} "
                f"{r.max_drawdown_pct:>8.2f} {pf:>6}"
            )

    # Best by return
    best = max(results, key=lambda x: x[0].total_return_pct)
    print(f"\nBest combo by return: RSI_OVERSOLD={best[1]}, RSI_OVERBOUGHT={best[2]}")
    print(f"  Return: {best[0].total_return_pct:+.2f}% | Win rate: {best[0].win_rate:.1f}% | "
          f"Max drawdown: -{best[0].max_drawdown_pct:.2f}%")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XRP/EUR strategy backtester")
    parser.add_argument("--days",    type=int, default=90, help="Lookback period in days (default 90)")
    parser.add_argument("--sweep",   action="store_true",  help="Grid search over RSI parameters")
    parser.add_argument("--compare", action="store_true",  help="Old loose settings vs new strict settings")
    args = parser.parse_args()

    if args.sweep:
        run_sweep(args.days)
    elif args.compare:
        run_compare(args.days)
    else:
        run_backtest(args.days)
