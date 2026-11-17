from multiprocessing.context import SpawnProcess
from smith.models.sandbox_config import SandboxConfig
from smith.sandbox.worker import worker_main

import multiprocessing as mp
import json
import time
from typing import NoReturn


class ExecutorError(Exception):
    pass


class SandboxExecutor:
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self.ctx = mp.get_context("spawn")
        self.process = None
        self.conn = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return

    def start(self) -> None:
        conn1, conn2 = self.ctx.Pipe()

        process = self.ctx.Process(
            target=worker_main,
            args=(conn2, self.config.model_dump()),
            daemon=True,
        )

        process.start()
        conn2.close()

        if not conn1.poll(10):
            self._abort_start(
                process,
                "Process did not send back 'ready' flag",
            )

        try:
            message = conn1.recv_bytes()
            message = json.loads(message)
        except (EOFError, json.JSONDecodeError):
            self._abort_start(
                process,
                "Error decoding json or process killed during execution",
            )

        if not (isinstance(message, dict) and message.get("type") == "ready"):
            self._abort_start(
                process,
                "Invalid 'ready' flag from process",
            )

        self.process, self.conn = process, conn1

    def _abort_start(self, process: SpawnProcess, reason: str) -> NoReturn:
        process.kill()
        process.join()
        raise ExecutorError(reason)

    def close(self):
        if not self.process or not self.conn:
            return

        self.conn.close()
        self.process.join(timeout=2)

        if self.process.is_alive():
            self.process.kill()
            self.process.join()

        self.process, self.conn = None, None

    def execute_code(self, code) -> str:
        return ""
