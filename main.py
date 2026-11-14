#!/usr/bin/env python3
"""btc-cent-forecast-01: forecast BTC price 4 hours ahead.

Reads every *.in file in $MAKEFUN_INPUT_DIR (default /input/) and writes
one *.out file per case in $MAKEFUN_OUTPUT_DIR (default /output/).

Input case (makefun/btc-cent-case/v1):
  {"schema": ..., "competition": "btc-cent-forecast-01", "case": "<id>",
   "at": "<checkpoint ISO 8601 UTC ms>", "horizon_minutes": 240,
   "markets": [{"id": "btc-usdc", "name": "Bitcoin",
                "start": "<ISO first minute>",
                "prices": [4320 1-minute closes, last = close at checkpoint]}]}

Output (one JSON object, exactly one key):
  {"forecasts": [{"case": "<id>", "price": <forecast>}]}
  price must be finite, > 0 and < 10,000,000.

Strategy (log-price, deterministic, stdlib only, no network):
  * Work in log-price so moves are scale-free.
  * Estimate the short-horizon drift from log-returns with exponential
    weighting (recent hours weigh more) and shrink it hard toward zero:
    a 4-hour move is dominated by noise, so the raw drift is scaled down
    and clamped. The shrinkage is volatility-aware: when recent realised
    volatility is high, the drift estimate is less trustworthy, so it is
    shrunk further (factor 1 / (1 + horizon_noise / 0.01)).
  * Add a weak mean-reversion pull toward the 24h exponential moving
    average of log-price.
  * Combine the two components, clamp the total predicted log-move, and
    exponentiate back. Degenerate series (short, constant, non-positive)
    fall back to the last close (persistence).
"""

import json
import math
import os
import sys

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

HORIZON_MINUTES = 240
MAX_PRICE = 10_000_000.0

# Drift estimation: exponential half-life (minutes) for return weighting.
DRIFT_HALF_LIFE_MIN = 360.0
# Shrink the annualised-style drift toward 0: 4h ahead is mostly noise.
DRIFT_SHRINK = 0.25
# Volatility-aware shrinkage scale: horizon noise (vol * sqrt(horizon))
# is compared against this reference (1% log-move) to down-weight the
# drift further in noisy regimes.
VOL_REFERENCE = 0.01
# Mean reversion toward the 24h EMA of log-price.
EMA_HALF_LIFE_MIN = 1440.0
REVERSION_STRENGTH = 0.10
# Cap the reversion pull so it can never dominate the drift in a strong
# sustained trend (the EMA lags far behind then); in normal regimes the
# EMA gap is small and this cap does not bind.
REVERSION_MAX_MOVE = 0.005  # 0.5% log-move
# Hard safety bounds on the predicted log-move over the horizon.
MAX_LOG_MOVE = 0.02  # ~2% move over 4h


def _clean_prices(raw):
    """Return a list of finite positive floats, or [] if unusable."""
    out = []
    for p in raw or []:
        try:
            v = float(p)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            out.append(v)
    return out


def _ema(values, half_life_min):
    """Exponential moving average over 1-minute samples, last value wins."""
    if not values:
        return None
    alpha = 1.0 - math.exp(math.log(0.5) / half_life_min)
    acc = values[0]
    for v in values[1:]:
        acc += alpha * (v - acc)
    return acc


def forecast(prices, horizon_minutes=HORIZON_MINUTES):
    """Predict the price `horizon_minutes` ahead from 1-minute closes."""
    prices = _clean_prices(prices)
    if not prices:
        return None
    last = prices[-1]
    if len(prices) < 2:
        return last

    logs = [math.log(p) for p in prices]
    n = len(logs)

    # --- weighted drift of log-returns (recent minutes weigh more) ---
    decay = math.exp(math.log(0.5) / DRIFT_HALF_LIFE_MIN)
    weighted_sum = 0.0
    weight_total = 0.0
    # returns[i] = logs[i+1] - logs[i]; weight decays with age.
    w = 1.0
    for i in range(n - 2, -1, -1):
        r = logs[i + 1] - logs[i]
        weighted_sum += w * r
        weight_total += w
        w *= decay
    mean_return = weighted_sum / weight_total if weight_total > 0 else 0.0

    # --- realised volatility of the same weighted returns ---
    # Used to shrink the drift further when the estimate is noisy.
    weighted_var = 0.0
    w = 1.0
    for i in range(n - 2, -1, -1):
        r = logs[i + 1] - logs[i]
        weighted_var += w * (r - mean_return) ** 2
        w *= decay
    variance = weighted_var / weight_total if weight_total > 0 else 0.0
    horizon_noise = math.sqrt(max(variance, 0.0)) * math.sqrt(horizon_minutes)
    vol_factor = 1.0 / (1.0 + horizon_noise / VOL_REFERENCE)

    drift_move = mean_return * horizon_minutes * DRIFT_SHRINK * vol_factor

    # --- weak mean reversion toward the 24h EMA of log-price ---
    ema_log = _ema(logs, EMA_HALF_LIFE_MIN)
    reversion_move = 0.0
    if ema_log is not None:
        reversion_move = REVERSION_STRENGTH * (ema_log - logs[-1])
        reversion_move = max(
            -REVERSION_MAX_MOVE, min(REVERSION_MAX_MOVE, reversion_move)
        )

    move = drift_move + reversion_move
    move = max(-MAX_LOG_MOVE, min(MAX_LOG_MOVE, move))
    price = math.exp(logs[-1] + move)
    if not (math.isfinite(price) and 0.0 < price < MAX_PRICE):
        return last
    return price


def process_case(case):
    """Build the single forecast row for one input case object."""
    case_id = case.get("case")
    markets = case.get("markets") or []
    prices = []
    for m in markets:
        if isinstance(m, dict) and m.get("id") == "btc-usdc":
            prices = m.get("prices") or []
            break
    if not prices and markets:
        first = markets[0]
        if isinstance(first, dict):
            prices = first.get("prices") or []
    price = forecast(prices)
    if price is None:
        raise ValueError("no usable prices in case %r" % (case_id,))
    return {"case": case_id, "price": round(price, 2)}


def main():
    in_dir = INPUT_DIR
    out_dir = OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(
        name for name in os.listdir(in_dir) if name.endswith(".in")
    )
    if not names:
        print("no .in files in %s" % in_dir, file=sys.stderr)
        return 1
    for name in names:
        in_path = os.path.join(in_dir, name)
        with open(in_path, "r", encoding="utf-8") as fh:
            case = json.load(fh)
        row = process_case(case)
        stem = name[:-3]
        out_path = os.path.join(out_dir, stem + ".out")
        payload = {"forecasts": [row]}
        # Deterministic serialisation: sorted keys, fixed separators.
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
