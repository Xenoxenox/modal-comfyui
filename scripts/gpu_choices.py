from __future__ import annotations

GPU_CHOICES: list[tuple[str, str]] = [
    ("T4", "Lowest cost; useful for light workflows and smoke tests."),
    ("L4", "Default Web UI choice; balanced cost and compatibility."),
    ("L40S", "More VRAM and throughput for larger image/video workflows."),
    ("A10G", "Common midrange GPU for SDXL-era workflows."),
    ("A100-40GB", "High-memory option for large graphs."),
    ("A100-80GB", "Very high-memory option for large video workflows."),
    ("H100", "Fast premium option for heavy workloads."),
    ("H200", "Premium option with more memory than H100."),
    ("B200", "Newest high-end option when available in your Modal region."),
]

