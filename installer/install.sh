#!/usr/bin/env bash
# agy-mcp installer chapter.
#
# Standalone use:
#   ./installer/install.sh [--client dsh|generic] [--dry-run] [--doctor]
#                          [--uninstall] [--help]
#
#   The default client is "generic": it writes only under
#   ${CONF_DIR:-$HOME/.config/ai-stack}/agymcp, the agy-mcp config path and
#   ~/.local/state/agy-mcp, and prints a standard MCP "mcpServers" JSON snippet
#   plus orchestrator guidance for any MCP client.  "--client dsh" additionally
#   registers the server in ~/.dsh/profiles/web/cordis.patch.yml and appends the
#   guidance block to ~/.dsh/AGENTS.md.
#
# Sourceable use:
#   source installer/install.sh
#   ch_agymcp            # core install + generic files + dsh registration
#   doctor_agymcp dsh    # read-only health checks
#   uninstall_agymcp dsh # remove only this chapter's files and marker blocks
#
# Environment equivalents:
#   AGY_MCP_CLIENT=dsh|generic   AGY_MCP_DRY_RUN=1
#   AGY_MCP_SRC=/path/to/repo   AGY_MCP_EDITABLE=1
#   AGY_MCP_PYTHON=python3      AGY_MCP_PIP_ARGS="--no-deps --no-index"
#   AGY_MCP_NO_RESTART=1        AGY_MCP_AGY_BINARY=/path/to/agy
#   AGY_MCP_CONFIG_PATH=...     AGY_MCP_STATE_DIR=...
#
# How this will be hosted later (comments only; no network fetching here):
#   * curl | bash from a raw URL: publish installer/install.sh at a stable raw
#     URL and document `curl -fsSL <url> | bash -s -- --client generic`.  The
#     script must stay POSIX-bash compatible and must not assume the repo is
#     checked out.
#   * Future `agy-mcp install` subcommand: the same functions move behind
#     `agy_mcp.installer` or a console-script subcommand, so a pip-installed
#     agy-mcp can install itself without a checkout.
#   * Fetch-latest via a git cache: clone or `git -C <cache> pull --ff-only` the
#     repository into a per-user cache directory, then run this file from that
#     checkout.  Deliberately not implemented now: network access, authentication
#     prompts and proxy handling need their own design and tests.
#
# The file never restarts anything when sourced, and dsh registration refuses to
# run from inside a dsh session so an installer invocation cannot restart the
# host that is executing it.

AGY_MCP_SCRIPT_PATH="${BASH_SOURCE[0]}"
AGY_MCP_SCRIPT_DIR="$(cd -- "$(dirname -- "$AGY_MCP_SCRIPT_PATH")" >/dev/null 2>&1 && pwd)"
AGY_MCP_REPO_DEFAULT="$(cd -- "$AGY_MCP_SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
AGY_MCP_PATCH_BEGIN='# >>> ai-stack agymcp >>>'
AGY_MCP_PATCH_END='# <<< ai-stack agymcp <<<'
AGY_MCP_AGENTS_BEGIN='<!-- >>> ai-stack agymcp >>> -->'
AGY_MCP_AGENTS_END='<!-- <<< ai-stack agymcp <<< -->'

# ---------------------------------------------------------------------------
# Compatibility shim: when this file is run on its own it must supply the
# helpers the owner's install-ai-stack.sh normally provides.  When it is sourced
# into that installer, `log` already exists and the shim defines nothing.
# ---------------------------------------------------------------------------
if ! declare -F log >/dev/null 2>&1; then
    log() { printf 'install: %s\n' "$*"; }
    ok() { printf '  ok: %s\n' "$*"; }
    good() { printf '  good: %s\n' "$*"; }
    note() { printf '  - %s\n' "$*"; }
    warn() { printf 'install: warning: %s\n' "$*" >&2; }
    die() {
        printf 'install: error: %s\n' "$*" >&2
        exit 1
    }
    CONF_DIR="${CONF_DIR:-$HOME/.config/ai-stack}"
    refuse_inside_dsh() {
        if [[ "${AGY_MCP_ALLOW_INSIDE_DSH:-0}" == "1" ]]; then
            return 0
        fi
        if [[ -n "${DSH_SESSION_ID:-}" || -n "${DSH_HOME:-}" || -n "${DSH_PROFILE:-}" ]]; then
            die "refusing to change dsh registration from inside dsh (DSH_SESSION_ID/DSH_HOME/DSH_PROFILE is set); run this in a normal terminal. Set AGY_MCP_ALLOW_INSIDE_DSH=1 only when dsh will not be restarted."
        fi
    }
    secret_get() {
        # The host installer uses this to read a named secret; agy-mcp needs none.
        local name="${1:-}"
        if [[ -n "$name" ]]; then
            printf '%s' "${!name:-}"
        fi
    }
fi

# ---------------------------------------------------------------------------
# Paths (computed at call time so a sourced chapter sees the host's CONF_DIR).
# ---------------------------------------------------------------------------
agy_mcp_client() { printf '%s\n' "${AGY_MCP_CLIENT:-generic}"; }
agy_mcp_dry_run() { [[ "${AGY_MCP_DRY_RUN:-0}" == "1" ]]; }
agy_mcp_upgrade() { [[ "${AGY_MCP_UPGRADE:-0}" == "1" ]]; }

