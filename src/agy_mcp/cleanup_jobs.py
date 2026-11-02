"""Compatibility shim: the job cleaner moved to :mod:`agy_mcp.cleanup.jobs_cli`.

``python -m agy_mcp.cleanup_jobs``, the ``run_cleanup`` / ``main`` API and every
private name the tests and scripts import keep working from this path.
"""

from agy_mcp.cleanup.git_metadata import (  # noqa: F401
    _CONFIG_SECTION_RE,
    _CONFIG_VAR_RE,
    _GIT_DIR_MARKERS,
    _HEX_SHA_RE,
    MAX_GIT_METADATA_SIZE,
    _has_git_dir_markers,
    _read_git_metadata_file,
    _validate_full_git_dir,
    _validate_git_config_file,
    _validate_git_dir,
    _validate_git_head_file,
    _validate_git_packed_refs_file,
)
from agy_mcp.cleanup.jobs_cli import (  # noqa: F401
    _classify_job_for_cleanup,
    _collect_kept_strings,
    _list_projects,
    main,
    parse_args,
    run_cleanup,
)
from agy_mcp.cleanup.store_io import (  # noqa: F401
    MAX_JOB_JSON_BYTES,
    JobJsonFailure,
    JobJsonProblem,
    load_job_json,
    normalize_repo_path,
)
from agy_mcp.cleanup.worktree_status import _check_worktree_status  # noqa: F401

if __name__ == "__main__":
    import sys

    sys.exit(main())
