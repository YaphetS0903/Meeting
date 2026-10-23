"""Run independently of Redis/Celery so their outages remain observable."""

import argparse
import fcntl
import logging
import signal
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.monitoring import collect, public_monitor_state, state_path, update_alert


def main():
    parser = argparse.ArgumentParser(description="会议系统独立运行监控")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true", help="检查独立监控进程是否持续运行，不发送告警")
    args = parser.parse_args()
    config = get_settings()
    if args.check:
        return 0 if public_monitor_state(config)["running"] else 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "process.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.error("Another monitor already owns this storage directory")
            return 1
        engine = create_engine(
            config.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3} if config.database_url.startswith("postgresql") else {},
        )
        factory = sessionmaker(engine)
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopped.set())
        try:
            while not stopped.is_set():
                try:
                    snapshot = collect(factory, config)
                    state = update_alert(snapshot, config)
                    logging.info(
                        "checks=%s issues=%s alert=%s",
                        len(snapshot["checks"]),
                        len(snapshot["issues"]),
                        state["delivery"],
                    )
                    if args.once:
                        return 0 if snapshot["healthy"] else 2
                except Exception as exc:
                    # Exception text can contain service credentials; never log it.
                    logging.error("Monitor check failed (%s)", type(exc).__name__)
                    if args.once:
                        return 1
                stopped.wait(config.monitor_interval_seconds)
        finally:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