# True when the venv's agy_mcp was installed in editable mode. PEP 660 editable
# installs leave a PEP 610 direct_url.json with "editable": true inside the
# dist-info; older setup.py develop installs leave an .egg-link or an
# __editable__*.pth shim. A plain wheel copy has none of these, which is how
# --upgrade can tell the two apart (C1).
agy_mcp_is_editable_install() {
    local venv="$1"
    local marker=""
    for marker in "$venv"/lib/python*/site-packages/agy_mcp-*.dist-info/direct_url.json \
                  "$venv"/lib/python*/site-packages/agy-mcp-*.dist-info/direct_url.json; do
        [[ -f "$marker" ]] || continue
        if grep -Eq '"editable"[[:space:]]*:[[:space:]]*true' "$marker"; then
            return 0
        fi
    done
    if compgen -G "$venv/lib/python*/site-packages/agy_mcp.egg-link" >/dev/null; then
        return 0
    fi
    if compgen -G "$venv/lib/python*/site-packages/__editable__*agy_mcp*.pth" >/dev/null; then
        return 0
    fi
    return 1
}
agy_mcp_conf_dir() { printf '%s\n' "${CONF_DIR:-$HOME/.config/ai-stack}"; }
agy_mcp_dir() { printf '%s\n' "$(agy_mcp_conf_dir)/agymcp"; }
agy_mcp_run_sh() { printf '%s\n' "$(agy_mcp_dir)/run.sh"; }
agy_mcp_env_file() { printf '%s\n' "$(agy_mcp_dir)/env"; }
agy_mcp_json_file() { printf '%s\n' "$(agy_mcp_dir)/mcp-client-config.json"; }
agy_mcp_guidance_file() { printf '%s\n' "$(agy_mcp_dir)/orchestrator-guidance.md"; }
agy_mcp_config_path() {
    if [[ -n "${AGY_MCP_CONFIG_PATH:-}" ]]; then
        printf '%s\n' "$AGY_MCP_CONFIG_PATH"
        return 0
    fi
    local base="${XDG_CONFIG_HOME:-$HOME/.config}"
    printf '%s\n' "$base/agy-mcp/config.toml"
}
agy_mcp_state_dir() {
    if [[ -n "${AGY_MCP_STATE_DIR:-}" ]]; then
        printf '%s\n' "$AGY_MCP_STATE_DIR"
        return 0
    fi
    local base="${XDG_STATE_HOME:-$HOME/.local/state}"
    printf '%s\n' "$base/agy-mcp"
}
agy_mcp_dsh_patch_path() {
    printf '%s\n' "${DSH_PROFILE_DIR:-$HOME/.dsh/profiles/web}/cordis.patch.yml"
}
agy_mcp_dsh_agents_path() {
    printf '%s\n' "${DSH_AGENTS_PATH:-$HOME/.dsh/AGENTS.md}"
}

