# Fork changes (stein212/nautilus_trader)

This fork carries small patches that [bt](https://github.com/stein212/alpha-research) needs on top
of upstream NautilusTrader. Each change is behind a config option or a new type, off by default,
so upstream behavior and tests are unchanged and upstream releases can be merged in.

Each entry records:

- the upstream base;
- what changed and why;
- the config or new type that switches it on;
- the files touched;
- the tests added;
- any merge notes.

Newest first.

## Base

- Branch `bt/rc7`, from upstream tag `v2.0.0rc7` (55c0e8205).

## Changes

### Fresh margin equity calculation

- Upstream base: `v2.0.0rc7` (`55c0e8205`), branch `bt/rc7`.
- Change and reason: opt-in `equity()` recalculation of open non-premium margin position PnL avoids stale instrument PnL cached before later trade ticks, without adding a trade handler or per-trade work.
- Config: `PortfolioConfig(recalculate_margin_equity=True)` in Python or `.recalculate_margin_equity(true)` on the Rust builder; serde and builder defaults are `false`.
- Files: `crates/portfolio/src/config.rs`, `crates/portfolio/src/portfolio.rs`, `crates/portfolio/src/python/mod.rs`, `crates/portfolio/tests/portfolio.rs`, `python/tests/unit/backtest/test_margin_equity.py`, generated `python/nautilus_trader/portfolio/__init__.pyi`, `docs/concepts/portfolio.md`, and this log.
- Tests: long/short trade-only stale-cache regression with default/disabled/enabled configs; repeated and account-filtered reads; multiple accounts; mark/quote price priority; missing prices, bar fallback and currency conversion; premium valuation with both flag settings; Python BacktestEngine NETTING USDT-margin trade-only long/short regressions with both flag settings.
- Verification: reproduced stale long/short equity before the cache guard; all 315 portfolio tests passed in both standard and high precision after the fix. All 318 Python backtest and portfolio tests passed, including the four trade-only long/short cases: flag-off unfiltered reads retained cached equity, while flag-on and account-filtered reads matched balance plus current PnL. Generated stubs, the editable CPython 3.14 debug build, `make format`, and `make pre-commit` succeeded.
- Merge notes: only guard the existing margin PnL cache lookup; reuse the existing price and conversion paths, account filters, premium valuation, and missing-price reporting. Append the Python constructor argument to preserve existing positional calls. No other defaults or crates change.

## Upstream merges

(none yet: record each upstream tag merged, any conflicts and how they were resolved)
