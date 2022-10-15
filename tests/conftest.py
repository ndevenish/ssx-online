from __future__ import annotations

import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import ispyb as ispyb_module
import ispyb.sqlalchemy as ispyb_sqlalchemy
import pytest

logger = logging.getLogger(__name__)
logger_container = logging.getLogger(__name__ + ".container")
logging.basicConfig(level=logging.DEBUG)

# Fill out the settings environment variables
os.environ["ISPYB_CREDENTIALS"] = str(Path(__file__).parent / "test_settings.cfg")


class NoActiveContainerRuntime(RuntimeError):
    pass


class ExistingDatabase:
    def __init__(self, ports):
        self.ports = ports


class RunningContainer:
    def __init__(
        self,
        container_name: str,
        process: subprocess.Popen,
        runtime: ContainerRuntime,
        inspect_state: dict | None = None,
    ):
        self.name = container_name
        self.process = process
        self._runtime = runtime
        self._state = inspect_state
        self._seen_alive = bool(inspect_state)
        self._output_lock = threading.Lock()
        self._redirect_thread = None

    def refresh(self):
        self._state = self._runtime.inspect(self.name)
        if self._state:
            self._seen_alive = True

    @property
    def ports(self) -> dict[int, int]:
        """Returns a dictionary mapping host port to internal port"""
        if self._state is None and not self._seen_alive:
            self.refresh()
        if self._state is None:
            return {}
        port_map = {}
        # Depending on runtime, this is either a list of mappings, or a
        # map of bindings
        port_settings = self._state["NetworkSettings"]["Ports"]
        if isinstance(port_settings, list):
            for setting in port_settings:
                port_map[setting["containerPort"]] = setting["hostPort"]
        else:
            # We are ignoring tcp/udp for now
            rePort = re.compile(r"^(\d+)")
            for container_map, entries in port_settings.items():
                match = rePort.match(container_map)
                assert match is not None
                container_port = int(match.group(1))
                if entries:
                    for entry in entries:
                        port_map[container_port] = int(entry["HostPort"])

        return port_map

    def __repr__(self):
        # We might not have an initial state to be fed
        if self._state is None and not self._seen_alive:
            self.refresh()

        if self._state is None:
            return f"<Container {self.name}: No longer exists>"

        is_alive = ""
        if self.process.poll() is not None:
            is_alive = " (dead)"
        return f"<Container {self.name}: {self._state['State']['Status']}{is_alive}>"

    def send_stdout_to_logger(self):
        """Send all container process logs to stdout, preventing buffer clog"""
        with self._output_lock:
            if self._redirect_thread is not None:
                return

            def _drain_to_log():
                for line in self.process.stdout:
                    logger_container.debug(line.strip())

            self._redirect_thread = threading.Thread(target=_drain_to_log)
            self._redirect_thread.start()


class ContainerRuntime:
    def __init__(self, command: str):
        self._command = command

    def build(self, context: os.PathLike) -> str:
        """Build a context, return the image shasum"""
        result = subprocess.run(
            [self._command, "build", "-q", str(context)],
            capture_output=True,
            check=True,
            text=True,
        )
        # Get the last line, which should be the image id
        last_line = result.stdout.strip().splitlines()[-1].removeprefix("sha256:")
        # This should be a sha256
        assert len(last_line) == 64
        return last_line

    def run(self, image: str, ports: list[int]) -> RunningContainer:
        """
        Run a specified container with configured port bindings.

        Args:
            image: The image name or ID to run
            ports: The ports to map inside the container
        """
        # Work out a process-specific name for this
        name = f"pytest_{os.getpid()}_{uuid.uuid4()}"
        port_cmd = itertools.chain.from_iterable(["-p", str(p)] for p in ports)
        start_time = time.monotonic()
        run_command = [self._command, "run", "--rm", "--name", name, *port_cmd, image]
        logger.info("Running: %s", " ".join(run_command))
        proc = subprocess.Popen(
            run_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )

        while not (state := self.inspect(name)):
            time.sleep(0.1)
        # Wait for this to be fully running
        while (
            proc.poll() is None
            and (state := self.inspect(name)) is not None
            and not state["State"]["Running"]
        ):
            time.sleep(0.2)

        logger.debug(
            "Started container in %.0f ms", (time.monotonic() - start_time) * 1000
        )

        return RunningContainer(name, proc, self, state)

    def inspect(self, container_id: str) -> dict | None:
        data = json.loads(
            subprocess.run(
                [self._command, "inspect", container_id], capture_output=True
            ).stdout
        )
        if not data:
            return None
        return data[0]

    def stop(self, container_id: str | RunningContainer, timeout: int | None = None):
        if not isinstance(container_id, str):
            container_id = container_id.name
        stop_time = []
        if timeout is not None:
            stop_time = ["-t", str(timeout)]
        command = [self._command, "stop", *stop_time, container_id]
        logger.info("Running: %s", " ".join(command))
        start = time.monotonic()
        subprocess.run(command, capture_output=True)
        logger.debug("Stopped container in %.0f ms", (time.monotonic() - start) * 1000)


