"""Graceful-shutdown signal wiring, shared by any service running a
consume-until-stopped loop."""

import asyncio
import signal


def install_shutdown_handlers(stopping: asyncio.Event) -> None:
    """Arm SIGINT/SIGTERM to set `stopping` rather than killing the process
    outright, so a consume loop can finish its in-flight message and commit
    before exiting."""
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stopping.set)
