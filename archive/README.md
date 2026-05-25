# IB API Auto-Executor (archived)

Built as a paper-trading harness for the INDEX-LAB swing strategy.
Paper-tested at IB Gateway port 4002 and identified two structural
divergences from the manual book that prevented promotion to live:

1. IB API does not support fractional shares; manual book sizes
   positions at ~$700 with fractional quantities. Whole-share floor
   at account/8 sizing produced positions ~180× larger and a
   different slippage / market-impact profile.
2. `outside_rth=True` on STP legs of brackets created a tail risk of
   microcap stops triggering on thin pre-market prints.

Kept manual execution to preserve track-record integrity. Code
retained for reference and as a working IB API harness reference
implementation.

Decommissioned 2026-05-25. Signal generation continues via
`run_signals.sh` driven by `deploy/tradingbot-signals.{service,timer}`.
