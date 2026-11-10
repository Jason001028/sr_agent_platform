"""Offload short synchronous control work while retaining shutdown ownership."""
import asyncio
import logging


async def run_sync(fn, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Native DB calls cannot be stopped by cancelling their asyncio waiter.
        # Finish them before lifespan closes the Store and its connections.
        try:
            await asyncio.shield(task)
        except Exception:
            logging.getLogger(__name__).exception("Control work failed during shutdown")
        raise
