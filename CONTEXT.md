# Context Glossary

Canonical domain language for modal-comfyui. Glossary only — no implementation
details, no specs. Update inline as terms are resolved.

---

## Custom Node (ComfyUI)

A ComfyUI extension installed under `custom_nodes/`. In this project, custom
nodes fall into exactly two tiers (see below). The full set that actually runs
in a container = **Blessed Nodes ∪ Experimental Nodes**.

## Blessed Node

A custom node declared in `config.toml` under `[plugins.*]` and installed at
Modal **image build** time. Reproducible, versioned, visible in the repo.
Example: a GitHub-only node not in the comfy.org registry
(`NewBieAI-Lab/ComfyUI-Newbie-Nodes`).

## Experimental Node

A custom node installed at **runtime** via the ComfyUI Manager UI. Lives in the
`comfy-cache` Volume at `/cache/custom_nodes`. Persists across container
restarts but is NOT in git and NOT reproducible from the repo alone.

## Promotion

The opt-in act of converting an Experimental Node into a Blessed Node by
writing its identity into `config.toml`. After promotion the node is baked at
image build. Promotion is deliberate and selective — there is NO automatic
write-back from the Volume to `config.toml`.

Identity captured = repo URL only, read from the Volume checkout
(`git remote get-url origin`), written as `repo = <url>`. NO version/commit
pin. This matches existing Blessed Node behaviour: `comfy node install <url>`
already installs default-branch HEAD at build time, so the whole system is
unpinned by design today. Reproducibility-by-commit is a known system-wide gap,
deferred to a future decision — not part of Promotion.

## Source-of-Truth Boundary (Tier Model A3)

`config.toml` and `/cache/custom_nodes` are deliberately independent sources.
- `config.toml` does not reflect Experimental Nodes.
- Editing/removing a `config.toml` entry does NOT remove a node already in the
  Volume.
- The repo intentionally does not show the complete runtime node set; the
  Volume is the missing half.
This separation is accepted by design; the mitigation is visibility tooling
(listing) plus opt-in Promotion, not synchronization.

## Node Dependency Install (runtime)

Experimental Nodes carry Python deps (`requirements.txt`) that do NOT persist —
they install into the container's ephemeral system site-packages (no venv; build
uses plain `debian_slim` + system pip). So deps must be reinstalled on every
cold start for nodes that need them.

Established behaviour (verified against ComfyUI `main.py`): a missing dep does
NOT crash ComfyUI. Node imports run once in a single `init_extra_nodes` call;
a failed import is logged `IMPORT FAILED` and the startup loop continues. The
server then starts unconditionally. Therefore a node whose deps arrive *after*
that one-shot scan stays dead until ComfyUI re-scans — i.e. until a restart.

Agreed install strategy = **D1′ + D2**:
- D2: per-node `.deps-installed` marker in the Volume; skip pip if present.
  Marker stores `sha256(requirements.txt)` at install time. On cold start,
  recompute hash — reinstall only if hash changed (catches dep changes on node
  update). Nodes without `requirements.txt` never write a marker.
  Steady-state cold start cost = one file hash read per node.
- D1′: pip runs in a background thread (OFF the `@modal.web_server`
  `startup_timeout=60` critical path, so it can never hard-fail startup). When a
  real install actually happened this boot, the installer must EXPLICITLY
  restart ComfyUI so node imports re-run with deps present. Passively waiting on
  the crash-supervisor does NOT work — ComfyUI does not exit on import failure.
  
**Restart mechanism (R1)**: Background thread holds a reference to the ComfyUI
`Popen` object. After successful pip install, thread calls
`comfy_process.terminate()`. Supervisor detects exit and relaunches. No network
call, no sentinel files.

## Comfy Supervisor

The daemon that relaunches ComfyUI when its process exits. Protects against
crashes only. It is NOT a dependency-recovery mechanism: late-arriving deps must
trigger a restart explicitly (see Node Dependency Install).

## Node Directory Separation (E3 — physical tier boundary)

Blessed and Experimental Nodes live in SEPARATE physical directories; ComfyUI
scans both. This is the agreed mechanism (verified against ComfyUI source +
official `extra_model_paths.yaml.example`).

- Image `custom_nodes/` — Blessed Nodes only, rebuilt fresh on every image
  build. NEVER mutated at runtime (no seed-copy, no rmtree, no symlink).
- `/cache/custom_nodes/` (Volume) — Experimental Nodes only. Registered as an
  ADDITIONAL scan path via `extra_model_paths.yaml` (`custom_nodes:` key is
  officially supported) or `--extra-model-paths-config`.
- `extra_model_paths.yaml` is **baked into the image at build time** (W1) via
  `add_local_file`, same pattern as `nginx.conf`. Both paths are static
  constants known at build time; no runtime generation needed.

Why: ComfyUI `init_extra_nodes` iterates `get_folder_paths("custom_nodes")` as
a LIST, and `apply_custom_paths()` loads `extra_model_paths.yaml`. So a second
custom_nodes dir is a supported, first-class config — not a hack.

This REPLACES the current branch's seed+symlink approach, which had a
correctness bug: once a Blessed Node was copied into the Volume, the Volume
permanently shadowed the image, so bumping the node's version in `config.toml`
+ rebuild had no effect. With E3 there is no copy and no shadow.

Collision (same node name Blessed AND Experimental) is user error and visible;
under E3 the intended state is zero overlap.

### extra_model_paths.yaml (W1a)

File baked into the image at `/root/comfy/ComfyUI/extra_model_paths.yaml`
(ComfyUI default scan path) via `add_local_file`. No launch flag needed.
ComfyUI finds it automatically at startup.

### Manager Install Target (Q6 resolution)

ComfyUI-Manager (`glob/manager_core.py::get_default_custom_nodes_path`) always
installs into `folder_paths.get_folder_paths("custom_nodes")[0]` — the first
registered path. There is no separate Manager config for install target.

**Corrected (2026-09): yaml entry order cannot move index 0.** ComfyUI registers
its built-in `custom_nodes` path at import time, before `apply_custom_paths()`
reads the yaml, so extra paths are *appended* unless a block sets
`is_default: true` (`utils/extra_config.py` pops `is_default` and forwards it to
`folder_paths.add_model_folder_path`, which inserts at index 0). The shipped
config is therefore:

```yaml
volume-nodes:           # [0] → Manager installs here
  base_path: /cache
  custom_nodes: custom_nodes
  is_default: true      # REQUIRED: order alone does not put it first
```

No `image-nodes` block is needed: ComfyUI already registers the image's own
`custom_nodes/` directory, which stays load-only and is never written at
runtime.

The interim runtime rename (`custom_nodes/` → `blessed_custom_nodes/`) + symlink
was removed in 2026-09: it mutated the image directory, made the yaml reference a
directory that does not exist at build time, and left the headless container
(which never ran the rename) with a registered-but-missing `custom_nodes` path.
ComfyUI's `execute_prestartup_script` calls `os.listdir` on every registered
`custom_nodes` path without an existence guard, so that crashed startup.
`server/comfy_runtime.py::ensure_runtime_dirs()` now creates
`/cache/custom_nodes` and `/cache/user` in both entrypoints.
