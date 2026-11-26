import asyncio
import threading
import shlex
import os
from mcp import ClientSession, StdioServerParameters, MCPError
from mcp.client.stdio import stdio_client
import concurrent.futures


class MCPBridgeError(Exception):
    pass


class MCPBridge:
    READY_TIMEOUT = 5
    TOOL_TIMEOUT = 600

    def __init__(self, url: str = "", command: str = "") -> None:
        if url and command:
            raise MCPBridgeError("Url and command set, only one should be set")
        if not url and not command:
            raise MCPBridgeError(
                "No url and no command set, one should be set"
            )
        self.url = url
        self.command = command
        self.session = None

        self.event_loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

        self.tools: list[dict] = []
        self.prompts: list = []
        self.resources: list = []

        self.ready: threading.Event = threading.Event()
        self.stop: asyncio.Event | None = None
        self.starting_error: Exception | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return

    def _server_label(self) -> str:
        if self.command:
            return f"command: {self.command}"
        return f"url: {self.url}"

    def start(self):
        self.event_loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

        if not self.ready.wait(self.READY_TIMEOUT):
            raise MCPBridgeError(
                f"MCP server did not respond within {self.READY_TIMEOUT} seconds "
                f"({self._server_label()})"
            )
        if self.starting_error is not None:
            error = self.starting_error
            message = (
                f"Could not connect to MCP server ({self._server_label()}): "
                f"{type(error).__name__}: {error}"
            )
            if isinstance(error, MCPError):
                message += (
                    ". The server process exited during startup; "
                    "see its error output above."
                )
            raise MCPBridgeError(message) from error

    def _run_loop(self):
        if not self.event_loop:
            raise MCPBridgeError("Event loop not defined, run start()")
        asyncio.set_event_loop(self.event_loop)
        self.event_loop.run_until_complete(self._main())

    def call_tool(self, name: str, args: dict | None = None):
        if (
            not self.thread
            or not self.thread.is_alive()
            or not self.event_loop
            or not self.session
        ):
            raise MCPBridgeError("MCP bridge is not connected")
        if not isinstance(name, str) or not name:
            raise MCPBridgeError("Name is empty")
        if not args:
            args = {}

        future = asyncio.run_coroutine_threadsafe(
            self.session.call_tool(name, args), self.event_loop
        )

        try:
            results = future.result(self.TOOL_TIMEOUT)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPBridgeError(
                f"Tool call {name} timed out after {self.TOOL_TIMEOUT} seconds"
            )
        except Exception as e:
            raise MCPBridgeError(
                f"Tool call {name} failed: {type(e).__name__}: {e}"
            ) from e

        text = "\n".join(
            [text.text for text in results.content if text.type == "text"]
        )

        return {"text": text, "is_error": bool(results.is_error)}

    def list_tools(self):
        return self.tools

    def read_resource(self, uri: str):
        if (
            not self.thread
            or not self.thread.is_alive()
            or not self.event_loop
            or not self.session
        ):
            raise MCPBridgeError("MCP bridge is not connected")
        if not isinstance(uri, str) or not uri:
            raise MCPBridgeError("Uri is empty")

        future = asyncio.run_coroutine_threadsafe(
            self.session.read_resource(uri), self.event_loop
        )
        try:
            result = future.result(self.READY_TIMEOUT)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPBridgeError(f"Read resource {uri} timed out")
        except Exception as e:
            raise MCPBridgeError(
                f"Read resource {uri} failed: {type(e).__name__}: {e}"
            ) from e

        text = "\n".join(
            c.text for c in result.contents if hasattr(c, "text")
        )
        return {"uri": uri, "text": text}

    def get_prompt(self, name: str, args: dict | None = None):
        if (
            not self.thread
            or not self.thread.is_alive()
            or not self.event_loop
            or not self.session
        ):
            raise MCPBridgeError("MCP bridge is not connected")
        if not isinstance(name, str) or not name:
            raise MCPBridgeError("Name is empty")
        args = {k: str(v) for k, v in (args or {}).items()}

        future = asyncio.run_coroutine_threadsafe(
            self.session.get_prompt(name, args), self.event_loop
        )
        try:
            result = future.result(self.READY_TIMEOUT)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPBridgeError(f"Get prompt {name} timed out")
        except Exception as e:
            raise MCPBridgeError(
                f"Get prompt {name} failed: {type(e).__name__}: {e}"
            ) from e

        return {
            "description": result.description,
            "messages": [
                {"role": m.role, "text": m.content.text}
                for m in result.messages
                if m.content.type == "text"
            ],
        }

    def close(self):
        if not self.thread or not self.event_loop:
            return

        try:
            if self.stop:
                self.event_loop.call_soon_threadsafe(self.stop.set)
        except RuntimeError:
            pass

        self.thread.join(2)
        if self.thread.is_alive():
            return
        self.event_loop.close()

        self.thread = None
        self.event_loop = None
        self.starting_error = None
        self.ready.clear()
        self.stop = None

    async def _main(self):
        self.stop = asyncio.Event()
        try:
            command_list = shlex.split(self.command)
            if not command_list:
                raise MCPBridgeError("command is empty")
            command = command_list[0]
            args = command_list[1:]
            env = dict(os.environ)

            server_params = StdioServerParameters(
                command=command, args=args, env=env
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    self.tools = [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "input_schema": tool.input_schema,
                        }
                        for tool in response.tools
                    ]
                    try:
                        resources_response = await session.list_resources()
                        self.resources = [
                            {
                                "uri": str(resource.uri),
                                "name": resource.name,
                                "description": resource.description,
                            }
                            for resource in resources_response.resources
                        ]
                    except Exception:
                        pass
                    try:
                        prompts_response = await session.list_prompts()
                        self.prompts = [
                            {
                                "name": prompt.name,
                                "description": prompt.description,
                                "arguments": [
                                    {
                                        "name": argument.name,
                                        "description": argument.description,
                                        "required": bool(argument.required),
                                    }
                                    for argument in prompt.arguments or []
                                ],
                            }
                            for prompt in prompts_response.prompts
                        ]
                    except Exception:
                        pass

                    self.session = session
                    self.ready.set()

                    await self.stop.wait()

        except Exception as e:
            error = e
            while hasattr(error, "exceptions"):
                error = error.exceptions[0]
            self.starting_error = error
            self.ready.set()

        finally:
            self.session = None