# ---------------------------------------------------------------------------
# Small output helpers shared by every layer.
# ---------------------------------------------------------------------------
agy_mcp_run() {
    if agy_mcp_dry_run; then
        printf '[dry-run] would run:'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

agy_mcp_mkdir() {
    local path="$1"
    local mode="${2:-700}"
    if agy_mcp_dry_run; then
        note "would create directory $path (mode $mode)"
        return 0
    fi
    mkdir -p -- "$path" || die "cannot create directory: $path"
    chmod "$mode" "$path" 2>/dev/null || true
}

agy_mcp_write_file() {
    local path="$1"
    local mode="$2"
    local tmp
    if agy_mcp_dry_run; then
        note "would write $path (mode $mode)"
        cat >/dev/null 2>&1 || true
        return 0
    fi
    mkdir -p -- "$(dirname -- "$path")" || die "cannot create directory for $path"
    tmp="$(mktemp "${path}.tmp.XXXXXX")" || die "cannot create temporary file for $path"
    if ! cat > "$tmp"; then
        rm -f -- "$tmp"
        die "cannot write $path"
    fi
    chmod "$mode" "$tmp" || {
        rm -f -- "$tmp"
        die "cannot chmod $path"
    }
    mv -f -- "$tmp" "$path" || {
        rm -f -- "$tmp"
        die "cannot replace $path"
    }
}

agy_mcp_json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

agy_mcp_toml_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# ---------------------------------------------------------------------------
# Layer A: prerequisites, venv, package, config, state directory, run files.
# ---------------------------------------------------------------------------
agy_mcp_find_agy() {
    local candidate=""
    if [[ -n "${AGY_MCP_AGY_BINARY:-}" ]]; then
        if [[ -x "$AGY_MCP_AGY_BINARY" ]]; then
            printf '%s\n' "$AGY_MCP_AGY_BINARY"
            return 0
        fi
        return 1
    fi
    candidate="$(command -v agy 2>/dev/null || true)"
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    if [[ -x "$HOME/.local/bin/agy" ]]; then
        printf '%s\n' "$HOME/.local/bin/agy"
        return 0
    fi
    return 1
}

agy_mcp_check_python() {
    local python_bin="${AGY_MCP_PYTHON:-python3}"
    if ! command -v "$python_bin" >/dev/null 2>&1; then
        die "Python 3.10 or newer is required, but '$python_bin' was not found. Set AGY_MCP_PYTHON=/path/to/python3."
    fi
    if ! env -u PYTHONPATH "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        die "Python 3.10 or newer is required; '$python_bin' reported: $("$python_bin" --version 2>&1)"
    fi
}

agy_mcp_check_prerequisites() {
    local agy_bin=""
    agy_bin="$(agy_mcp_find_agy)" || die "agy is not installed. Put the agy CLI on PATH or at ~/.local/bin/agy (or set AGY_MCP_AGY_BINARY), then rerun."
    AGY_MCP_RESOLVED_AGY="$agy_bin"
    if ! command -v git >/dev/null 2>&1; then
        die "git is not installed; agy-mcp needs git for worktrees and commits."
    fi
    agy_mcp_check_python
    note "agy found: $AGY_MCP_RESOLVED_AGY"
}

agy_mcp_entrypoint_mode() {
    local venv="$1"
    local src="$2"
    if [[ -x "$venv/bin/python" ]] && ! agy_mcp_dry_run; then
        if env -u PYTHONPATH "$venv/bin/python" -c 'import pathlib, sys, agy_mcp; raise SystemExit(0 if pathlib.Path(agy_mcp.__file__).resolve().parent.joinpath("__main__.py").is_file() else 1)' >/dev/null 2>&1; then
            printf 'python_module\n'
            return 0
        fi
        if [[ -x "$venv/bin/agy-mcp" ]]; then
            printf 'console\n'
            return 0
        fi
    fi
    # Dry-run fallback: inspect the source tree before the venv exists.
    if [[ -f "$src/src/agy_mcp/__main__.py" || -f "$src/agy_mcp/__main__.py" ]]; then
        printf 'python_module\n'
        return 0
    fi
    if [[ -f "$src/pyproject.toml" ]] && grep -Eq '^[[:space:]]*agy-mcp[[:space:]]*=' "$src/pyproject.toml"; then
        printf 'console\n'
        return 0
    fi
    # The real package ships __main__.py; default to it for unknown sources.
    printf 'python_module\n'
}

agy_mcp_write_config() {
    local config="$1"
    local home_toml=""
    home_toml="$(agy_mcp_toml_escape "$HOME")"
    if [[ -f "$config" ]]; then
        note "config already exists; not overwriting: $config"
        note "suggested additions (apply manually only if the key is missing):"
        if ! grep -Eq '^[[:space:]]*usage_guard[[:space:]]*=' "$config"; then
            note '+ usage_guard = "warn"'
        fi
        if ! grep -Eq '^[[:space:]]*plan_nudge[[:space:]]*=' "$config"; then
            note '+ plan_nudge = 1'
        fi
        if ! grep -Eq '^[[:space:]]*allowed_workdir_roots[[:space:]]*=' "$config"; then
            note "+ allowed_workdir_roots = [\"$home_toml\"]"
        fi
        return 0
    fi
    agy_mcp_write_file "$config" 600 <<EOF
# agy-mcp configuration written by the installer.
# Safe first-install defaults; the rest of the keys use code defaults.
usage_guard = "warn"
plan_nudge = 1
allowed_workdir_roots = ["$home_toml"]
EOF
}

agy_mcp_write_core_files() {
    local src="$1"
    local dir=""
    local venv=""
    local config=""
    local state=""
    local env_file=""
    local run_file=""
    local mode=""
    dir="$(agy_mcp_dir)"
    venv="$dir/venv"
    config="$(agy_mcp_config_path)"
    state="$(agy_mcp_state_dir)"
    env_file="$(agy_mcp_env_file)"
    run_file="$(agy_mcp_run_sh)"

    agy_mcp_write_config "$config"
    agy_mcp_mkdir "$state" 700

    {
        printf '# Generated by the agy-mcp installer; mode 600. Sourced by run.sh.\n'
        printf 'AGY_MCP_AGY_BINARY=%q\n' "$AGY_MCP_RESOLVED_AGY"
        printf 'AGY_MCP_CONFIG=%q\n' "$config"
        printf 'AGY_MCP_STATE_DIR=%q\n' "$state"
    } | agy_mcp_write_file "$env_file" 600

    mode="$(agy_mcp_entrypoint_mode "$venv" "$src")"
    {
        printf '#!/usr/bin/env bash\n'
        printf 'set -euo pipefail\n'
        printf 'HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\n'
        printf '# shellcheck source=/dev/null\n'
        printf 'source "$HERE/env"\n'
        if [[ "$mode" == "console" ]]; then
            printf 'exec "$HERE/venv/bin/agy-mcp" --agy-binary "$AGY_MCP_AGY_BINARY" --config "$AGY_MCP_CONFIG" --state-dir "$AGY_MCP_STATE_DIR"\n'
        else
            printf 'exec "$HERE/venv/bin/python" -m agy_mcp --agy-binary "$AGY_MCP_AGY_BINARY" --config "$AGY_MCP_CONFIG" --state-dir "$AGY_MCP_STATE_DIR"\n'
        fi
    } | agy_mcp_write_file "$run_file" 755
}

agy_mcp_install_core() {
    log "agy-mcp core install"
    agy_mcp_check_prerequisites || return 1

    local src="${AGY_MCP_SRC:-$AGY_MCP_REPO_DEFAULT}"
    if [[ ! -d "$src" && ! -f "$src" ]]; then
        die "AGY_MCP_SRC does not exist: $src"
    fi
    local dir=""
    local venv=""
    local python_bin="${AGY_MCP_PYTHON:-python3}"
    local -a editable=()
    local -a extra=()
    dir="$(agy_mcp_dir)"
    venv="$dir/venv"

    agy_mcp_mkdir "$dir" 700
    if [[ -x "$venv/bin/python" ]]; then
        note "venv already exists: $venv"
    else
        agy_mcp_run env -u PYTHONPATH "$python_bin" -m venv "$venv" || die "failed to create venv at $venv"
    fi

    if [[ -n "${AGY_MCP_PIP_ARGS:-}" ]]; then
        read -r -a extra <<< "$AGY_MCP_PIP_ARGS"
    fi

    local editable_install=0
    if agy_mcp_is_editable_install "$venv"; then
        editable_install=1
    fi

    if agy_mcp_upgrade; then
        if [[ "${AGY_MCP_EDITABLE:-0}" == "1" ]]; then
            editable=(-e)
        fi
        if [[ "$editable_install" == "1" && "${AGY_MCP_EDITABLE:-0}" != "1" ]]; then
            # An editable install already executes the source tree: reinstalling
            # would replace the link with a normal copy and silently stop live
            # edits from taking effect (C1).
            note "editable install is already live; nothing to reinstall"
        elif [[ "${AGY_MCP_EDITABLE:-0}" == "1" ]]; then
            note "reinstalling agy-mcp from $src (--upgrade)"
            agy_mcp_run env -u PYTHONPATH "$venv/bin/python" -m pip install --disable-pip-version-check --upgrade "${editable[@]}" "${extra[@]}" "$src" || die "pip upgrade of '$src' failed"
        else
            # Dependencies are deliberately re-resolved: a new version can add
            # or raise a requirement, and --no-deps would leave the venv stale.
            note "reinstalling agy-mcp from $src (--upgrade)"
            agy_mcp_run env -u PYTHONPATH "$venv/bin/python" -m pip install --disable-pip-version-check --upgrade --force-reinstall "${extra[@]}" "$src" || die "pip upgrade of '$src' failed"
        fi
    elif [[ -x "$venv/bin/python" ]] && ! agy_mcp_dry_run && env -u PYTHONPATH "$venv/bin/python" -c 'import agy_mcp' >/dev/null 2>&1; then
        note "agy-mcp already installed; rerun with --upgrade to reinstall from $src"
    else
        if [[ "${AGY_MCP_EDITABLE:-0}" == "1" ]]; then
            editable=(-e)
        fi
        agy_mcp_run env -u PYTHONPATH "$venv/bin/python" -m pip install --disable-pip-version-check "${editable[@]}" "${extra[@]}" "$src" || die "pip install of '$src' failed"
    fi

    agy_mcp_write_core_files "$src"
    note "run.sh: $(agy_mcp_run_sh)"
    note "env: $(agy_mcp_env_file) (mode 600)"
    note "config: $(agy_mcp_config_path)"
    note "state: $(agy_mcp_state_dir)"
    note "No agy login check was run. Run 'agy' once, log in, and check /usage yourself; there is one deliberate human step."
}

# ---------------------------------------------------------------------------
# Layer B: generic MCP client files (written for both clients).
# ---------------------------------------------------------------------------
agy_mcp_write_agents_guidance() {
    cat <<'AGY_MCP_AGENTS_EOF'
# Coding jobs with agy (mcp__agy__ tools)
You can hand self-contained coding work to Google's agy through the mcp__agy__ tools. agy works on its own git branch in an isolated worktree; the wrapper audits it, runs verification and makes the commits. The wrapper never merges or pushes.

## When to delegate
- Delegate: a clearly specified chunk (one module, tests for existing code, a refactor, docs, a port), anything long-running, anything that can run while you do other work.
- Do it yourself: small edits, design decisions, security-sensitive code, anything that needs the whole conversation's context.
- Before delegating, call agy_usage. Do not start new jobs when the Gemini Models weekly bucket remaining is below 10% or its 5h bucket remaining is below 15%. Run at most 2 jobs at once.

## Choosing a workflow (write it in docs/orchestrator/PLAN.md first)
Before any work write docs/orchestrator/PLAN.md; its first line names the chosen workflow and why. If the goal is ambiguous and nobody is watching: take the most reasonable reading, say which in PLAN.md, continue.
- GATED (default when tests can check the result): write the contract and acceptance tests in tests/acceptance/ first and commit them; start ONE big agy job: "make tests/acceptance pass, never modify tests/acceptance, add your own tests"; wait with the longest agy_wait the tool allows and do nothing between waits (no polling); gate in this order: `git diff <base> -- tests/acceptance` must be empty (restore from your commit if agy touched it), agy_review and read the diff, merge into integration/<topic>, run the acceptance tests and the full suite yourself; never trust the job's claim of success; at most one fix job, and an optional second job for extra tests/hardening is gated the same way.
- MICRO (risky core code, or a task that cannot be specified up front): 3-5 small chunks, one agy job each; after EACH job: agy_review, read the diff, merge, run the full suite, then agy_cleanup.
- RESEARCH (no spec possible): you own the design, the interfaces and the final synthesis; delegate only self-contained research sub-topics or modules; treat everything agy reports as unverified until you check it against a source (mark claims VERIFIED only with a source, else ASSUMED).
- DIRECT (tiny: roughly one file or a few lines): do it yourself, no agy.

Always: every agy job prompt is fully self-contained (goal, files, constraints, 3-10 step checklist, verify command, commit subject <= 72 characters); check agy_usage before every start; below the quota floor, do the remaining chunks yourself and log the handover in docs/orchestrator/DECISIONS.md; if a job ends in an odd state use agy_status/agy_resume/agy_cancel and log it, never dig into agy-mcp's source, config or state directories; do not re-read large files or poll.

## Running a job
1. agy_start with: a specific title; a prompt stating the goal, constraints, the files involved and the acceptance criteria; a checklist of 3-10 concrete steps; verify (for example "pytest -q"); max_minutes (30 for most jobs). Do not pass the model parameter unless the human named one; use the server default.
2. agy_wait until the state changes (it returns after at most 5 minutes; call it again). Do not poll agy_status in a loop.
3. When done: agy_review. Trust the wrapper's facts, audit_findings and verify result, not the agent's own report. agy_review carries the same plan, facts, agent_report and scratchpad as agy_wait, so read agent_report and plan to see what was skipped. Read the diff before accepting.
4. Accept = merge the job branch into a dedicated integration branch in the project (for example integration/<topic>), run the project's tests there, then agy_cleanup. Never merge, fast-forward or push into main/master or the repository's default branch: that is the human's decision. Record "merge integration/<topic> into main when ready" in docs/orchestrator/REPORT.md and commit docs/orchestrator/ on the integration branch, not on main. Reject = agy_resume with specific feedback (keeping manual resumes to a small number; the cumulative max_minutes budget still applies), or discard and redo with a better prompt.
5. Never pass force=true to agy_cleanup. If agy_cleanup refuses, remove the worktree only (delete_branch false), add the job branch to NEEDS_HUMAN and continue.

## States and what to do
- done: review as above.
- needs_decision: answer using the decision policy below, then agy_resume.
- needs_review with plan_incomplete or verify_failed, or in the timeout state: agy_resume once with specific guidance; if it repeats, take over yourself or split the task.
- needs_review with quota_exhausted: stop starting jobs, continue other work, resume after resume_after.
- needs_review with auth_required: stop and add it to NEEDS_HUMAN (agy needs a login).
- needs_review with audit_violation, host_path_modified, instruction_file_modified, audit_unavailable or blocked_by_permission: never auto-resume, never merge. Add it to NEEDS_HUMAN and continue with other work.
- failed: read the error; retry once with a changed approach; otherwise report it.
- If agy_resume fails with error code job_finalizing, the previous run is still wrapping up: wait 10 seconds and call agy_resume again (at most 5 attempts), then report it.

## Decision policy (you decide; the human is not there)
Say yes and log it when the choice is reversible, stays inside the repo, follows existing conventions or the SPEC, and is the smaller, simpler option.
Never decide alone (add to NEEDS_HUMAN and continue with other work): deleting or overwriting user data, pushing or any force operation, credentials/secrets/network access, spending money, changing permissions or sandbox settings, editing AGENTS.md / GEMINI.md / .agents/, installing system packages, touching files outside the repo, licensing, anything you cannot undo.

## Files to keep (in the project, docs/orchestrator/)
PLAN.md (tasks and status), DECISIONS.md (every decision you made and why, with the job id), NEEDS_HUMAN.md (questions that need a person), REPORT.md (at the end: done, not done, risks, how to verify). Update them as you go; a restarted session must be able to continue from them.

## Unattended runs
Work until the plan is done or only NEEDS_HUMAN items remain. Never loop on a failing job. If something is unclear, make the safe choice, log it and continue.
AGY_MCP_AGENTS_EOF
}

agy_mcp_write_generic_files() {
    local dir=""
    local run_sh=""
    local json_file=""
    local guidance_file=""
    dir="$(agy_mcp_dir)"
    run_sh="$(agy_mcp_run_sh)"
    json_file="$(agy_mcp_json_file)"
    guidance_file="$(agy_mcp_guidance_file)"

    agy_mcp_write_file "$json_file" 644 <<EOF
{
  "mcpServers": {
    "agy": {
      "command": "$(agy_mcp_json_escape "$run_sh")",
      "args": [],
      "env": {}
    }
  }
}
EOF
    agy_mcp_write_agents_guidance | agy_mcp_write_file "$guidance_file" 644
}

agy_mcp_print_generic_files() {
    local json_file=""
    local guidance_file=""
    json_file="$(agy_mcp_json_file)"
    guidance_file="$(agy_mcp_guidance_file)"

    note "MCP client JSON: $json_file"
    if agy_mcp_dry_run; then
        note "would print the mcpServers JSON snippet"
    else
        cat "$json_file"
    fi
    note "orchestrator guidance: $guidance_file"
    if agy_mcp_dry_run; then
        note "would print the orchestrator guidance"
    else
        cat "$guidance_file"
    fi
    note "Paste the JSON into your MCP client's mcpServers map and the guidance into its instruction file."
    note "Set the client tool-call timeout to at least 360000 ms: agy_wait blocks for up to 300 s and the client needs headroom."
}

agy_mcp_install_generic() {
    agy_mcp_install_core || return 1
    agy_mcp_write_generic_files || return 1
    agy_mcp_print_generic_files
}

# ---------------------------------------------------------------------------
# Layer C: dsh registration (marker blocks only).
# ---------------------------------------------------------------------------
agy_mcp_print_dsh_patch_block() {
    printf '%s\n' "$AGY_MCP_PATCH_BEGIN"
    printf '%s\n' '- insert:'
    printf '%s\n' '    - id: mcp-agy'
    printf '%s\n' "      name: '@deepseek-ai/dsh-mcp-client'"
    printf '%s\n' '      config:'
    printf '%s\n' '        serverName: agy'
    printf '%s\n' '        transport: stdio'
    printf '        command: %s\n' "$(agy_mcp_run_sh)"
    printf '%s\n' '        toolCallTimeoutMs: 360000'
    printf '%s\n' "$AGY_MCP_PATCH_END"
}

agy_mcp_print_agents_block() {
    printf '%s\n' "$AGY_MCP_AGENTS_BEGIN"
    agy_mcp_write_agents_guidance
    printf '%s\n' "$AGY_MCP_AGENTS_END"
}

agy_mcp_flow_style_offenders() {
    # Print "LINE: text" for each line that really uses YAML flow style.
    # Ignored: full-line comments (first non-space char is '#') and the lines
    # inside this chapter's own marker block, which the installer rewrites
    # wholesale anyway (B3).
    local patch="$1"
    awk -v b="$AGY_MCP_PATCH_BEGIN" -v e="$AGY_MCP_PATCH_END" '
        $0 == b { inblock = 1; next }
        $0 == e && inblock { inblock = 0; next }
        inblock { next }
        {
            text = $0
            sub(/^[[:space:]]+/, "", text)
            if (substr(text, 1, 1) == "#") next
        }
        /\[[^]]*\]/ { print NR ": " $0 }
    ' "$patch"
}

