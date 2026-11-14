---
description: Review a trading strategy file for logic and risk issues
argument-hint: <path-to-strategy>
---

Review the strategy in $ARGUMENTS. Check for:

- Look-ahead bias (using future data in signals or backtests)
- Missing risk controls (position sizing, stop-loss, max drawdown, exposure limits)
- Float arithmetic on prices or money
- Unhandled API errors, rate limits, or partial fills
- Hardcoded credentials or live-trading endpoints

Report findings ranked by severity with file:line references.
