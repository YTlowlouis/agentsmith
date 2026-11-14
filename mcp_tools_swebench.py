import os
import fnmatch
import re
import ast
import subprocess
from sys import stderr

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
MAX_LEN_FOR_OLDSTR = 50
RUN_TESTS_TIMEOUT = 300
MAX_TESTS_OUTPUT = 4000

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

    final = grep_repo(regex, file_pattern)
    if not final:
        return f"No matches for pattern: '{pattern}' for files: {file_pattern}"
    if len(final) > MAX_MATCHES:
        old_len = len(final)
        final = final[:MAX_MATCHES]
        final.append(
            f"[Showing {len(final)} of {old_len}. Use a more specific pattern or file_pattern.]"
        )
    return "\n".join(final)


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """Replace an exact snippet of text in a file of the repository.

    Args:
        filepath: path to the file, either absolute under /testbed
            (e.g. /testbed/sympy/core/basic.py) or relative to the
            repository root (e.g. sympy/core/basic.py).
        old_str: text to replace. It must match the file exactly, including
            indentation and whitespace, and appear exactly once. If it
            appears several times, include surrounding lines to make it unique.
        new_str: replacement text.

    For .py files, an edit that would break Python syntax is rejected and the
    file is left unchanged. Returns a confirmation message on success.
    """
    if not old_str:
        raise ToolError("Empty snippet to be replaced")

    verified_path = translate_path(filepath)
    if not os.path.exists(verified_path):
        raise ToolError(f"{to_display_path(verified_path)} does not exist")
    if os.path.isdir(verified_path):
        raise ToolError(f"{to_display_path(verified_path)} is a directory")

    try:
        with open(verified_path, "r", encoding="utf-8") as f:
            content = f.read()
            count = content.count(old_str)
            new_old_str = old_str
            if len(old_str) > MAX_LEN_FOR_OLDSTR:
                new_old_str = old_str[:MAX_LEN_FOR_OLDSTR]
                new_old_str += "..."
        if count <= 0:
            raise ToolError(
                f"{new_old_str} not found in {to_display_path(verified_path)}"
            )
        if count > 1:
            raise ToolError(
                f"Multiple occurrence of {new_old_str} in {to_display_path(verified_path)}, try to make it more precise"
            )
        content = content.replace(old_str, new_str)
        if filepath.endswith(".py"):
            ast.parse(content)

        with open(verified_path, "w", encoding="utf-8") as f:
            f.write(content)

    except SyntaxError as e:
        raise ToolError(
            f"Invalid code after modification at line {e.lineno}: {e.msg}. Wrong code: {e.text}"
        )
    except PermissionError:
        raise ToolError(
            f"PermissionError when opening: {to_display_path(verified_path)}"
        )
    except FileNotFoundError:
        raise ToolError(f"File {to_display_path(verified_path)} not found")
    except IsADirectoryError:
        raise ToolError(
            f"File {to_display_path(verified_path)} is a directory"
        )
    except UnicodeDecodeError:
        raise ToolError(
            f"Unicode decode error opening {to_display_path(verified_path)}, file may not be a text file"
        )
    return f"Replacement in file {to_display_path(verified_path)} executed without any error"


