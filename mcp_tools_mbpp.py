from re import sub
from mcp.server import MCPServer
import json
import subprocess
import tempfile
import os
import sys

mcp = MCPServer("Test")


@mcp.tool()
def run_tests(code: str, test_list: list[str]) -> str:
    """run "test_list" tests using "code" code"""

    grouped_code = code + "\n"
    for test in test_list:
        grouped_code += test + "\n"

    with tempfile.TemporaryDirectory() as smith_temp:
        tmpfilepath = os.path.join(smith_temp, "solution.py")
        with open(tmpfilepath, "w") as f:
            f.write(grouped_code)
        try:
            output = subprocess.run(
                [sys.executable, f"{tmpfilepath}"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=smith_temp,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "success": False,
                    "output": "executable code timeout after 5 seconds, infinite loop or code overly slow",
                }
            )
        output_text = output.stdout
        output_text += output.stderr
        output_code = output.returncode == 0
    return json.dumps({"success": output_code, "output": output_text})


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
