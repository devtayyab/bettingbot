# ValueBet — Automated Value-Betting Pilot

Identifies value-betting opportunities by treating **Betfair Exchange** as the sharp
reference and **Pinnacle** as confirmation, then surfaces qualifying bets for a
**single Stoiximan account**. Detection, sizing, logging and P&L are fully automated;
placement runs through Playwright behind a dry-run + human-approval gate.

> **Scope (per the pilot brief).** This system operates **one** Stoiximan account as
> a normal user. It contains **no** multi-account orchestration, device-fingerprint
> spoofing, proxy rotation, or anti-detection behaviour — those were intentionally
> removed from scope. Automating placement may still breach a bookmaker's terms and
> can get an account limited; that risk is the operator's. Use within the law and the
> platforms' terms in your jurisdiction.

## Architecture

```
                ┌──────────────┐     ┌──────────────┐
                │ Betfair API  │     │ Pinnacle API │
                │ (reference)  │     │(confirmation)│
                └──────┬───────┘     └──────┬───────┘
                       │  OddsSource interface │
                       └───────────┬───────────┘
                                   ▼
   scheduler ──► pipeline ──► ValueEngine ──────────► signals (DB)
   (APScheduler)   │          de-vig → favorite           │
                   │          filter → edge ≥ 4.9%         │
                   ▼          → Pinnacle confirm           ▼
            odds_snapshot     → Kelly sizing        FastAPI + Dashboard
            (Timescale)                              │  approve / reject
                                                     ▼
                                          Playwright placement worker
                                          (Stoiximan, single account)
                                          dry-run + approval guarded
                                                     │
                                                     ▼
                                              bets (DB) → P&L
```

### Components (`src/valuebet/`)
| Module | Responsibility |
|---|---|
| `core/odds_math.py` | Pure math: implied prob, de-vig (multiplicative / additive / **Shin**), edge, Kelly. Fully unit-tested. |
| `core/models.py` | Domain dataclasses (`Quote`, `MarketSnapshot`, `ValueSignal`). |
| `sources/` | `OddsSource` interface + Betfair, Pinnacle, and a deterministic mock source. |
| `engine/value_engine.py` | The pipeline: align markets → de-vig → favorite filter → edge threshold → sharp confirmation → size. |
| `engine/matching.py` | Conservative cross-source market/selection matching. |
| `placement/stoiximan.py` | Playwright worker for one account. Dry-run + approval + price-protection guards. |
| `db/` | SQLAlchemy models, repository, session, init. `odds_snapshot` is a Timescale hypertable. |
| `api/` | FastAPI: signals feed, approve/reject, place, settle, P&L + a self-contained dashboard. |
| `scheduler/run.py` | APScheduler polling (live vs pre-match cadences). |
| `cli.py` | `init-db`, `scan`, `pnl`. |

## How a signal is decided
For each Stoiximan market, aligned to Betfair + Pinnacle:
1. **De-vig Betfair** (multiplicative) → fair probability per selection.
2. **Favorites only**: `fair_prob ≥ FAVORITE_MIN_PROB` (default 0.55).
3. **Edge**: `edge = fair_prob × stoiximan_odds − 1` must be `≥ EDGE_THRESHOLD` (default 0.049).
4. **Confirmation**: de-vig Pinnacle (Shin) and require it within `CONFIRMATION_TOLERANCE` of Betfair. A price both sharps disagree with is a trap, not value.
5. **Sizing**: fractional Kelly (`KELLY_FRACTION`, default quarter-Kelly), capped at `MAX_STAKE`.

All thresholds live in `.env` (see `.env.example`).

