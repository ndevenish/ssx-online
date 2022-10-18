from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar, Type, TypeVar, cast

import aiofiles.os
import numpy as np

logger = logging.getLogger(__name__)

C = TypeVar("C", bound="FileWatcherEmitter")
# T = TypeVar("T")


class FileWatcherEmitter:
    """
    Continuously watch a file for updates, process each line, then notify listeners.

    Upon creation, enqueues itself as a task on the current threads event loop.
    """

    # A per-class-type lookup for watcher instances
    _watchers: dict[
        Type[FileWatcherEmitter], dict[Path, FileWatcherEmitter]
    ] = defaultdict(dict)
    # A list of tasks, to prevent destruction
    _tasks: ClassVar[set[asyncio.Task]] = set()

    def __init__(self, filename: Path):
        self._filename = filename
        self._listeners: set[asyncio.Queue] = set()
        self._item_count = 0
        # Start reading and consuming this file
        task = asyncio.create_task(self._consume_results())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_results(self) -> None:
        reader = FileLineReader(self._filename)
        async for lines in reader.read_lines_continuous():
            new_items = 0
            for line in lines:
                new_items += self.process_line(line)
            self._item_count += new_items
            if new_items > 0:
                # Send messages to all the listeners for this
                for listener in self._listeners:
                    listener.put_nowait(new_items)

    def process_line(self, line: str) -> int:
        """
        Process a line. Return the number of items processed.

        The number of items will be accumulated over groups of lines,
        and listeners will be notified about this many new entries.
        """
        raise NotImplementedError

    def add_listener(self, listener: asyncio.Queue, from_index: int = 0) -> None:
        self._listeners.add(listener)
        # If the listener has asked for an index less than our current,
        # then send it a message saying how much data is available.
        if from_index < self._item_count:
            listener.put_nowait(self._item_count - from_index)

    @classmethod
    def get_watcher_for_file(cls: type[C], filename: Path) -> C:
        if filename in cls._watchers[cls]:
            return cast(C, cls._watchers[cls][filename])
        watcher = cls(filename)
        cls._watchers[cls][filename] = watcher
        return watcher


class PIAWatcher(FileWatcherEmitter):
    def __init__(self, filename: Path):
        # Have a preallocated numpy array that we can write into
        self._data = np.empty((26000, 3), dtype=int)
        self._data_entries = 0
        super().__init__(filename)

    def __getitem__(self, index) -> np.ndarray:
        return self._data[: self._data_entries][index]

    def process_line(self, line) -> int:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Line was not valid JSON object, ignoring: %r", line)
            return 0
        try:
            self._data[self._data_entries] = (
                data["file-number"],
                data["n_spots_total"],
                data["n_spots_4A"],
            )
        except KeyError:
            logger.warning(
                "Could not read file-number, n_spots_total or n_spots_4A from: %r", data
            )
            return 0

        self._data_entries += 1
        # Do we need to grow the output?
        if self._data_entries == self._data.shape[0]:
            new_data = np.empty((self._data.shape[0] + 10000, self._data.shape[1]))
            new_data[: self._data_entries] = self._data
            self._data = new_data
        # We always processed one item here
        return 1


class PIAListener:
    """
    Listen for PIA results from a particular source.

    This class doesn't do the listening, but sets itself up to receive update
    notifications from a separate sender.
    """

    def __init__(self, filename: Path, offset: int = 0):
        self._filename = filename
        self._offset = offset
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._watcher = PIAWatcher.get_watcher_for_file(filename)
        self._watcher.add_listener(self._queue, offset)

    async def get_data_chunk(self) -> np.ndarray:
        count = await self._queue.get()
        return self._watcher[-count:]


class FileLineReader:
    def __init__(self, filename: Path):
        self._filename = filename
        self._seek = None
        self._cancel = asyncio.Event()
        self._line = 0
        self._seek = 0

    def cancel(self) -> None:
        self._cancel.set()

    async def read_lines_continuous(self) -> AsyncIterator[list[str]]:
        """
        Read a single line at a time, for all lines.

        If the file does not yet exist, this function will wait until it does.

        If the file ends, the this function will wait until more data arrives.
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
                data: str
                while data := await f.read():
                    self._seek = await f.tell()
                    lines = data.splitlines(keepends=True)
                    # If we have at least one full line, include the partial start
                    if "\n" in data:
                        lines[0] = partial + lines[0]
                        partial = ""
                    # If we have a partial end line, combine it
                    if not data.endswith("\n"):
                        logger.debug("Read partial line, buffering: %r", lines[-1])
                        partial = lines[-1]
                        lines = lines[:-1]

                    if lines:
                        logger.debug("Internal Read lines: %r", lines)
                        self._line += len(lines)
                        yield lines
                try:
                    await asyncio.wait_for(self._cancel.wait(), timeout=1.0)
                    logger.debug("FileLineReader was cancelled")
                    return
                except asyncio.TimeoutError:
                    # This is fine - we waited but it wasn't cancelled
                    pass
