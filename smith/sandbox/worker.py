from multiprocessing.connection import Connection
from contextlib import redirect_stderr, redirect_stdout
import json
import io
import traceback
import resource


class WorkerError(Exception):
    pass


class FinalAnswerSignal(BaseException):
    pass


class Worker:
    def __init__(self, conn: Connection, config: dict) -> None:
        self.conn: Connection | None = conn
        self.config = config
        self.final_value: str | None = None
        self.variable_dic = {"final_answer": self.final_answer}
        self.variable_dic["__name__"] = "__sandbox__"

    def final_answer(self, answer: object):
        self.final_value = str(answer)
        raise FinalAnswerSignal("Final answer set.")

    def send_dict(self, message: dict):
        dict_str = json.dumps(message)
        if not self.conn:
            raise WorkerError("Connection is None")
        self.conn.send_bytes(dict_str.encode())

    def receive_dict(self) -> dict | None:
        if not self.conn:
            raise WorkerError("Connection is None")
        message = self.conn.recv_bytes()

        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            return

        if not isinstance(message, dict):
            return

        return message

    def execute_code(self, code: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        error = ""
        self.final_value = None

        with redirect_stderr(stderr), redirect_stdout(stdout):
            try:
                result = compile(code, filename="<sandbox>", mode="exec")
                exec(result, self.variable_dic)
            except FinalAnswerSignal:
                pass
            except Exception:
                error = traceback.format_exc()

        return {
            "type": "result",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "error": error,
            "final_answer": self.final_value,
        }

    def run(self) -> None:
        memory_limit = self.config["max_memory_mb"]
        memory_limit *= 1000000
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))

        self.send_dict({"type": "ready"})

        while True:
            try:
                message = self.receive_dict()
            except EOFError:
                return
            if (
                not message
                or not message.get("type", None) == "exec"
                or not isinstance(message.get("code", None), str)
            ):
                self.send_dict(
                    {
                        "type": "error",
                        "message": "Invalid message: a dict of type 'exec' with code of type 'str' is needed",
                    }
                )
                continue
            self.send_dict(self.execute_code(message.get("code")))


def worker_main(conn: Connection, config: dict, tools: list) -> None:
    worker = Worker(conn, config)
    worker.run()
