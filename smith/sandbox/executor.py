from multiprocessing.context import SpawnProcess
from multiprocessing.connection import Connection
from keyword import iskeyword
from smith.models.sandbox_config import SandboxConfig
from smith.sandbox.mcp_bridge import MCPBridge
from smith.sandbox.worker import worker_main

import multiprocessing as mp
import json
from typing import NoReturn
from pydantic import BaseModel


class ExecutionResult(BaseModel):
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    timed_out: bool = False
    restarted: bool = False
    final_answer: str | None = None


class ExecutorError(Exception):
    pass


class SandboxExecutor:
    def __init__(
        self, config: SandboxConfig, bridge: MCPBridge | None = None
    ) -> None:
        self.bridge = bridge
        if self.bridge is not None and self.bridge.session is None:
            raise ExecutorError("Bridge session is not initialised")

        self.tools: list = []
        if self.bridge is not None:
            no_tool_check = ["final_answer", "print", "open", "len"]
            self.tools = [
                tool
                for tool in list(set(self.bridge.list_tools()))
                if isinstance(tool.get("name", ""), str)
                and tool.get("name", "").isidentifier()
                and not iskeyword(tool.get("name", ""))
                and not tool.get("name", "").startswith("__")
                and tool.get("name", "") not in no_tool_check
            ]

        self.config = config
        self.ctx = mp.get_context("spawn")
        self.process: SpawnProcess | None = None
        self.conn: Connection | None = None

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
            args=(conn2, self.config.model_dump(), self.tools),
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

    def close(self, instant: bool = False):
        if not self.process or not self.conn:
            return

        self.conn.close()
        if instant:
            self.process.join(timeout=0)
        else:
            self.process.join(timeout=2)

        if self.process.is_alive():
            self.process.kill()
            self.process.join()

        self.process, self.conn = None, None

    def execute_code(self, code: str) -> ExecutionResult:
        if self.process and not self.process.is_alive():
            self.close()
            self.start()
        if not self.process or not self.conn:
            self.start()

        message = {}
        message["type"] = "exec"
        message["code"] = code
        msg_str = json.dumps(message)
        if not self.conn:
            raise ExecutorError("Conection is not established")
        try:
            self.conn.send_bytes(msg_str.encode())
        except OSError:
            return ExecutionResult(
                error=self._handle_child_death(), restarted=True
            )

        if not self.conn.poll(self.config.max_execution_time_seconds):
            self.close(True)
            self.start()
            return ExecutionResult(
                error=f"Execution timed out after: {self.config.max_execution_time_seconds}s, possible infinite loop. Sandbox restarted: all variables and functions defined earlier were lost.",
                timed_out=True,
                restarted=True,
            )

        try:
            response = self.conn.recv_bytes()
        except (OSError, EOFError):
            return ExecutionResult(
                error=self._handle_child_death(),
                restarted=True,
            )

        try:
            response_dict = json.loads(response)
            if isinstance(response_dict, dict):
                if response_dict.get("type", "") == "error":
                    raise ExecutorError(response_dict.get("message", ""))
                if not response_dict.get("type") == "result":
                    raise ExecutorError("Invalid response from child")
            else:
                raise ExecutorError(
                    "Invalid response, from child: not a dictionnary"
                )

        except json.JSONDecodeError:
            raise ExecutorError("Error when decoding json from child response")

        result = ExecutionResult(
            stdout=response_dict.get("stdout", ""),
            stderr=response_dict.get("stderr", ""),
            error=response_dict.get("error", ""),
            final_answer=response_dict.get("final_answer", ""),
        )
        return result

    def _handle_child_death(self) -> str:
        if not self.process:
            raise ExecutorError("Process is not initialised")
        self.process.join(1)
        code = self.process.exitcode
        self.close(True)
        self.start()
        return f"The sandbox process crashed (exit code {code}) while running your code. It was restarted: all variables and functions defined earlier were lost."
