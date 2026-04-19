"""
运行指定命令并捕获输出。若命令失败，将错误写入 .claude/_last_error.txt，
供 Claude Code asyncRewake hook 读取后自动唤醒 Claude 修复。

用法：
    python scripts/run_and_report.py python -m client.watch https://xxx.modal.run
    python scripts/run_and_report.py python serve.py
    python scripts/run_and_report.py python -m client.infer
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Windows GBK 终端编码修复（复用项目 utils 模式）
import io as _io
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ERROR_FILE = Path(".claude/_last_error.txt")
MAX_LINES = 80  # 最多保留末尾 N 行错误输出（避免日志过长撑爆上下文）


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/run_and_report.py <命令> [参数...]")
        return 1

    cmd = sys.argv[1:]
    print(f"[RUN] {' '.join(cmd)}\n", flush=True)

    result = subprocess.run(
        cmd,
        capture_output=False,   # 让输出直接显示在终端
        text=True,
    )

    if result.returncode == 0:
        # 成功：清除上次错误记录
        if ERROR_FILE.exists():
            ERROR_FILE.unlink()
        print("\n[OK] 运行成功（退出码 0）")
        return 0

    # 失败：重新运行一次以捕获输出写入错误文件
    print(f"\n[FAIL] 退出码 {result.returncode}，正在捕获错误输出...", flush=True)

    captured = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    combined = (captured.stdout + "\n" + captured.stderr).strip()
    lines = combined.splitlines()
    if len(lines) > MAX_LINES:
        lines = ["... (前面输出已省略，仅保留末尾内容) ...", ""] + lines[-MAX_LINES:]
    error_text = "\n".join(lines)

    ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
    ERROR_FILE.write_text(
        f"命令: {' '.join(cmd)}\n退出码: {captured.returncode}\n\n{error_text}",
        encoding="utf-8",
    )

    print(f"\n错误已写入 {ERROR_FILE}，Claude 将自动唤醒并修复。")

    # 退出码 2 触发 asyncRewake hook 唤醒 Claude
    return 2


if __name__ == "__main__":
    sys.exit(main())
