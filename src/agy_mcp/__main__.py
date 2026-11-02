"""Console entrypoint for ``python -m agy_mcp``.

Owns nothing but the delegation to :func:`agy_mcp.server.main`; argument
parsing, configuration and process lifecycle all live in ``server.py``. It must
never start background work itself, so that ``python -m agy_mcp --help`` stays
side-effect free.
"""

import sys

from agy_mcp.server import main

if __name__ == "__main__":
    main(sys.argv[1:])
