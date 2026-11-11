from mcp.server import MCPServer
import json
import subprocess
import tempfile
import os
import sys
import secrets

mcp = MCPServer("mcp_mbpp")

CHAR_LIMIT_STDOUT = 500
CHAR_LIMIT_STDERR = 1500
TIMEOUT = 5


@mcp.tool()
def run_tests(code: str, test_list: list[str]) -> str:
    """Run Python code against a list of assert statements.

    Args:
    code: the full solution source (function definitions and needed imports).
    test_list: assert statements, executed in order after the code.

    Returns a JSON string: {“success”: bool, “output”: str}. success is true only if every assert passed. output holds stdout/stderr (truncated if long). Execution is killed after 5 seconds."""

    random_code = secrets.token_hex(16)
    grouped_code = code + "\n"
    for test in test_list:
        grouped_code += test + "\n"
    grouped_code += f"\nprint('{random_code}', end='')"

    with tempfile.TemporaryDirectory() as smith_temp:
        tmpfilepath = os.path.join(smith_temp, "solution.py")
        with open(tmpfilepath, "w") as f:
            f.write(grouped_code)
        try:
            output = subprocess.run(
                [sys.executable, f"{tmpfilepath}"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=smith_temp,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "success": False,
                    "output": f"\nexecutable code timeout after {TIMEOUT} seconds, infinite loop or code overly slow",
                }
            )
        output_text = output.stdout
        output_error = output.stderr
        output_text = output_text.replace(random_code, "")

        output_text = truncate_output(output_text, output_error)

        output_code = output.returncode == 0 and output.stdout.endswith(
            random_code
        )
        if output.returncode == 0 and not output.stdout.endswith(random_code):
            output_text += "\nThe program exited (code 0) before all tests ran. Remove any exit()/sys.exit()/quit() call from your code."

    return json.dumps({"success": output_code, "output": output_text})


def truncate_output(stdout: str, stderr: str) -> str:
    if len(stdout) > CHAR_LIMIT_STDOUT:
        half = int(CHAR_LIMIT_STDOUT * 0.5)
        stdout = (
            stdout[:half]
            + f"\n[{len(stdout) - half * 2} characters truncated]\n"
            + stdout[-half:]
        )
    if len(stderr) > CHAR_LIMIT_STDERR:
        stderr = (
            stderr[: int(CHAR_LIMIT_STDERR * 0.25)]
            + f"\n[{len(stderr) - CHAR_LIMIT_STDERR} characters truncated]\n"
            + stderr[-int(CHAR_LIMIT_STDERR * 0.75) :]
        )
    return stdout + stderr


if __name__ == "__main__":
    mcp.run()


"""
    {
  "task_id": 104,
  "task_definition": "Write a function to sort each sublist of strings in a given list of lists using lambda function.",
  "function_definition": "def sort_sublists(input_list):",
  "test_imports": [],
  "test_list": [
    "assert sort_sublists(([\" red \",\"green\" ],[\"blue \",\" black\"],[\" orange\",\"brown\"]))==[[' red ', 'green'], [' black', 'blue '], [' orange', 'brown']]",
    "assert sort_sublists(([\"zilver\",\"gold\"], [\"magnesium\",\"aluminium\"], [\"steel\", \"bronze\"]))==[['gold', 'zilver'],['aluminium', 'magnesium'], ['bronze', 'steel']]"
  ]
}%
"""