agy_mcp_check_flow_style_patch() {
    local patch="$1"
    local offenders=""
    if [[ -f "$patch" ]]; then
        offenders="$(agy_mcp_flow_style_offenders "$patch")"
        if [[ -n "$offenders" ]]; then
            agy_mcp_print_dsh_patch_block
            printf '%s\n' "$offenders" >&2
            die "refusing to edit $patch: it uses flow style ([...]) on the line(s) printed above; add the block printed above manually, then rerun. The file was not changed."
        fi
    fi
}

agy_mcp_check_dsh_preconditions() {
    refuse_inside_dsh
    agy_mcp_check_flow_style_patch "$(agy_mcp_dsh_patch_path)"
}

agy_mcp_replace_block() {
    local file="$1"
    local begin="$2"
    local end="$3"
    local block="$4"
    local tmp=""
    if [[ -f "$file" ]]; then
        if grep -Fqx -- "$begin" "$file" && ! grep -Fqx -- "$end" "$file"; then
            die "refusing to edit $file: found '$begin' without '$end'"
        fi
    fi
    if agy_mcp_dry_run; then
        note "would replace the agy-mcp block in $file"
        return 0
    fi
    mkdir -p -- "$(dirname -- "$file")" || die "cannot create directory for $file"
    tmp="$(mktemp "${file}.tmp.XXXXXX")" || die "cannot create temporary file for $file"
    if [[ -f "$file" ]]; then
        awk -v b="$begin" -v e="$end" -v blockfile="$block" '
            $0 == b {
                while ((getline line < blockfile) > 0) print line
                close(blockfile)
                inblock = 1
                wrote = 1
                next
            }
            $0 == e && inblock { inblock = 0; next }
            !inblock { print }
            END {
                if (!wrote) {
                    while ((getline line < blockfile) > 0) print line
                    close(blockfile)
                }
            }
        ' "$file" > "$tmp" || {
            rm -f -- "$tmp"
            die "failed to rewrite $file"
        }
        chmod --reference="$file" "$tmp" 2>/dev/null || chmod 644 "$tmp"
    else
        cp -- "$block" "$tmp" || {
            rm -f -- "$tmp"
            die "failed to create $file"
        }
        chmod 644 "$tmp"
    fi
    mv -f -- "$tmp" "$file" || {
        rm -f -- "$tmp"
        die "cannot replace $file"
    }
}