## Quick start (local, no credentials)
Runs against deterministic mock sources so you can see the full loop immediately.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# SQLite for a zero-infra demo:
export DATABASE_URL="sqlite:///./valuebet.db"
python -m valuebet.cli init-db
python -m valuebet.cli scan --sport soccer       # detects + persists signals
uvicorn valuebet.api.main:app --reload           # open http://localhost:8000
```

In the dashboard: trigger a scan, **Approve** a signal, **Place** (dry-run prepares
the slip without committing), then **Settle** to see P&L update.

## Testing locally

### 1. Run the test suite (no infra, no credentials)
The decision-critical code (odds math + engine) is pure and runs anywhere:
```bash
pip install -e ".[dev]"
pytest -q                       # 29 tests: odds math, engine, confirmation gating, dedup
pytest --cov=valuebet -q        # with coverage
```

> **Python version note.** The code targets **Python 3.11+** (it uses `X | None`
> annotations). On 3.9/3.10 the pure tests still pass, but the SQLAlchemy/FastAPI
> layers need either 3.11 or `pip install eval_type_backport`. The Docker image is
> 3.11, so containers are unaffected.

### 2. Exercise the full pipeline on SQLite (mock odds, zero infra)
Mock sources stand in for Betfair/Pinnacle/Stoiximan, so you see the entire loop —
scan → signal → approve → place (dry-run) → settle → P&L — without any accounts:
```bash
export DATABASE_URL="sqlite:///./valuebet.db"
python -m valuebet.cli init-db
python -m valuebet.cli scan --sport soccer     # detect + persist (run twice: dedup => 0 new)
python -m valuebet.cli pnl
uvicorn valuebet.api.main:app --reload         # http://localhost:8000
```
In the dashboard: **Scan → Approve → Place** (dry-run prepares the slip without
committing) **→ Settle**. P&L updates live.

You can also drive it over HTTP:
```bash
curl -X POST "localhost:8000/scan?sport=soccer"
curl localhost:8000/signals
curl -X POST localhost:8000/signals/1/approve
curl -X POST localhost:8000/signals/1/place -H 'content-type: application/json' -d '{}'
curl localhost:8000/pnl
```

### 3. Test against a real Postgres/Timescale locally
```bash
docker compose up -d db redis          # just the datastores
export DATABASE_URL="postgresql+psycopg://valuebet:valuebet@localhost:5432/valuebet"
alembic upgrade head                   # creates schema + hypertable
pytest -q && python -m valuebet.cli scan --sport soccer
```

### 4. Test the Playwright placement worker
```bash
pip install playwright && playwright install chromium
# Keep PLACEMENT_DRY_RUN=true. With dry-run on, the worker prepares the slip and
# reads the live price but never clicks "place". Requires the live-site selectors
# in placement/stoiximan.py to be filled in first (see "Going live").
```

## Deployment

### Option A — Docker Compose (single host: VPS / EC2 / droplet)
The fastest production-like deploy. Brings up Timescale, Redis, the API, and the scheduler.
```bash
cp .env.example .env                   # fill in real credentials + tuning
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose logs -f scheduler       # watch scans run
```
- API/dashboard on `:8000`; put **nginx/Caddy + TLS** in front and **do not** expose
  `:8000` publicly without auth (the dashboard can place real bets).
- The placement worker needs Chromium — build that image with
  `--build-arg INSTALL_BROWSERS=true` (already supported in the `Dockerfile`).
- `docker compose down` / `up -d` to restart; the `dbdata` volume persists odds & bets.

### Option B — Managed pieces (more durable)
| Piece | Managed option |
|---|---|
| Database | Timescale Cloud, or AWS RDS Postgres + `timescaledb` extension |
| Containers | the same image on Fly.io / Render / ECS / a systemd unit |
| Secrets | inject `.env` via the platform's secret store — never commit `.env` |
| Scheduler | run `python -m valuebet.scheduler.run` as its own always-on service |

Run **API** and **scheduler** as separate services off the same image
(`uvicorn valuebet.api.main:app` vs `python -m valuebet.scheduler.run`) so polling
keeps running even if the API restarts.

### Deployment checklist
- [ ] `DATABASE_URL` points at managed Postgres; `alembic upgrade head` applied.
- [ ] Secrets set via the platform (Betfair certs mounted as files, not env text).
- [ ] `ENV=prod` (switches logs to JSON) and `LOG_LEVEL=INFO`.
- [ ] Dashboard behind auth + TLS; `:8000` not publicly open.
- [ ] `PLACEMENT_DRY_RUN=true` until the live Stoiximan integration is validated.
- [ ] Back up the `dbdata` volume / enable automated DB snapshots.

## Going live (the deliberate gates)
Real bets are placed **only** when all of these are true:
- `PLACEMENT_DRY_RUN=false`
- the signal is **approved** in the dashboard (unless `PLACEMENT_REQUIRE_APPROVAL=false`)
- live odds are still within `slippage` of the detected price (price protection)

Before disabling dry-run you **must** validate the DOM selectors in
`placement/stoiximan.py:SELECTORS` and implement `_navigate_to_selection` against the
live Stoiximan site (left as a clearly-marked integration point).

## Roadmap (post-pilot)
- Read-only Stoiximan odds reader (replace the mock target) for live edge detection.
- Closing-line-value (CLV) tracking from `odds_snapshot` — the truest measure of edge.
- Backtester over stored snapshots to calibrate `EDGE_THRESHOLD` empirically.
- Alembic autogenerate wired to CI; Prometheus metrics on the scheduler.
