# ADR 0001 — Physical Tier Separation for Custom Nodes

**Status**: Accepted  
**Date**: 2026-06-25

## Context

ComfyUI Manager installs nodes into the active container filesystem. Without
persistence, all runtime-installed nodes are lost on cold start. The
`feat/persist-custom-nodes` branch addressed this by symlinking the image's
`custom_nodes/` directory to `/cache/custom_nodes` in the Modal Volume after
seeding it with the image-baked Blessed Nodes.

This approach has a correctness bug: once a Blessed Node is seeded into the
Volume, upgrading it in `config.toml` and rebuilding the image has no effect.
The Volume copy shadows the image copy permanently. The seed is a one-way
write; there is no mechanism to re-sync or evict it.

ComfyUI supports registering multiple `custom_nodes` directories via
`extra_model_paths.yaml` — this is a first-class, documented feature.

## Decision

Blessed Nodes and Experimental Nodes live in **separate physical directories**.
ComfyUI scans both via `extra_model_paths.yaml` baked into the image.

```
/root/comfy/ComfyUI/custom_nodes/   ← Blessed Nodes (image, rebuilt on every deploy)
/cache/custom_nodes/                ← Experimental Nodes (Volume, persisted at runtime)
```

`extra_model_paths.yaml` registers `/cache/custom_nodes` with
**`is_default: true`**, which is what makes `folder_paths.get_folder_paths("custom_nodes")[0]`
resolve to the Volume path. ComfyUI-Manager always installs to index `[0]`, so
Manager-installed nodes land in the Volume and persist across restarts.

`is_default` is required rather than "listing the Volume first": ComfyUI
registers its built-in `custom_nodes` path at import time, so yaml entries are
appended unless the block sets `is_default` (`utils/extra_config.py` →
`folder_paths.add_model_folder_path(..., is_default=True)`, which inserts at
index 0). The image's own `custom_nodes/` directory needs no yaml entry because
ComfyUI already registers it.

No seed copy. No symlink. The image directory is never mutated at runtime.

Both entrypoints must call `server/comfy_runtime.py::ensure_runtime_dirs()`
before launching ComfyUI: a registered-but-missing `custom_nodes` path makes
`execute_prestartup_script`'s unguarded `os.listdir` crash startup.

## Consequences

**Gained:**
- Upgrading a Blessed Node in `config.toml` + rebuilding the image always takes
  effect — no stale Volume copy can shadow it.
- Tier boundary is physically enforced: what is in the image vs. what is in the
  Volume is unambiguous.
- Collision (same node in both tiers) is visible, not silent.

**Lost / accepted cost:**
- Image rebuild is required to activate the new yaml (one-time).
- Users who already have Blessed Nodes seeded into the Volume from the old
  branch will see duplicate loads until they manually remove the Volume copies.
- Requires verifying that `extra_model_paths.yaml` `custom_nodes:` key is
  supported (verified against ComfyUI source and `extra_model_paths.yaml.example`).

## Alternatives Rejected

**E2 — Seed + Symlink** (previous branch implementation)  
Image `custom_nodes/` is copied into the Volume on first boot, then replaced
with a symlink to the Volume. Simple code, but produces the shadow bug
described above. Rejected.

**E2′ — Runtime rename + symlink** (interim implementation, removed 2026-09)  
`custom_nodes/` was renamed to `blessed_custom_nodes/` at container start and
`custom_nodes` symlinked to the Volume, with the yaml registering the renamed
directory. It appeared to satisfy Manager's index-0 requirement, but it mutated
the image directory at runtime, depended on a path that does not exist at build
time, and broke headless inference containers that never ran the rename.
Rejected in favour of `is_default: true`.

**Single Volume directory, no image baking**  
Skip Blessed Nodes entirely; all nodes go through Manager at runtime. Loses
reproducibility for build-time nodes and breaks the `config.toml` plugin
workflow. Rejected.