agy_mcp_register_dsh() {
    agy_mcp_check_dsh_preconditions
    local patch=""
    local agents=""
    local block=""
    patch="$(agy_mcp_dsh_patch_path)"
    agents="$(agy_mcp_dsh_agents_path)"

    if [[ -f "$patch" ]]; then
        agy_mcp_run cp -p -- "$patch" "$patch.bak"
    fi
    block="$(mktemp "${TMPDIR:-/tmp}/agymcp-patch.XXXXXX")" || die "cannot create temporary block"
    agy_mcp_print_dsh_patch_block > "$block"
    agy_mcp_replace_block "$patch" "$AGY_MCP_PATCH_BEGIN" "$AGY_MCP_PATCH_END" "$block"
    rm -f -- "$block"
    note "dsh patch block installed: $patch"

    if [[ -f "$agents" ]]; then
        agy_mcp_run cp -p -- "$agents" "$agents.bak"
    fi
    block="$(mktemp "${TMPDIR:-/tmp}/agymcp-agents.XXXXXX")" || die "cannot create temporary block"
    agy_mcp_print_agents_block > "$block"
    agy_mcp_replace_block "$agents" "$AGY_MCP_AGENTS_BEGIN" "$AGY_MCP_AGENTS_END" "$block"
    rm -f -- "$block"
    note "dsh AGENTS.md block installed: $agents"
}

