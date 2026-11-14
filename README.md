# btc-cent-forecast-01

Forecast Bitcoin's price 4 hours ahead, 12 times over 48 hours
(make.fun competition, entry by @kiowayyy).

## Program

`main.py` — Python 3.13, stdlib only, deterministic, no network.

- Reads every `*.in` file in `$MAKEFUN_INPUT_DIR` (default `/input/`).
- Writes one `*.out` file per case in `$MAKEFUN_OUTPUT_DIR` (default `/output/`).
- Each input case is a `makefun/btc-cent-case/v1` object with 4320 1-minute
  closes (72 hours) for `btc-usdc`; the last close is the checkpoint price.
- Each output is exactly `{"forecasts":[{"case":"<id>","price":<number>}]}`.

## Strategy

Work in log-price:

1. **Weighted drift** — log-returns weighted exponentially (half-life 6h, so
   recent hours matter most), scaled by the 240-minute horizon, then shrunk
   hard toward 0 (a 4-hour move is dominated by noise). The shrinkage is
   volatility-aware: the drift is down-weighted further when recent
   realised volatility is high, since the estimate is then less reliable.
2. **Weak mean reversion** — a small pull toward the 24h EMA of log-price.
3. **Clamp** — the total predicted log-move is capped at ~2% over 4 hours,
   so high-volatility stretches cannot produce extreme forecasts.

Degenerate series (too short, constant, non-positive) fall back to the last
close (persistence, the house baseline).

## Run

```sh
MAKEFUN_INPUT_DIR=/tmp/btc-test/input MAKEFUN_OUTPUT_DIR=/tmp/btc-test/out \
  python3 main.py
```

## License

MIT — see `LICENSE`.
