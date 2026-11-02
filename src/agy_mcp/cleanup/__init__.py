"""Focused modules for the two standalone cleanup CLIs (SPEC §22, §23).

``store_io`` holds the shared store/record primitives, ``git_metadata`` the
strict git metadata readers, ``worktree_status`` the worktree-registration
decision, and ``jobs_cli`` / ``fixtures_cli`` the two command-line tools. The
old top-level module paths remain as thin re-export shims.
"""
