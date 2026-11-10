import anyio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server_params = StdioServerParameters(
        command="uv", args=["run", "../mcp_tools_mbpp.py"], env=None
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            pass


if __name__ == "__main__":
    anyio.run(main)
