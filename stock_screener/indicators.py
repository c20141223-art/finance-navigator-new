"""RSI / MACD, computed to Taiwan charting-software conventions (spec §9).

- RSI: Wilder smoothing. AU/AD seeded with the simple average of the first
  n gains/losses, then Wilder-smoothed: avg = (prev*(n-1) + current) / n.
  RSI = 100 * AU / (AU + AD) — the form Taiwan software displays (equal to
  100 - 100/(1+RS)).
- MACD: DIF = EMA(fast) - EMA(slow); MACD(signal line) = EMA(signal) of
  DIF; OSC (柱狀體) = DIF - MACD. EMAs are seeded with the SMA of the
  first n values (classic Appel convention), smoothing k = 2/(n+1).
  Seeding conventions differ slightly across vendors and converge after
  ~2n bars; the radar only reads OSC shape (收斂/翻正) well past warmup,
  where vendor differences are negligible.

All functions take/return pandas Series aligned to the input index, with
NaN during warmup.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    au = pd.Series(np.nan, index=close.index, dtype=float)
    ad = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= period:
        return pd.Series(np.nan, index=close.index, dtype=float)

    au.iloc[period] = gain.iloc[1:period + 1].mean()
    ad.iloc[period] = loss.iloc[1:period + 1].mean()
    for i in range(period + 1, len(close)):
        au.iloc[i] = (au.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        ad.iloc[i] = (ad.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    denom = au + ad
    rsi = 100.0 * au / denom
    rsi[denom == 0] = 50.0  # flat series: neutral, matches common software
    return rsi


def ema(values: pd.Series, period: int) -> pd.Series:
    """SMA-seeded EMA."""
    values = values.astype(float)
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    out.iloc[period - 1] = values.iloc[:period].mean()
    for i in range(period, len(values)):
        out.iloc[i] = values.iloc[i] * k + out.iloc[i - 1] * (1 - k)
    return out


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """Returns DataFrame with columns dif / macd / osc."""
    dif = ema(close, fast) - ema(close, slow)
    # signal line: EMA over DIF's valid region only
    dif_valid = dif.dropna()
    signal_line = pd.Series(np.nan, index=close.index, dtype=float)
    if len(dif_valid) >= signal:
        sig = ema(dif_valid.reset_index(drop=True), signal)
        signal_line.loc[dif_valid.index] = sig.values
    osc = dif - signal_line
    return pd.DataFrame({"dif": dif, "macd": signal_line, "osc": osc})
