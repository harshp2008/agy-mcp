# Three execution modes architecture: protected, sandbox, and unrestricted

Status: not built (design document only)
Audience: maintainers, contributors

Architectural plan for supporting three distinct per-project run modes in agy-mcp: protected (current baseline), sandbox (container namespaces), and unrestricted (detective host monitoring).

---

## 1. Overview and Objectives

`agy-mcp` currently runs in a single execution mode where real `agy` is launched under `--mode accept-edits`. While this ensures built-in safety checks prevent destructive file operations and unconfirmed shell commands, it also causes immediate soft-denials whenever an agent attempts necessary build, test, or directory discovery commands.

This document outlines the architectural plan for supporting three distinct per-project run modes:
1. `protected` (current baseline)
2. `sandbox` (unshare / Bubblewrap containment)
3. `unrestricted` (bypass agy permission checks with detective host monitoring)

---

## 2. The Three Execution Modes

### Mode 1: `protected` (Current Baseline)
* **Mechanics**: Launches `agy --mode accept-edits`. Relies on host-level Git worktrees and agy's internal policy guardian.
* **Guarantees**:
  - No shell commands execute without confirmation (in headless `-p` mode, all unapproved commands halt immediately).
  - Built-in file tools create parent directories and update files within the linked worktree.
  - Wrapper commits files via `.agy/COMMIT_MSG` polling and audits sensitive paths (`.git*`, `.agents/**`).
* **Limitations**: Zero test execution within agent turns; complex shell-based tasks require auto-resume guidance or `NEEDS_DECISION`.

### Mode 2: `sandbox` (Bubblewrap / Container Namespaces - Future)
* **Mechanics**: Launches `agy` inside an unprivileged user namespace using `bwrap` (Bubblewrap).
* **Guarantees**:
  - Filesystem read isolation: Read-only bind mounts of `/usr`, `/lib`, `/bin`, `/etc`.
  - Filesystem write isolation: Read-write bind mount restricted strictly to `<jobdir>/work` and `<jobdir>/tmp`.
  - Process isolation: Dedicated PID and mount namespaces; `/proc` filtered.
  - Network isolation: Loopback only or optional proxy gateway.
* **Limitations**: Requires container namespace support on the host kernel.

### Mode 3: `unrestricted` (Bypassed Permissions with Detective Safeguards)
* **Mechanics**: Launches `agy` with permission gates bypassed (e.g. `--dangerously-skip-permissions` or `--mode auto`). Real `agy` executes arbitrary commands and shell scripts without prompting or soft-denying.
* **Honest Threat Disclosure**:
  > [!WARNING]
  > The wrapper **cannot enforce** command-level restrictions on commands executed directly inside the `agy` process hierarchy. A wrapper running alongside an unrestricted agent can only provide **detective monitoring plus backup snapshots**.
* **Detective Never-Do List (Post-hoc & Boundary Auditing)**:
  - Recursive deletion of `/` or `$HOME`
  - Block device destruction (`dd`, `mkfs`, raw writes to `/dev/sd*`, `/dev/nvme*`)
  - Out-of-tree Git force-push (`git push --force`) to remote origins
  - Tampering with host SSH/GPG credentials or shell configuration files (`~/.bashrc`, `~/.ssh`)
* **Detection & Recovery**: Host path pre/post snapshots detect any violation immediately, halting subsequent resumes, refusing branch merges, and logging high-severity audit events.

---

## 3. Unknowns to Probe First

Before implementing `sandbox` or `unrestricted` modes, the following empirical probes must be conducted:

1. **CLI Bypass Capabilities in Headless Mode**:
   - Does `--dangerously-skip-permissions` actually permit shell commands (`RunCommand`) when running headless with `-p <prompt>`?
   - Does `--mode auto` or other undocumented CLI flags bypass the soft-denial in `tool_confirmation_manager.go`?
   - What exact flags eliminate `Print mode: soft-denying tool confirmation`?

2. **Linux Namespaces & Bubblewrap Compatibility**:
   - On modern distributions (notably Ubuntu 24.04 LTS), kernel restrictions on unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns = 1`) frequently block `bwrap` unless explicit AppArmor profiles or sysctl overrides are applied.
   - Probe: Can `bwrap` execute without root or custom AppArmor profiles on standard developer workstations?

3. **Transcript & State Interactions**:
   - Does unrestricted mode generate full tool confirmation events in the session transcript, or does it bypass transcript logging entirely?

---

## 4. Implementation Phasing

1. **Phase 1 (Probe & Measure)**: Scripted probes against real `agy` with candidate bypass flags in an isolated VM.
2. **Phase 2 (Configuration Model)**: Introduce `mode = "protected" | "sandbox" | "unrestricted"` in `config.toml` and MCP tool payloads.
3. **Phase 3 (Runner Abstraction)**: Implement `BubblewrapRunner` adhering to the existing `Runner` interface.

## Sources

- `src/agy_mcp/runner.py` — `Runner` interface, `LocalProcessRunner`, and `--mode accept-edits` invocation.
- `src/agy_mcp/config.py` — runner configuration keys.
- `doc/design/spec.md` — Section 4 (Runner interface and process management) and Section 5 (security model).
