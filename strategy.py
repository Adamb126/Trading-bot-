import logging
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import config

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    signal: Signal
    reason: str
    confidence: float  # 0.0 – 1.0
    suggested_price: Optional[float] = None


def _score_buy(row: pd.Series) -> tuple[int, list[str]]:
    """Return (score, reasons) for a BUY signal. Max score = 5."""
    score = 0
    reasons = []

    if row["rsi"] < config.RSI_OVERSOLD:
        score += 1
        reasons.append(f"RSI oversold ({row['rsi']:.1f})")

    if row["macd"] > row["macd_signal"] and row["macd_hist"] > 0:
        score += 1
        reasons.append("MACD bullish crossover")

    if row["bb_pct"] < 0.2:
        score += 1
        reasons.append(f"Price near BB lower band ({row['bb_pct']:.2f})")

    if row["ema_trend"] == 1:
        score += 1
        reasons.append("EMA fast > EMA slow (uptrend)")

    if row["vol_ratio"] > 1.3:
        score += 1
        reasons.append(f"High volume confirmation ({row['vol_ratio']:.2f}x)")

    return score, reasons


def _score_sell(row: pd.Series) -> tuple[int, list[str]]:
    """Return (score, reasons) for a SELL signal. Max score = 5."""
    score = 0
    reasons = []

    if row["rsi"] > config.RSI_OVERBOUGHT:
        score += 1
        reasons.append(f"RSI overbought ({row['rsi']:.1f})")

    if row["macd"] < row["macd_signal"] and row["macd_hist"] < 0:
        score += 1
        reasons.append("MACD bearish crossover")

    if row["bb_pct"] > 0.8:
        score += 1
        reasons.append(f"Price near BB upper band ({row['bb_pct']:.2f})")

    if row["ema_trend"] == 0:
        score += 1
        reasons.append("EMA fast < EMA slow (downtrend)")

    if row["vol_ratio"] > 1.3:
        score += 1
        reasons.append(f"High volume confirmation ({row['vol_ratio']:.2f}x)")

    return score, reasons


def generate_signal(df: pd.DataFrame) -> TradeSignal:
    """Analyse the latest candle and return a trade signal."""
    required_cols = ["rsi", "macd", "macd_signal", "macd_hist", "bb_pct", "ema_trend", "vol_ratio"]
    if df.shape[0] < 50 or not all(c in df.columns for c in required_cols):
        return TradeSignal(Signal.HOLD, "Insufficient data", 0.0)

    row = df.iloc[-1]

    # Previous candle for crossover detection
    prev = df.iloc[-2]
    macd_just_crossed_up = prev["macd"] <= prev["macd_signal"] and row["macd"] > row["macd_signal"]
    macd_just_crossed_down = prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]

    buy_score, buy_reasons = _score_buy(row)
    sell_score, sell_reasons = _score_sell(row)

    # Require at least 3/5 factors AND MACD crossover confirmation to enter
    if buy_score >= 3 and macd_just_crossed_up:
        confidence = buy_score / 5.0
        reason = " | ".join(buy_reasons)
        price = row["close"] * (1 + config.LIMIT_ORDER_SLIPPAGE)
        logger.info(f"BUY signal (conf={confidence:.2f}): {reason}")
        return TradeSignal(Signal.BUY, reason, confidence, price)

    if sell_score >= 3 and macd_just_crossed_down:
        confidence = sell_score / 5.0
        reason = " | ".join(sell_reasons)
        price = row["close"] * (1 - config.LIMIT_ORDER_SLIPPAGE)
        logger.info(f"SELL signal (conf={confidence:.2f}): {reason}")
        return TradeSignal(Signal.SELL, reason, confidence, price)

    return TradeSignal(Signal.HOLD, "No clear signal", 0.0)