agy_mcp_restart_dsh() {
    if [[ "${AGY_MCP_NO_RESTART:-0}" == "1" ]]; then
        note "AGY_MCP_NO_RESTART=1: skipping the dsh restart"
        return 0
    fi
    refuse_inside_dsh
    if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
        note "restarting dsh through the systemd user session"
        agy_mcp_run systemctl --user restart dsh || warn "could not restart dsh; restart it yourself to load the new MCP server"
    else
        note "no systemd user session detected; restart dsh yourself to load the new MCP server"
    fi
}

# ---------------------------------------------------------------------------
# ch_agymcp: the paste-in chapter (layers A + B + C).
#
# PASTE-IN NOTES for the owner's install-ai-stack.sh.  This function body calls
# the helper functions above; paste those helpers too (or source this file).
# Exactly three host edits are needed:
#   1. Add "agymcp" to the ALL_CHAPTERS list/array.
#   2. Add the chapter description entry used by the list/help command, e.g.
#        agymcp) printf 'agy-mcp MCP server for autonomous agy coding jobs\n' ;;
#   3. Add "agymcp" to the --doctor dispatch so it calls doctor_agymcp.
# ---------------------------------------------------------------------------
ch_agymcp() {
    agy_mcp_check_dsh_preconditions || return 1
    agy_mcp_install_core || return 1
    agy_mcp_write_generic_files || return 1
    agy_mcp_print_generic_files
    agy_mcp_register_dsh || return 1
    agy_mcp_restart_dsh || return 1
}

