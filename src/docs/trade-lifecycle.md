# Signal Processing & Trade Lifecycle

## Why it matters
AlgoBot executes trades automatically in response to webhook alerts. Understanding the validation, routing, and execution path explains how risk settings, partial take profits, and stop losses behave in production.

## End-to-end flow
1. **Webhook ingestion** – [`/api/webhook`](../src/api/webhook.py) authenticates requests via header/body secrets, validates payloads with Pydantic models, and normalises TradingView fields.
2. **Signal classification** – [`SignalProcessor.handle_signal`](../src/services/signal_processor.py) inspects `command` + strategy metadata to decide whether to open, scale, take-profit, or close a position.
3. **Position lookup** – Existing trades are fetched through [`FilePositionRepository`](../src/repositories/file_position_repository.py); missing entries trigger creation paths.
4. **Order preparation** – [`PositionService`](../src/services/position_service.py) calculates order size using the configured trade amount, leverage, and precision helpers in [`src/core/asset.py`](../src/core/asset.py).
5. **Execution & persistence** – Market orders flow through [`BinanceAdapter`](../src/adapters/binance_adapter.py). Responses are logged via [`get_order_logger`](../src/core/logging_config.py) before positions are persisted to disk (open, closed, and history ledgers).
6. **Notifications** – Telegram updates are dispatched using [`telegram_notifications.py`](../src/telegram/telegram_notifications.py), and webhook responses echo the action outcome.

## Entry logic highlights
- **Direction control** – Allowed long/short directions are enforced via [`UserSettings`](../src/core/user_settings.py), preventing unwanted strategies from firing.
- **Trade sizing** – `PositionService.create_position` converts the `trade_amount` setting into base-asset quantities, respecting min step sizes and optional take-profit scaling tiers.
- **Stop management** – Initial stop loss is stored with the position; background safety tasks will tighten stops if configured.

## Take-profit stages
- Webhook payloads can specify `maxTP` and `takeProfit` indices. `SignalProcessor` tracks progress by updating the `take_profit_index` within each position record.
- Each partial exit writes to both the open-position file (for residual size) and the closed history ledger for analytics.
- Telegram messages summarise the realised percentage per stage and remaining exposure.

## Stop-out scenarios
- **Manual alert** – Signals with `command=STOP` delegate to `PositionService.close_position`, sending a market close order and marking the trade as closed.
- **Automated sweep** – `SafetyTasks._apply_stop_loss` checks price deviations and maximum trade age; when triggered, the same close workflow runs, annotated as `auto_close` in the repository and notifications.

## Extending signal handling
- Add new strategies by registering parser helpers in [`SignalProcessor._process_signal`](../src/services/signal_processor.py).
- For exchange-specific logic (e.g., futures hedging), extend `BinanceAdapter` and branch within `PositionService` while keeping repository contracts unchanged.
- Document non-standard workflows in this file to keep reviewers aligned with how payloads translate to orders.

## Related references
- `src/docs/api_reference.md` for legacy parameter tables
- `src/tasks/safety_tasks.py` for periodic stop-loss enforcement
- `src/telegram/telegram_commands.py` for manual close/adjustment commands