"""Command-line entrypoint: `python -m valuebet.cli <command>`."""

from __future__ import annotations

import argparse

from .core.models import Sport
from .logging import configure_logging, get_logger

log = get_logger("cli")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="valuebet")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create tables (+ timescale hypertable)")

    p_scan = sub.add_parser("scan", help="run one value scan and persist signals")
    p_scan.add_argument("--sport", default="soccer", choices=[s.value for s in Sport])
    p_scan.add_argument("--live", action="store_true")

    sub.add_parser("pnl", help="print P&L summary")

    args = parser.parse_args()

    if args.cmd == "init-db":
        from .db.init import init_db

        init_db()
        log.info("done", cmd="init-db")
    elif args.cmd == "scan":
        from .pipeline import run_scan

        count = run_scan(Sport(args.sport), live=args.live)
        log.info("done", cmd="scan", new_signals=count)
    elif args.cmd == "pnl":
        from .db.repository import pnl_summary
        from .db.session import session_scope

        with session_scope() as session:
            log.info("pnl", **pnl_summary(session))


if __name__ == "__main__":
    main()
