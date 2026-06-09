"""
Simple backtester to validate the strategy against historical data.
Usage:
    python backtest.py [--days 30]
"""

import argparse
import logging
from datetime import datetime, timedelta
import pandas as pd

from exchange import ExchangeClient
from indicators import compute_indicators, ohlcv_to_df
from strategy import Signal, generate_signal
import config

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def run_backtest(days: int = 30):
    print(f"\nBacktest: XRP/EUR | Last {days} days | Timeframe: {config.TIMEFRAME}")
    print("-" * 60)

    client = ExchangeClient()
    # Fetch maximum available candles
    raw = client.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit=1000)
    df_full = ohlcv_to_df(raw)
    df_full = compute_indicators(df_full)

    cutoff = datetime.utcnow() - timedelta(days=days)
    df_full = df_full[df_full.index >= pd.Timestamp(cutoff)]

    if df_full.empty:
        print("Not enough historical data.")
        return

    # Simulate
    position_xrp = 0.0
    avg_entry = 0.0
    position_cost = 0.0
    trades = []
    daily_pnl: dict[str, float] = {}

    starting_eur = config.TRADE_AMOUNT_EUR
    available_eur = starting_eur

    for i in range(50, len(df_full)):
        window = df_full.iloc[:i+1]
        row = window.iloc[-1]
        current_price = row["close"]
        today = str(row.name.date())

        if today not in daily_pnl:
            daily_pnl[today] = 0.0

        signal = generate_signal(window)

        if signal.signal == Signal.BUY and position_xrp == 0 and available_eur >= 5:
            xrp_bought = available_eur / current_price
            position_xrp = xrp_bought
            avg_entry = current_price
            position_cost = available_eur
            available_eur = 0.0

        elif signal.signal == Signal.SELL and position_xrp > 0:
            proceeds = position_xrp * current_price
            pnl = proceeds - position_cost
            daily_pnl[today] = daily_pnl.get(today, 0.0) + pnl
            trades.append({
                "time": row.name,
                "entry": avg_entry,
                "exit": current_price,
                "xrp": position_xrp,
                "pnl_eur": pnl,
                "pnl_pct": pnl / position_cost * 100,
            })
            available_eur = proceeds
            position_xrp = 0.0
            avg_entry = 0.0
            position_cost = 0.0

    # Close any open position at last price
    if position_xrp > 0:
        last_price = df_full.iloc[-1]["close"]
        proceeds = position_xrp * last_price
        pnl = proceeds - position_cost
        trades.append({
            "time": df_full.index[-1],
            "entry": avg_entry,
            "exit": last_price,
            "xrp": position_xrp,
            "pnl_eur": pnl,
            "pnl_pct": pnl / position_cost * 100,
        })
        available_eur = proceeds

    total_return = (available_eur - starting_eur) / starting_eur * 100
    wins = [t for t in trades if t["pnl_eur"] > 0]
    losses = [t for t in trades if t["pnl_eur"] <= 0]

    print(f"Total trades: {len(trades)}")
    print(f"Wins: {len(wins)} | Losses: {len(losses)}")
    if trades:
        win_rate = len(wins) / len(trades) * 100
        avg_win = sum(t["pnl_eur"] for t in wins) / max(len(wins), 1)
        avg_loss = sum(t["pnl_eur"] for t in losses) / max(len(losses), 1)
        print(f"Win rate: {win_rate:.1f}%")
        print(f"Avg win: +{avg_win:.2f} EUR | Avg loss: {avg_loss:.2f} EUR")
    print(f"Starting capital: {starting_eur:.2f} EUR")
    print(f"Final capital:    {available_eur:.2f} EUR")
    print(f"Total return:     {total_return:+.2f}%")

    print("\nDaily P&L:")
    for day, pnl in sorted(daily_pnl.items()):
        bar = "+" * int(max(pnl, 0)) + "-" * int(max(-pnl, 0))
        print(f"  {day}: {pnl:+.2f} EUR  {bar[:40]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    run_backtest(args.days)
