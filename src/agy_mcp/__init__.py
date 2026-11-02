"""agy-mcp: MCP server orchestrating autonomous Google Antigravity (agy) CLI jobs.

This package owns the local wrapper process: the MCP server, job state, worktree
lifecycle, supervision, security audit, commits, verification and usage
accounting. It must never import or start the real ``agy`` binary at import
time, and it must never merge or push a user's branches.
"""

__version__ = "0.1.0.dev0"
