# Background Maintenance & Safeguards

## Why it matters
The trading engine relies on long-running tasks to keep ledger files healthy, enforce risk limits, and avoid orphaned positions. This guide explains what runs in the background and how to adjust the cadence safely.

## Scheduled task families

| Task group | Description | Source |
| --- | --- | --- |
| Maintenance tasks | Periodically reload positions, reconcile open/closed ledgers, and execute file repair utilities. | [`src/tasks/maintenance_tasks.py`](../src/tasks/maintenance_tasks.py) |
| Safety tasks | Iterate active positions to enforce stop-loss thresholds, maximum trade durations, and leverage sanity checks. | [`src/tasks/safety_tasks.py`](../src/tasks/safety_tasks.py) |
| Shutdown tasks | Optional hook to close all open positions and flush caches during graceful shutdown. | [`src/tasks/shutdown_tasks.py`](../src/tasks/shutdown_tasks.py) |
| Race fix utility | Standalone coroutine invoked at startup to deduplicate ledger entries. | [`src/tasks/fix_position_race.py`](../src/tasks/fix_position_race.py) |

## MaintenanceTasks details
- **Reload cadence** – Controlled by values in [`user_config.yaml`](../user_config.yaml) surfaced through [`UserSettings`](../src/core/user_settings.py).
- **Ledger reconciliation** – Runs `migrate_closed_positions` to ensure trades move from open to closed files when quantity hits zero.
- **Data integrity** – Calls the race condition fixer (`fix_position_race`) to remove duplicates and repair corrupted JSON snapshots.
- **Extensibility** – Register additional async callbacks via `MaintenanceTasks.add_task` to piggyback custom health checks.

## SafetyTasks details
- **Stop-loss enforcement** – Compares current price feeds (via adapter ticker endpoints) with each position's configured stop; breaches trigger `PositionService.close_position` with an `auto_close` flag.
- **Time-based exits** – Optionally closes trades that exceed `LONG_TERM_TRADE_HRS` or similar thresholds defined in configuration.
- **Notification hooks** – Emits Telegram alerts when automated closes occur, giving operators visibility into guardrail actions.

## Configuration touchpoints
- Settings live in [`src/core/config.py`](../src/core/config.py) and [`user_config.yaml`](../user_config.yaml). Adjust intervals and thresholds there; changes propagate via the settings API/UI without restarts.
- API consumers can PATCH `/api/settings` (see [`src/api/settings_api.py`](../src/api/settings_api.py)) to tweak these values remotely.

## Operational tips
- **Logging** – Both task groups log under the `src.tasks` namespace; adjust verbosity through [`configure_logging`](../src/core/logging_config.py) when debugging.
- **Manual runs** – You can call `await MaintenanceTasks.run_once()` or `await SafetyTasks.run_once()` in a REPL to diagnose behaviour without waiting for timers.
- **Resource usage** – Each task schedules its own `asyncio.create_task`; ensure long-running additions yield control frequently to avoid blocking other loops.

## Related references
- `docs/trade-lifecycle.md` for how auto-closes integrate with the trade pipeline
- `src/docs/safety_features.md` (legacy deep dive)