# ---------------------------------------------------------------------------
# Doctor, uninstall, usage.
# ---------------------------------------------------------------------------
agy_mcp_remove_block() {
    local file="$1"
    local begin="$2"
    local end="$3"
    local tmp=""
    if [[ ! -f "$file" ]]; then
        return 0
    fi
    if ! grep -Fqx -- "$begin" "$file"; then
        return 0
    fi
    if ! grep -Fqx -- "$end" "$file"; then
        warn "not editing $file: found '$begin' without '$end'"
        return 1
    fi
    if agy_mcp_dry_run; then
        note "would remove the agy-mcp block from $file"
        return 0
    fi
    tmp="$(mktemp "${file}.tmp.XXXXXX")" || die "cannot create temporary file for $file"
    awk -v b="$begin" -v e="$end" '
        $0 == b { inblock = 1; next }
        $0 == e && inblock { inblock = 0; next }
        !inblock { print }
    ' "$file" > "$tmp" || {
        rm -f -- "$tmp"
        die "failed to rewrite $file"
    }
    chmod --reference="$file" "$tmp" 2>/dev/null || chmod 644 "$tmp"
    mv -f -- "$tmp" "$file" || {
        rm -f -- "$tmp"
        die "cannot replace $file"
    }
    return 0
}

doctor_agymcp() {
    local client="${1:-$(agy_mcp_client)}"
    local failures=0
    local dir=""
    local run_sh=""
    local venv=""
    local config=""
    local agy_bin=""
    dir="$(agy_mcp_dir)"
    run_sh="$(agy_mcp_run_sh)"
    venv="$dir/venv"
    config="$(agy_mcp_config_path)"

    log "agy-mcp doctor ($client)"
    if [[ -x "$run_sh" ]]; then
        good "run.sh is executable: $run_sh"
    else
        note "run.sh is missing or not executable: $run_sh"
        failures=$((failures + 1))
    fi
    if [[ -x "$venv/bin/python" ]] && env -u PYTHONPATH "$venv/bin/python" -c 'import agy_mcp' >/dev/null 2>&1; then
        good "venv imports agy_mcp: $venv"
    else
        note "venv does not import agy_mcp: $venv"
        failures=$((failures + 1))
    fi
    if agy_bin="$(agy_mcp_find_agy)"; then
        good "agy found at $agy_bin"
    else
        note "agy not found on PATH or at ~/.local/bin/agy"
        failures=$((failures + 1))
    fi
    if [[ -f "$config" ]]; then
        good "config present at $config"
    else
        note "config missing at $config"
        failures=$((failures + 1))
    fi

    if [[ "$client" == "dsh" ]]; then
        local patch=""
        local agents=""
        patch="$(agy_mcp_dsh_patch_path)"
        agents="$(agy_mcp_dsh_agents_path)"
        if [[ -f "$patch" ]] && grep -Fqx -- "$AGY_MCP_PATCH_BEGIN" "$patch"; then
            good "dsh patch block present in $patch"
        else
            note "dsh patch block missing from $patch"
            failures=$((failures + 1))
        fi
        if [[ -f "$agents" ]] && grep -Fqx -- "$AGY_MCP_AGENTS_BEGIN" "$agents"; then
            good "AGENTS.md block present in $agents"
        else
            note "AGENTS.md block missing from $agents"
            failures=$((failures + 1))
        fi
    else
        if [[ -f "$(agy_mcp_json_file)" ]]; then
            good "generic MCP JSON present: $(agy_mcp_json_file)"
        else
            note "generic MCP JSON missing: $(agy_mcp_json_file)"
            failures=$((failures + 1))
        fi
        if [[ -f "$(agy_mcp_guidance_file)" ]]; then
            good "generic guidance present: $(agy_mcp_guidance_file)"
        else
            note "generic guidance missing: $(agy_mcp_guidance_file)"
            failures=$((failures + 1))
        fi
    fi

    if [[ "$failures" -eq 0 ]]; then
        ok "agy-mcp doctor: healthy ($client)"
        return 0
    fi
    warn "agy-mcp doctor: $failures problem(s) found ($client)"
    return 1
}

agy_mcp_has_block() {
    local file="$1"
    local begin="$2"
    [[ -f "$file" ]] && grep -Fqx -- "$begin" "$file"
}

# Remove this chapter's marker block from one dsh file and print what was removed.
# Returns 0 only when the block was actually removed, so the caller never claims
# a removal (and a restart) that did not happen (C2).
agy_mcp_remove_dsh_block() {
    local file="$1"
    local begin="$2"
    local end="$3"
    if ! agy_mcp_has_block "$file" "$begin"; then
        return 1
    fi
    if [[ -n "$(agy_mcp_flow_style_offenders "$file")" ]]; then
        warn "not editing $file: it uses flow style ([...]); remove the agy-mcp block manually"
        return 1
    fi
    agy_mcp_run cp -p -- "$file" "$file.bak"
    agy_mcp_remove_block "$file" "$begin" "$end" || true
    note "removed the agy-mcp block from $file"
}

agy_mcp_remove_dsh_blocks() {
    local patch=""
    local agents=""
    local removed=0
    patch="$(agy_mcp_dsh_patch_path)"
    agents="$(agy_mcp_dsh_agents_path)"
    if agy_mcp_has_block "$patch" "$AGY_MCP_PATCH_BEGIN"; then
        if agy_mcp_remove_dsh_block "$patch" "$AGY_MCP_PATCH_BEGIN" "$AGY_MCP_PATCH_END"; then
            removed=1
        fi
    fi
    if agy_mcp_has_block "$agents" "$AGY_MCP_AGENTS_BEGIN"; then
        if agy_mcp_remove_dsh_block "$agents" "$AGY_MCP_AGENTS_BEGIN" "$AGY_MCP_AGENTS_END"; then
            removed=1
        fi
    fi
    if [[ "$removed" == "1" ]]; then
        note "restart dsh from a normal terminal to unload the server"
    fi
}

