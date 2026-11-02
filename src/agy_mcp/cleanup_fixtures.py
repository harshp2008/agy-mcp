"""Compatibility shim: the fixture cleaner moved to :mod:`agy_mcp.cleanup.fixtures_cli`.

``python -m agy_mcp.cleanup_fixtures`` and the private helpers imported by the
frozen tests keep working from this path.
"""

from agy_mcp.cleanup.fixtures_cli import (  # noqa: F401
    _classify_job,
    _is_pytest_temp_repo,
    _pytest_basetemp,
    main,
    parse_args,
    run_cleanup,
)
from agy_mcp.cleanup.store_io import (  # noqa: F401
    _UUID_RE,
    MAX_JSON_DEPTH,
    _apply_edge_protection,
    _check_json_depth,
    _check_pgid_status,
    _check_pid_status,
    _extract_strings,
    _get_kernel_pid_max,
    _has_symlinks,
    _is_canonical_uuid,
    _parse_iso_ts,
    _raise_on_duplicate_keys,
    _safe_delete_job_dir,
    _safe_json_loads,
    _write_manifest,
)

if __name__ == "__main__":
    import sys

    sys.exit(main())