def _get_container_runtime() -> ContainerRuntime | None:
    """Finds the name of an active container runtime."""
    for runtime in ["docker", "podman"]:
        if cmd := shutil.which(runtime):
            if subprocess.run([cmd, "version"], capture_output=True).returncode == 0:
                return ContainerRuntime(cmd)

    return None


@contextmanager
def running_container(
    docker_context: os.PathLike | str,
    internal_ports: list[int] = [],
    timeout_seconds: int = 10,
) -> Iterator[RunningContainer]:
    """
    Run a container as a context manager.

    The container will always be built, so this should be pointed
    towards a build context that doesn't container unrelated data (e.g.
    in a subfolder), to prevent unnecessary rebuild churn.

    Args:
        docker_context: The context directory to pass to "docker build"
        internal_ports: A list of internal port numbers to expose
    """
    runtime = _get_container_runtime()
    if runtime is None:
        raise NoActiveContainerRuntime()
    image_sha = runtime.build(Path(docker_context))

    container = runtime.run(image_sha, internal_ports)
    try:
        yield container
    finally:
        runtime.stop(container, timeout=0)


@pytest.fixture(scope="session")
def ispyb_database():
    """Spin up an instance of the ISPyB database for these tests"""
    if (port := os.getenv("ISPYB_DATABASE_PORT")) is not None:
        logger.info("Using existing ISPyB port: %s", port)
        yield ExistingDatabase(ports={3306: int(port)})
        return
    # No existing database to use, let's start our own
    try:
        with running_container(
            Path(__file__).parent / "ispyb-database", internal_ports=[3306]
        ) as container:
            start = time.monotonic()
            # Wait until the temporary server has stopped
            for line in container.process.stdout:
                logger_container.debug(line.strip())
                if "Temporary server stopped" in line:
                    break
            # Now wait until we are ready
            for line in container.process.stdout:
                logger_container.debug(line.strip())
                if "ready for connections" in line:
                    break
            logger.debug(
                "Took %.0f ms for final database to be available",
                (time.monotonic() - start) * 1000,
            )
            # We want all remaining container logs to go straight to logger
            container.send_stdout_to_logger()

            yield container
    except NoActiveContainerRuntime:
        pytest.skip(
            "No container runtime available and no ISPYB_DATABASE_PORT alternative specified."
        )


class FakeISpyBRawConfigParser:
    def __init__(self, section_map: dict[str, dict[str, str]]):
        self._sections = section_map

    def __call__(self, **kwargs):
        return self

    def read(self, credentials_file):
        return True

    def has_section(self, section: str) -> bool:
        return section in self._sections

    def items(self, section: str) -> list[tuple[str, str]]:
        return [(x, y) for x, y in self._sections[section].items()]


class FakeConfigParserModule:
    def __init__(self, section_map: dict[str, dict[str, str]]):
        self.RawConfigParser = FakeISpyBRawConfigParser(section_map)


@pytest.fixture
def ispyb_credentials(monkeypatch):
    credentials_lookup = {}
    fake_module = FakeConfigParserModule(credentials_lookup)
    monkeypatch.setattr(ispyb_module, "configparser", fake_module)
    monkeypatch.setattr(ispyb_sqlalchemy, "configparser", fake_module)
    yield credentials_lookup


@pytest.fixture
def ispyb(ispyb_database, ispyb_credentials):
    ispyb_credentials["ispyb_sqlalchemy"] = {
        "username": "root",
        "password": "",
        "host": "localhost",
        "port": ispyb_database.ports[3306],
        "database": "ispyb",
    }
    ispyb_credentials["ispyb_mariadb_sp"] = {
        "user": "root",
        "pw": "",
        "host": "localhost",
        "port": ispyb_database.ports[3306],
        "db": "ispyb",
    }
    return ispyb_database


if __name__ == "__main__":
    print("Testing container abstract interface")
    runtime = _get_container_runtime()
    assert runtime is not None
    sha = runtime.build(Path(__file__).parent / "ispyb-database")
    print(f"Built {sha}")
    proc = runtime.run(sha, ports=[5546])
    print(f"Started process {proc.process.pid}")
    while proc.process.poll() is None:
        proc.refresh()
        print(proc, proc.ports)
        time.sleep(1)
    print(proc.process.wait())
    proc.refresh()
    print(proc)
    print(f"Process ended with code {proc.process.returncode}")
