"""
Shared MCP utilities for Couchbase MCP server init and validation
"""

import os
from agents.mcp import MCPServerStdio


def validate_environment(mcp_path: str) -> bool:
    """Validate a local checkout path for the Couchbase MCP server (only needed when running from source)."""
    if not mcp_path:
        # No source path configured - the MCP server will be run via `uvx couchbase-mcp-server` instead.
        return True
    if not os.path.exists(mcp_path):
        print(f" Error: Couchbase MCP server path not found: {mcp_path}")
        print("Please update the mcp_source_path in config.py, or switch MCP_CONFIG to run via `uvx couchbase-mcp-server` instead.")
        return False
    return True


async def initialize_mcp_server(command: str, mcp_path: str, script_path: str, env_config: dict):
    print(f" Connecting to Couchbase MCP server via: {command}")

    server = MCPServerStdio(
        params={
            "command": command,
            "args": ["--directory", mcp_path, "run", script_path] if mcp_path else ["couchbase-mcp-server"],
            "env": env_config,
        }
    )
    await server.connect()
    return server