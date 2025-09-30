# Configuration, Data Stores, and Logging

## Why it matters
Running AlgoBot safely requires predictable configuration management, durable position storage, and auditable logs. This guide outlines how those concerns are handled and where to make changes.

## Configuration stack
- **Static defaults** – [`src/core/config.py`](../core/config.py) defines environment variable names, default trade amounts, stop-loss percentages, and adapter settings consumed across the app.
- **User overrides** – [`user_config.yaml`](../user_config.yaml) stores operator preferences (trade sizing, allowed symbols, chat recipients). The [`UserSettings`](../core/user_settings.py) singleton loads and hot-reloads this file.
- **Runtime edits** – The settings API (`/api/settings` in [`src/api/settings_api.py`](../api/settings_api.py)) exposes GET/PATCH operations with API-key auth so the web UI and Telegram commands can adjust configuration on the fly.
- **Validation** – Pydantic models inside `UserSettings` ensure incoming changes respect expected ranges before persisting back to disk.

## Data storage
- **Primary ledger** – [`src/data/open_positions.json`](../data/) (created at runtime) tracks active trades with quantities, stops, and TP progress.
- **Closed history** – `closed_positions.json` and CSV exports capture realised trades for analytics and auditing.
- **Backups** – The repository writes to timestamped backups before mutating files, minimising corruption risk during crashes (`FilePositionRepository._write_atomic`).
- **File locking** – [`src/repositories/file_lock.py`](../repositories/file_lock.py) provides cross-process locks to prevent race conditions when tasks and API calls write simultaneously.

## Logging & observability
- **Central config** – [`configure_logging`](../core/logging_config.py) sets structured logging with rotating file handlers stored under `/logs`.
- **Order log** – `get_order_logger()` returns a dedicated logger used by `PositionService` to record every order request/response pair.
- **Task logs** – Background services use the `src.tasks` logger namespace to keep health information separate from request logs.
- **Telegram transcripts** – [`src/telegram/telegram_users.py`](../telegram/telegram_users.py) persists chat interactions so admin approvals and notifications are auditable.

## Operational workflows
- **Config backup/restore** – Copy `user_config.yaml` alongside the `/logs` directory before deployments. Restore by placing the file back and calling the settings reload endpoint.
- **Data migrations** – When altering position schema, update `FilePositionRepository` serializers and the `fix_position_race` utility to transform legacy records.
- **Monitoring** – Tail `logs/trading.log` for execution summaries, `logs/tasks.log` for background jobs, and `logs/telegram.log` for chat activity.

## Related references
- `docs/background-jobs.md` for how maintenance tasks interact with storage
- `src/docs/cli_tools.md` for command-line helpers that inspect ledgers