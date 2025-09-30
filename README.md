# AlgoBot (Demonstration Overview)

> **Demonstration Note:** This portfolio-ready README describes the project at a high level. Deployment and setup instructions have been intentionally removed; see the original `README.md` or ops runbooks for implementation details.

AlgoBot is a FastAPI-based trade automation service that ingests strategy webhooks, manages Binance positions, enforces guardrails, and exposes a lightweight operator interface (web + Telegram). The project combines a stateless API tier with file-backed persistence, long-running safety jobs, and chat-driven controls to keep discretionary oversight while reacting to market signals within seconds.

## How the codebase is organised

The repository follows a layered layout so that every production concern has a dedicated module. Start with the **runtime layer** (entry point + dependency wiring) and branch into **adapters**, **services**, **tasks**, and **interfaces** as needed.

| Area | Purpose | Start here |
| --- | --- | --- |
| Application runtime | Bootstraps the FastAPI app, background workers, and Telegram subprocess. | [`main.py`](main.py) |
| Core models & config | Shared domain models, typed config accessors, logging setup. | [`src/core/`](src/core) |
| Exchange adapters | Binance REST/WebSocket abstractions with spot/futures support. | [`src/adapters/`](src/adapters) |
| Persistence | File-backed repositories, optimistic locking, background repair utilities. | [`src/repositories/`](src/repositories) |
| Domain services | Trade execution + signal orchestration logic. | [`src/services/`](src/services) |
| API surface | REST routes for webhooks and runtime settings management. | [`src/api/`](src/api) |
| Background tasks | Continuous maintenance, safety sweeps, and orderly shutdown helpers. | [`src/tasks/`](src/tasks) |
| Operator interfaces | Telegram bot, HTML settings console, chart capture utilities. | [`src/telegram/`](src/telegram), [`src/frontend/`](src/frontend) |
| Documentation | Deep-dive guides linked below. | [`src/docs/`](docs) |

## System walkthrough

1. **Startup sequence** – `main.py` wires adapters, repositories, and services, then schedules periodic maintenance/safety jobs and spawns the Telegram bot in a dedicated process.
2. **Signal ingestion** – TradingView (or similar) webhooks hit `/api/webhook`, where payloads are validated before being dispatched to the `SignalProcessor`.
3. **Position lifecycle** – `SignalProcessor` coordinates entries, take-profit legs, and stop-outs by delegating to `PositionService`, which calculates order sizing, hits the Binance adapter, and persists outcomes via the repository.
4. **Ongoing safety** – `SafetyTasks` and `MaintenanceTasks` run continuously to enforce stop-loss/age rules, reconcile ledgers, and repair file inconsistencies.
5. **Operator feedback** – Notifications and manual overrides flow through Telegram commands, plus a small settings UI served from `src/frontend` for browser-based administration.

Each step is documented in more detail inside the `docs/` directory:

- [Architecture & runtime orchestration](src/docs/architecture.md)
- [Signal processing & trade lifecycle](src/docs/trade-lifecycle.md)
- [Background maintenance & safeguards](src/docs/background-jobs.md)
- [Configuration, data stores, and logging](src/docs/operations.md)
- [Operator interfaces (web + Telegram)](src/docs/interfaces.md)

## Example: Settings web console

The built-in settings console (`src/frontend/settings.html`) is a statically served Single Page App that lets an authenticated operator adjust risk parameters on the fly. Key UI regions:

1. **Authentication gate** – The `/settings` router serves a login page that checks the `SETTINGS_API_KEY` before revealing controls.
2. **Configuration explorer** – A collapsible tree mirrors `user_config.yaml`, letting operators tweak leverage, allowed symbols, or safety intervals.
3. **Live status banner** – Displays adapter mode (spot vs. futures, live vs. testnet) and timestamp of the latest config reload.
4. **Change log panel** – Highlights unsaved edits and the payload that will be PATCHed to `/api/settings`.
5. **Quick actions** – Buttons trigger maintenance routines (e.g., refresh cached positions) via the same authenticated API.

Refer to [the interface guide](src/docs/interfaces.md) for component-level callouts and request/response examples that power this page.

## Deployment-at-a-glance

While the implementation details are intentionally omitted here, the service is designed to run as a long-lived FastAPI process (Uvicorn or Gunicorn) alongside a background Telegram worker. Successful deployments typically include:

- External secret management for Binance and Telegram credentials.
- Persistent storage (local volume or S3) mapped to `src/data/` for open/closed position ledgers.
- A reverse proxy terminating HTTPS and forwarding to the FastAPI app.
- Scheduled backups of the data directory plus log shipping for the trade/order log files.

For additional context (sequence diagrams, CLI tools, etc.), explore the rest of the curated documentation inside the [`docs/`](docs) folder.