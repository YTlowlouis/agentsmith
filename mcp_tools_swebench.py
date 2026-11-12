import os
from os.path import realpath

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

CONTAINER_ROOT = "/testbed"

_raw_testbed = os.environ.get("TESTBED_PATH")
if not _raw_testbed:
    raise SystemExit("TESTBED_PATH environment variable is not set")
TESTBED_PATH = os.path.realpath(_raw_testbed)
MAX_LINES = 200

mcp = MCPServer("mcp_swebench")


def translate_path(path: str) -> str:
    """Map a path given by the LLM to a real path inside TESTBED_PATH.

    Accepts '/testbed/...', paths relative to the repository root, and absolute
    paths already inside TESTBED_PATH. Raises ToolError for anything else.
    """
    requested = path
    if path == CONTAINER_ROOT:
        path = TESTBED_PATH
    elif path.startswith(CONTAINER_ROOT + "/"):
        relative = path[len(CONTAINER_ROOT) + 1 :]
        path = os.path.join(TESTBED_PATH, relative)
    elif not os.path.isabs(path):
        path = os.path.join(TESTBED_PATH, path)

    real_path = os.path.realpath(path)
    if os.path.commonpath([real_path, TESTBED_PATH]) != TESTBED_PATH:
        raise ToolError(
            f"Access denied: {requested} is outside the repository ({CONTAINER_ROOT})"
        )
    return real_path


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read a range of lines from a file in the repository.

    Args:
        filepath: path to the file, either absolute under /testbed
            (e.g. /testbed/sympy/core/basic.py) or relative to the
            repository root (e.g. sympy/core/basic.py).
        start_line: first line to read, 1-based and inclusive.
        end_line: last line to read, 1-based and inclusive. May exceed the
            file length; reading stops at the last line.

    Returns the lines in the format "<line_number>: <line_content>", one per
    line. At most 200 lines are returned per call; if the range is larger,
    the output says which start_line to use to continue.
    """
    try:
        if start_line <= 0 or end_line <= 0:
            raise ToolError(
                f"Invalid start or end line: {start_line if start_line <= 0 else end_line}"
            )
        if end_line < start_line:
            raise ToolError(
                f"End line: {end_line} before start line: {start_line}"
            )

        with open(translate_path(filepath), "r", encoding="utf-8") as f:
            file = f.readlines()
            if len(file) == 0:
                raise ToolError("File is empty")
            if start_line > len(file):
                raise ToolError(
                    f"Start line: {start_line} begins after end of file which is {len(file)} lines long."
                )
            text = []
            count = 0
            for x, line in enumerate(file, start=1):
                if x > end_line:
                    break
                if x < start_line:
                    continue
                if count >= MAX_LINES:
                    text.append(
                        f"[Output limited to {MAX_LINES} lines. Call read_file again with start_line={start_line + MAX_LINES} to continue.]"
                    )
                    break
                content = line.rstrip("\n")
                text.append(f"{x}: {content}")
                count += 1

    except PermissionError:
        raise ToolError(f"PermissionError when opening: {filepath}")
    except FileNotFoundError:
        raise ToolError(f"File {filepath} not found")
    except IsADirectoryError:
        raise ToolError(f"File {filepath} is a directory")
    except UnicodeDecodeError:
        raise ToolError(
            f"Unicode decode error opening {filepath}, file may not be a text file"
        )
    return "\n".join(text)


if __name__ == "__main__":
    mcp.run()
