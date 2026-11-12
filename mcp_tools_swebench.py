import os
import fnmatch
import re

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

CONTAINER_ROOT = "/testbed"

_raw_testbed = os.environ.get("TESTBED_PATH")
if not _raw_testbed:
    raise SystemExit("TESTBED_PATH environment variable is not set")
TESTBED_PATH = os.path.realpath(_raw_testbed)
MAX_LINES = 200
EXCLUDED_DIRS = {".git", "__pycache__", ".tox", ".pytest_cache"}
MAX_FILES = 200
MAX_MATCHES = 200
MAX_LINE_FOR_MATCH = 200

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


def to_display_path(path: str) -> str:
    if path == TESTBED_PATH:
        return CONTAINER_ROOT
    if path.startswith(TESTBED_PATH + "/"):
        len_testbed = len(TESTBED_PATH)
        return CONTAINER_ROOT + path[len_testbed:]
    raise ValueError(f"Path: {path} not in testbed")


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


def find_files(root: str, pattern: str) -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for file in fnmatch.filter(filenames, pattern):
            files.append(os.path.join(dirpath, file))
    return sorted(files)


@mcp.tool()
def list_files(directory: str, pattern: str = "*") -> str:
    """List files recursively under a directory of the repository.

    Args:
        directory: directory to search, either absolute under /testbed
            (e.g. /testbed/sympy/matrices) or relative to the repository
            root (e.g. sympy/matrices).
        pattern: shell-style pattern matched against file names only
            (e.g. "*.py", "test_*.py"). Defaults to "*" (all files).

    Returns one absolute /testbed path per line, sorted alphabetically.
    Subdirectories are searched; .git and __pycache__ are skipped.
    At most 200 files are returned per call.
    """
    validated_dir = translate_path(directory)
    if not os.path.exists(validated_dir):
        raise ToolError(f"Path: {directory} does not exist")
    if not os.path.isdir(validated_dir):
        raise ToolError(f"{directory} is not a directory")

    files = find_files(validated_dir, pattern)
    files = [to_display_path(file) for file in files]
    if len(files) > MAX_FILES:
        old_len = len(files)
        files = files[:MAX_FILES]
        files.append(
            f"[Showing {MAX_FILES} of {old_len} files. Use a more specific directory or pattern.]"
        )
    if len(files) == 0:
        return f"No files matching '{pattern}' in {directory}"
    return "\n".join(files)


@mcp.tool()
def search_code(pattern: str, file_pattern: str = "*.py") -> str:
    """Search the whole repository for lines matching a regular expression.

    Args:
        pattern: Python regular expression searched in each line of file
            content (e.g. "def _eval_inverse", r"class \w+Matrix").
            Escape special characters to search literally (e.g. "foo\\(").
        file_pattern: shell-style pattern on file names selecting which files
            to search (e.g. "*.py", "test_*.py"). Defaults to "*.py".

    Returns one match per line in the format
    "/testbed/path/to/file.py:<line_number> <line_content>".
    Long lines are truncated. At most 100 matches are returned per call.
    """
    final = []
    try:
        regex = re.compile(pattern)
    except re.error:
        raise ToolError(
            f"Invalid pattern: '{pattern}', escape special characters"
        )
    files = find_files(TESTBED_PATH, file_pattern)

    for file in files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                for x, line in enumerate(f, start=1):
                    if regex.search(line):
                        newline = line.rstrip("\n")
                        if len(newline) > MAX_LINE_FOR_MATCH:
                            newline = (
                                newline[:MAX_LINE_FOR_MATCH]
                                + f"... truncated {len(newline) - MAX_LINE_FOR_MATCH} characters"
                            )
                            final.append(
                                f"{to_display_path(file)}:{x} {newline}"
                            )
                        else:
                            final.append(
                                f"{to_display_path(file)}:{x} {newline}"
                            )

        except (
            PermissionError,
            FileNotFoundError,
            IsADirectoryError,
            UnicodeDecodeError,
        ):
            continue
    if not final:
        return f"No matches for pattern: '{pattern}' for files: {file_pattern}"
    if len(final) > MAX_MATCHES:
        old_len = len(final)
        final = final[:MAX_MATCHES]
        final.append(
            f"[Showing {len(final)} of {old_len}. Use a more specific pattern or file_pattern.]"
        )
    return "\n".join(final)


if __name__ == "__main__":
    mcp.run()
