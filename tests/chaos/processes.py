"""Real service processes for chaos tests: started like production (``python -m …``) and killed without warning."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil


class ServiceProcess:
    def __init__(
        self, module: str, *args: str, log_dir: Path, name: str, env: dict[str, str] | None = None
    ) -> None:
        self.command = [sys.executable, "-m", module, *args]
        self.log_path = log_dir / f"{name}.log"
        self.env = {**os.environ, **(env or {})}
        self.name = name
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> ServiceProcess:
        log = self.log_path.open("ab")
        # The environment carries the test database, Redis DB 1 and the short test timeouts (tests/conftest.py).
        self.process = subprocess.Popen(
            self.command, env=self.env, stdout=log, stderr=subprocess.STDOUT
        )
        return self

    @property
    def pid(self) -> int:
        assert self.process is not None
        return self.process.pid

    def tree(self) -> list[psutil.Process]:
        try:
            root = psutil.Process(self.pid)
            # Virtual-env launchers on Windows run the interpreter as a child: kill the whole tree.
            return [*root.children(recursive=True), root]
        except psutil.NoSuchProcess:
            return []

    def kill(self) -> None:
        """SIGKILL (TerminateProcess on Windows): no shutdown hooks, no lease release, no final writes."""
        for proc in self.tree():
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
        if self.process is not None:
            self.process.wait(timeout=10)

    def stop(self, timeout: float = 15) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        tree = self.tree()
        for proc in tree:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                continue
        _, alive = psutil.wait_procs(tree, timeout=timeout)
        for proc in alive:
            proc.kill()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def log_tail(self, lines: int = 40) -> str:
        if not self.log_path.exists():
            return ""
        return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
