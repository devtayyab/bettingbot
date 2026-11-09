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

    p_login = sub.add_parser("login-stoiximan", help="interactive browser login to save Stoiximan session cookies")
    p_login.add_argument("--url", default="https://www.stoiximan.com.cy/", help="Stoiximan website URL")
    p_login.add_argument("--output", default=None, help="custom cookie JSON output path")

    p_cookie = sub.add_parser("cookie-status", help="check saved Stoiximan session status")
    p_cookie.add_argument("--path", default=None, help="custom cookie JSON path")

    p_login_ge = sub.add_parser("login-goldedge", help="interactive browser login to save GoldEdge session cookies")
    p_login_ge.add_argument("--url", default="https://www.goldedge.net/", help="GoldEdge website URL")
    p_login_ge.add_argument("--output", default=None, help="custom cookie JSON output path")

    p_sync_ge = sub.add_parser("sync-goldedge", help="scrape value bets from GoldEdge and save into database")
    p_sync_ge.add_argument("--min-edge", type=float, default=None, help="minimum edge threshold (e.g. 0.02)")

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
    elif args.cmd == "cookie-status":
        from .placement.session_store import get_cookie_status

        status = get_cookie_status(args.path)
        log.info("cookie_status", **status)
        print(f"Stoiximan Session Status: {status['message']}")
        print(f"Path: {status['path']}")
    elif args.cmd == "login-stoiximan":
        from .placement.stoiximan import interactive_login

        saved_path, count = interactive_login(url=args.url, cookie_path=args.output)
        log.info("login_completed", path=str(saved_path), count=count)
        print(f"✅ Successfully saved {count} cookies to {saved_path}")
    elif args.cmd == "login-goldedge":
        from .sources.goldedge import interactive_login as ge_login

        saved_path, count = ge_login(url=args.url, cookie_path=args.output)
        log.info("goldedge_login_completed", path=str(saved_path), count=count)
        print(f"✅ Successfully saved {count} cookies to {saved_path}")
    elif args.cmd == "sync-goldedge":
        from .sources.goldedge import sync_goldedge_to_db

        count = sync_goldedge_to_db(min_edge=args.min_edge)
        log.info("goldedge_sync_completed", persisted=count)
        print(f"✅ Successfully synced {count} value bets from GoldEdge into database.")


if __name__ == "__main__":
    main()