@mcp.tool()
def get_patch() -> str:
    """Return the unified git diff of all changes made to the repository.

    Returns an empty string if nothing was modified. Submit your fix with
    final_answer(get_patch()).
    """
    try:
        gitc = subprocess.run(
            ["git", "-c", "core.fileMode=false", "diff"],
            timeout=5,
            text=True,
            capture_output=True,
            cwd=TESTBED_PATH,
        )

        if gitc.returncode != 0:
            raise ToolError(f"Error when showing diff: {gitc.stderr}")
    except subprocess.TimeoutExpired:
        raise ToolError(
            "Subprocess timed out on command: git -c core.fileMode=false diff"
        )
    except FileNotFoundError:
        raise ToolError("git is not installed or not found")
    return gitc.stdout


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find where a function, method or class is defined in the repository.

    Args:
        name: the bare name to look for (e.g. "_eval_inverse", "DenseMatrix").
            A dotted name like "DenseMatrix._eval_inverse" is reduced to its
            last part.

    Searches all .py files for lines starting with "def <name>",
    "async def <name>" or "class <name>". Returns one match per line in the
    format "/testbed/path/to/file.py:<line_number> <line_content>".
    Names defined by assignment (e.g. "Matrix = MutableDenseMatrix") are not
    found; use search_code for those. At most 100 results are returned.
    """
    if not name:
        raise ToolError("Empty name")
    if "." in name:
        name = name.rsplit(".", 1)[-1]

    escaped_name = re.escape(name)
    reg = re.compile(rf"^\s*(async\s+)?(def|class)\s+{escaped_name}\b")

    final = grep_repo(reg, "*.py")

    if not final:
        return f"No definition found for: {name}"

    if len(final) > MAX_MATCHES:
        old_len = len(final)
        final = final[:MAX_MATCHES]
        final.append(
            f"[Showing {MAX_MATCHES} of {old_len} definitions. "
            f"Use search_code with the class name to narrow it down.]"
        )
    return "\n".join(final)


def grep_repo(regex: re.Pattern, file_pattern: str) -> list[str]:
    """Search every line of the repository files matching file_pattern.

    Returns one formatted line per match:
    "/testbed/path/to/file.py:<line_number> <line_content>".
    Unreadable files are skipped; long lines are truncated.
    """
    matches = []
    for file in find_files(TESTBED_PATH, file_pattern):
        try:
            with open(file, "r", encoding="utf-8") as f:
                for x, line in enumerate(f, start=1):
                    if not regex.search(line):
                        continue
                    content = line.rstrip("\n")
                    if len(content) > MAX_LINE_FOR_MATCH:
                        content = (
                            content[:MAX_LINE_FOR_MATCH]
                            + f"... truncated {len(content) - MAX_LINE_FOR_MATCH} characters"
                        )
                    matches.append(f"{to_display_path(file)}:{x} {content}")
        except (
            PermissionError,
            FileNotFoundError,
            IsADirectoryError,
            UnicodeDecodeError,
        ):
            continue
    return matches


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    """Find all usages of a function, method or class name in the repository.

    Args:
        name: the bare name of the symbol (e.g. "inv"). A dotted name like
            "Matrix.inv" is reduced to its last part.
        filepath: file where the symbol appears, either absolute under
            /testbed or relative to the repository root.
        line: 1-based line number in filepath where the symbol appears
            (e.g. its definition). Used to check the symbol exists there.

    Searches all .py files for the name as a whole word. The search is by
    name only: other symbols with the same name, comments and strings are
    included too. Returns one match per line in the format
    "/testbed/path/to/file.py:<line_number> <line_content>".
    At most 100 results are returned.
    """
    if not name:
        raise ToolError("Empty name")
    if line <= 0:
        raise ToolError("Line number inferior to 1 does not exist")

    name = name.rsplit(".", 1)[-1]

    verified_path = translate_path(filepath)

    reg = re.compile(rf"\b{re.escape(name)}\b")

    if not os.path.exists(verified_path):
        raise ToolError(f"File: {filepath} does not exist")
    try:
        with open(verified_path, "r", encoding="utf-8") as f:
            text = f.readlines()
            if len(text) < line:
                raise ToolError(f"Line: {line} exceeds number of line in file")
            if not reg.search(text[line - 1]):
                raise ToolError(f"{name} does not appear at line {line}")

        final = grep_repo(reg, "*.py")

        if not final:
            raise ToolError(f"No match found for pattern: '{name}'")

        if len(final) > MAX_MATCHES:
            old_len = len(final)
            final = final[:MAX_MATCHES]
            final.append(
                f"[Showing {MAX_MATCHES} of {old_len} references. "
                f"Use search_code with a more specific pattern (e.g. '\\.{name}\\(' for calls) to narrow it down.]",
            )
        return "\n".join(final)

    except PermissionError:
        raise ToolError(
            f"PermissionError when opening: {to_display_path(verified_path)}"
        )
    except FileNotFoundError:
        raise ToolError(f"File {to_display_path(verified_path)} not found")
    except IsADirectoryError:
        raise ToolError(
            f"File {to_display_path(verified_path)} is a directory"
        )
    except UnicodeDecodeError:
        raise ToolError(
            f"Unicode decode error opening {to_display_path(verified_path)}, file may not be a text file"
        )


@mcp.tool()
def run_tests() -> str:
    """Run the official test script of the task and return its test output.

    Applies the task's test files, runs the relevant tests, then restores the
    test files: edits you make to those test files are overwritten. Only the
    test runner output is returned, truncated to its last part if long. Read
    the summary at the end (e.g. "tests finished: 24 passed, 1 failed").
    Takes up to a few minutes.
    """
    eval_path = os.environ.get("EVAL_SCRIPT_PATH", "/eval.sh")
    if not os.path.exists(eval_path):
        raise ToolError(f"File: {eval_path} does not exist")

    try:
        result = subprocess.run(
            ["bash", eval_path],
            cwd=TESTBED_PATH,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=RUN_TESTS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"Tests did not finish within {RUN_TESTS_TIMEOUT} seconds. "
            "The fix may cause an infinite loop or the test suite is too slow."
        )

    text = result.stdout.split("\n")

    start_index = -1
    end_index = -1

    for x, line in enumerate(text):
        if ">>>>> Start Test Output" in line and start_index < 0:
            start_index = x
        if ">>>>> End Test Output" in line and end_index < 0:
            end_index = x

    if start_index < 0 or end_index < 0:
        text.append(
            "Missing start or end marker, an error probably"
            " occurred that prevented tests from completing"
        )
        text = "\n".join(text)

    else:
        text = "\n".join(text[start_index + 1 : end_index])

    if len(text) > MAX_TESTS_OUTPUT:
        beggining = text[: int(MAX_TESTS_OUTPUT * 0.2)]
        beggining += f"\n[... {len(text) - MAX_TESTS_OUTPUT} characters truncated ...]\n"
        end = text[-int(MAX_TESTS_OUTPUT * 0.8) :]
        text = beggining + end

    return text


@mcp.tool()
def run_command(command, workdir) -> str:
    pass


if __name__ == "__main__":
    mcp.run()