# An explicit --client generic must not edit dsh, but the registration it leaves
# behind now points at the run.sh this uninstall deletes: say so.
agy_mcp_warn_dsh_blocks_left() {
    local patch=""
    local agents=""
    local found=0
    patch="$(agy_mcp_dsh_patch_path)"
    agents="$(agy_mcp_dsh_agents_path)"
    if agy_mcp_has_block "$patch" "$AGY_MCP_PATCH_BEGIN"; then
        found=1
    fi
    if agy_mcp_has_block "$agents" "$AGY_MCP_AGENTS_BEGIN"; then
        found=1
    fi
    if [[ "$found" == "1" ]]; then
        warn "--client generic left the dsh agy-mcp block(s) in place in $patch and/or $agents: they still register the agy-mcp server and point at a missing run.sh ($(agy_mcp_run_sh))."
        warn "rerun --uninstall without --client (or with --client dsh) to remove them, then restart dsh from a normal terminal to unload the server"
    fi
}

uninstall_agymcp() {
    local client="${1:-$(agy_mcp_client)}"
    # Second argument: 1 when the caller named the client explicitly. With no
    # explicit client the dsh blocks are removed too, so an uninstall can never
    # leave a registration pointing at a deleted run.sh (B2).
    local client_explicit="${2:-0}"
    local dir=""
    dir="$(agy_mcp_dir)"
    log "uninstalling agy-mcp ($client)"

    if [[ "$client" == "dsh" ]]; then
        agy_mcp_remove_dsh_blocks
    elif [[ "$client_explicit" == "1" ]]; then
        agy_mcp_warn_dsh_blocks_left
    else
        agy_mcp_remove_dsh_blocks
    fi

    local file=""
    for file in \
        "$(agy_mcp_run_sh)" \
        "$(agy_mcp_env_file)" \
        "$(agy_mcp_json_file)" \
        "$(agy_mcp_guidance_file)"; do
        if [[ -e "$file" ]]; then
            agy_mcp_run rm -f -- "$file"
        fi
    done
    if [[ -d "$dir/venv" ]]; then
        agy_mcp_run rm -rf -- "$dir/venv"
    fi
    if agy_mcp_dry_run; then
        note "would remove empty directory $dir if present"
    else
        rmdir -- "$dir" 2>/dev/null || true
    fi
    note "kept: $(agy_mcp_config_path), $(agy_mcp_state_dir) and any .bak backups"
}

agy_mcp_usage() {
    cat <<'AGY_MCP_USAGE'
Usage: installer/install.sh [options]

  --client dsh|generic   Target client. Default: generic (no dsh files).
  --dry-run              Print every file and command; change nothing.
  --upgrade              Reinstall agy-mcp from AGY_MCP_SRC even when the venv
                         already imports it (pip --upgrade --force-reinstall;
                         dependencies are re-resolved).
  --doctor               Check run.sh, venv, agy, config and client blocks.
  --uninstall            Remove only this chapter's blocks and generated files.
  --help                 Show this help.

Environment: AGY_MCP_CLIENT, AGY_MCP_DRY_RUN=1, AGY_MCP_SRC, AGY_MCP_EDITABLE=1,
AGY_MCP_UPGRADE=1, AGY_MCP_PYTHON, AGY_MCP_PIP_ARGS, AGY_MCP_NO_RESTART=1,
AGY_MCP_AGY_BINARY.

Standalone installer default: generic. DSH registration is opt-in with
--client dsh. Use --dry-run first if you want to see every write.
AGY_MCP_USAGE
}

main() {
    local client=""
    local client_explicit=0
    local dry=0
    local doctor=0
    local uninstall=0
    local upgrade=0
    local arg=""

    if [[ -n "${AGY_MCP_CLIENT:-}" ]]; then
        client="$AGY_MCP_CLIENT"
        client_explicit=1
    fi
    if [[ "${AGY_MCP_DRY_RUN:-0}" == "1" ]]; then
        dry=1
    fi

    while [[ $# -gt 0 ]]; do
        arg="$1"
        case "$arg" in
            --client)
                if [[ $# -lt 2 ]]; then
                    die "--client needs a value: dsh or generic"
                fi
                client="$2"
                client_explicit=1
                shift 2
                ;;
            --client=*)
                client="${arg#--client=}"
                client_explicit=1
                shift
                ;;
            --dry-run)
                dry=1
                shift
                ;;
            --upgrade)
                upgrade=1
                shift
                ;;
            --doctor)
                doctor=1
                shift
                ;;
            --uninstall)
                uninstall=1
                shift
                ;;
            --help|-h)
                agy_mcp_usage
                return 0
                ;;
            *)
                die "unknown option '$arg'; try --help"
                ;;
        esac
    done

    client="${client:-generic}"
    case "$client" in
        dsh|generic) ;;
        *)
            die "unknown client '$client' (expected 'dsh' or 'generic')"
            ;;
    esac
    AGY_MCP_CLIENT="$client"
    if [[ "$dry" == "1" ]]; then
        AGY_MCP_DRY_RUN=1
    else
        AGY_MCP_DRY_RUN="${AGY_MCP_DRY_RUN:-0}"
    fi
    if [[ "$upgrade" == "1" ]]; then
        AGY_MCP_UPGRADE=1
    fi
    if [[ "$doctor" == "1" && "$uninstall" == "1" ]]; then
        die "choose only one of --doctor and --uninstall"
    fi
    if [[ "$doctor" == "1" ]]; then
        doctor_agymcp "$client"
        return $?
    fi
    if [[ "$uninstall" == "1" ]]; then
        uninstall_agymcp "$client" "$client_explicit"
        return $?
    fi
    if [[ "$client" == "dsh" ]]; then
        ch_agymcp
    else
        agy_mcp_install_generic
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
