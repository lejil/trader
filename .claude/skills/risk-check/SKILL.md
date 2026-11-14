---
name: risk-check
description: Check trading code for risk-management problems before it runs against a live or paper account. Use when writing or changing order placement, position sizing, or strategy execution code, or when the user asks whether something is safe to run.
---

# Risk check

Review the order-placement or strategy code you were given (or just changed) against this checklist. Report each failing item with a `file:line` reference and a suggested fix.

## Checklist

1. **Environment**: Orders go to a paper/sandbox endpoint unless live trading is set explicitly through config or an env var, never hardcoded.
2. **Position sizing**: Every order size comes from a sizing function that has a per-trade risk cap (e.g. a percentage of equity).
3. **Exposure limits**: There is a max position size per symbol and a max total exposure.
4. **Stops**: Each entry has a defined exit: a stop-loss, a time stop, or an explicit reason why it has none.
5. **Kill switch**: A daily loss limit or max drawdown halts trading.
6. **Order safety**: Limit orders or price-band checks guard against fat-finger fills, and quantity and price are validated before submission.
7. **Idempotency**: Retries can't send duplicate orders (client order IDs are used).
8. **Error handling**: Rejections, partial fills, rate limits, and disconnects are handled and logged.
9. **Numerics**: Prices, quantities, and P&L use `Decimal` (or integer ticks), never floats.
10. **Secrets**: API keys are read from env vars, never logged or committed.

End with one line: **SAFE TO RUN**, **RUN ON PAPER ONLY**, or **DO NOT RUN**, plus the single biggest reason.
