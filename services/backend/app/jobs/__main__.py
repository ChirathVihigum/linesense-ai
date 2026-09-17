"""``python -m app.jobs``: run a durable job worker until SIGINT/SIGTERM."""

from __future__ import annotations

import argparse
import asyncio
import signal
from collections.abc import Sequence

from app.db.session import get_engine, get_session_factory
from app.jobs.handlers import build_registry
from app.jobs.queue import QUEUES
from app.jobs.worker import Worker
from app.logging import configure_logging
from app.settings import get_settings


def _parse_queues(value: str) -> list[str]:
    queues = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(queues) - set(QUEUES))
    if not queues or unknown:
        raise argparse.ArgumentTypeError(
            f"queues must be a comma-separated subset of {','.join(QUEUES)}"
        )
    return queues


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description=__doc__)
    parser.add_argument("--queues", type=_parse_queues, default=list(QUEUES))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--worker-id", default=None)
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(settings)
    worker = Worker(
        registry=build_registry(settings),
        session_factory=get_session_factory(settings.database_url),
        settings=settings,
        queues=args.queues,
        concurrency=args.concurrency,
        worker_id=args.worker_id,
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    try:
        await worker.run(stop_event)
    finally:
        await get_engine(settings.database_url).dispose()


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    main()
