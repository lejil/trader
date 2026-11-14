---
name: new-strategy
description: Scaffold a new trading strategy with a consistent structure, config, and a backtest-ready test. Use when the user asks to create, add, or start a new strategy.
---

# New strategy

Create a new strategy that matches the project's existing conventions. Read `CLAUDE.md` and any existing strategies first, and copy their layout and language.

## Steps

1. **Clarify** the strategy's name, instruments, timeframe, and the entry/exit idea in one sentence. Ask only if the request leaves these out.
2. **Create the strategy module.** It contains pure signal logic: it takes market data in and returns signals or target positions out. It has no broker or network calls, which keeps it backtestable.
3. **Externalize parameters** (lookbacks, thresholds, sizing) into a config object or file with sensible defaults. Don't use magic numbers in the logic.
4. **Add a test** that runs the strategy on a small fixed dataset and asserts on the signals it produces. Include a check that signals at bar *t* use only data up to *t*, so there is no look-ahead.
5. **Wire it up** in the strategy registry or CLI entry point, if the project has one.
6. **Run the tests** and report the results.

Finish by suggesting the user run the `risk-check` skill before connecting the strategy to any account.
