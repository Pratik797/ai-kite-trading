# Roadmap — remaining phases

This pass built the deterministic foundation (spec sections 1/3/4/5-partial/
6/7/8, spec roadmap Phases 1/3/6/7 and part of 8). Everything below is
**not started** and should not be built until the backtesting results in
`README.md` justify spending money on live infrastructure — see the honest
note there about ₹1,000 active capital vs. Kite Connect's ₹500/month fee.

Do not start any phase below without first re-reading spec section 10
(Kite & Regulatory Requirements) and confirming current Zerodha/SEBI
requirements — they change over time and the spec explicitly calls this out.

## Phase 2 & 9 — Kite Connect broker adapter (market data + execution)

**What it needs:**
- A Zerodha trading account and a Kite Connect developer subscription
  (currently ₹500/month, per spec section 10 — verify current pricing
  before subscribing).
- `KITE_API_KEY`, `KITE_API_SECRET` from https://developers.kite.trade/,
  and a daily OAuth login flow producing a short-lived `KITE_ACCESS_TOKEN`
  (never store the broker password — spec section 11).
- SEBI's Feb-2025 retail algorithmic-trading framework compliance
  (registration/static IP/order-rate controls per current Zerodha rules —
  check immediately before going live, spec section 10).

**What it builds:**
- A market data adapter (REST for historical/quotes, WebSocket for ticks)
  replacing `app/backtest/data.py`'s yfinance substitute for live/paper use.
- An order adapter with idempotent order placement, timeout handling
  without blind retries, and startup + periodic broker-state reconciliation
  (spec section 11).
- A kill switch wired to broker mismatches, stale data, and repeated order
  failures.

## Phase 5 — Claude / Anthropic AI Analysis adapter

**What it needs:**
- A separate **Anthropic API key** from https://console.anthropic.com —
  this is distinct from a Claude Pro/Claude Code subscription and is
  usage-billed (spec section 12). Minimize calls by doing all indicator
  math locally (already true of this pass) and sending Claude only compact
  structured context, never raw tick streams (spec section 5).

**What it builds:**
- A schema-validated JSON contract: signal, confidence, reasons,
  contradictory evidence, risk flags, invalidation conditions.
- Guardrails: Claude gets no broker credentials, cannot change risk limits,
  cannot touch protected capital, cannot self-modify the production
  strategy (spec section 5). The existing `RiskEngine` remains the sole
  gate that can approve a trade — an AI signal is validated exactly like
  the deterministic baseline's signal, no special path.
- Backtesting comparison: technical-only baseline (already built) vs.
  AI/news-enhanced strategy (spec section 8) — this phase should extend
  `app/backtest/engine.py` to run both and compare, not replace the
  baseline comparison.

## Phase 4 — News ingestion & event risk

**What it needs:** free/legal news sources first (spec section 12 targets
₹0 initially); add a paid feed only if a backtest justifies it.

**What it builds:** company/sector/macro/global event ingestion with
deduplication, timestamps, source, sentiment, and materiality — feeding
into the AI Analysis module's context, not the deterministic baseline.

## Phase 8 (remainder) — Paper trading, dashboard, alerts

**What it needs:** nothing beyond what's already built for the paper-mode
signal/risk path; the dashboard needs Node/npm for the React+TypeScript
frontend and a running FastAPI backend.

**What it builds:**
- A live-market paper-trading loop reusing the exact same
  `RiskEngine`/`ProfitLocker`/strategy path this pass already exercises in
  the backtest engine — spec section 9 requires this parity before any
  live approval.
- FastAPI backend exposing dashboard/scanner/signals/portfolio/risk-manager/
  profit-locker/settings endpoints (spec section 2/13).
- React + TypeScript frontend implementing the UI navigation in spec
  section 13.

## Phase 9 (remainder) — Live execution gate

**What it needs:** everything above, plus explicit human sign-off. Spec
section 9: start live with manual approval, then semi-auto, then fully
automated only after evidence from paper trading.

**What it builds:** the live order path itself, gated behind the same risk
engine, with the kill switch and reconciliation from Phase 2/9 wired in.

## Phase 10 — Security, monitoring, compliance, controlled rollout

**What it needs:** current Kite/SEBI requirements re-verified immediately
before this phase (they were already evolving as of the spec's writing —
spec section 10).

**What it builds:** structured audit logs, alerting, database backups,
rate-limit handling, stale-data detection, and the full production
checklist in spec section 14 — most of which (tests, reproducible
backtests, no-guaranteed-return language) this pass already satisfies for
the deterministic core.

## Summary of credentials/costs by phase

| Phase | Credential/setup needed | Cost |
|---|---|---|
| 2/9 Kite adapter | Kite Connect API key/secret + SEBI algo compliance | ~₹500/month |
| 5 Claude adapter | Anthropic API key (separate from Claude Pro) | usage-based |
| 4 News | none initially (free sources) | ₹0 target |
| 8 Dashboard | Node/npm for frontend build | ₹0 |
| 9 Live execution | manual approval workflow, no new credential | ₹0 |
| 10 Rollout | verified current SEBI/Zerodha compliance | varies |

None of these are needed to run or extend the backtesting work in this
pass.
