import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles.os

logger = logging.getLogger(__name__)


class FileLineReader:
    def __init__(self, filename: Path):
        self._filename = filename
        self._seek = None
        self._cancel = asyncio.Event()

    def cancel(self) -> None:
        self._cancel.set()

    async def readlines_continuous(self) -> AsyncIterator[str]:
        """
        Read a single line at a time, for all lines.

        If the file does not yet exist, this function will wait until it does.
        If the file ends, then
        """
        while not await aiofiles.os.path.isfile(self._filename):
            logger.debug(
                "File %s does not exist yet, waiting for creation", self._filename
            )
            try:
                await asyncio.wait_for(self._cancel.wait(), timeout=1.0)
                logger.debug(
                    "FileLineReader waiting for %s cancelled before file appeared",
                    self._filename,
                )
                return
            except asyncio.TimeoutError:
                # We weren't cancelled, check again to see if the file is there yet
                pass

        async with aiofiles.open(self._filename, "r") as f:
            partial = ""
            while True:
                # Read lines until we don't get any more
                while line := await f.readline():
                    if not line.endswith("\n"):
                        # We got a partial line. Buffer it until we have a full line.
                        logger.debug("Read partial line, buffering: %r", line)
                        partial += line
                        continue
                    line = partial + line
                    partial = ""
                    logger.debug("Internal Read line: %r", line)
                    yield line
                try:
                    await asyncio.wait_for(self._cancel.wait(), timeout=1.0)
                    logger.debug("FileLineReader was cancelled")
                    return
                except asyncio.TimeoutError:
                    # This is fine - we waited but it wasn't cancelled
                    pass
