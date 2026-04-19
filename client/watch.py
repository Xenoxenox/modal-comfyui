"""实时监听 ComfyUI Web UI，将新生成图片自动下载到本地 output/ 目录。

用法：
    python -m client.watch <Modal-Web-UI-URL>
    python -m client.watch          # 交互输入 URL

工作原理：
    每隔 POLL_INTERVAL 秒轮询 ComfyUI GET /history，
    对比本地已下载集合，发现新 prompt 后通过 GET /view 下载全部输出图片，
    写入 output/<prompt_id前8位>_<原始文件名>。
    整个过程不经过 Modal Storage，无需修改服务端代码。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from client.utils import ensure_utf8_stdio, setup_logger

ensure_utf8_stdio()

try:
    import requests
except ImportError:
    print("需要 requests，请运行 `uv sync` 或 `pip install requests`。")
    raise

POLL_INTERVAL = 3  # 秒
DEFAULT_OUTPUT_DIR = Path("output")


def strip_trailing_slash(url: str) -> str:
    return url.rstrip("/")


def poll_once(
    base_url: str,
    session: requests.Session,
    downloaded_prompts: set[str],
    output_dir: Path,
) -> int:
    """检查一次 /history，下载所有新图片。返回本次新下载的文件数。"""
    resp = session.get(f"{base_url}/history", timeout=10)
    resp.raise_for_status()
    history: dict = resp.json()  # {prompt_id: {outputs: {...}, ...}}

    new_files = 0
    for prompt_id, data in history.items():
        if prompt_id in downloaded_prompts:
            continue

        outputs: dict = data.get("outputs", {})
        images_found = False

        for _node_id, node_out in outputs.items():
            for img in node_out.get("images", []):
                filename: str = img["filename"]
                subfolder: str = img.get("subfolder", "")
                img_type: str = img.get("type", "output")

                params = {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                }
                try:
                    img_resp = session.get(
                        f"{base_url}/view", params=params, timeout=60
                    )
                    img_resp.raise_for_status()
                except Exception as exc:
                    logging.warning("下载失败 %s: %s", filename, exc)
                    continue

                # 写入 output/<prompt_id前8位>_<原始文件名>
                save_name = f"{prompt_id[:8]}_{filename}"
                save_path = output_dir / save_name
                save_path.write_bytes(img_resp.content)
                logging.info("✅ 已下载: %s  (%d KB)", save_path, len(img_resp.content) // 1024)
                new_files += 1
                images_found = True

        # 即使没有图片输出（如纯文本节点），也标记为已处理，避免重复扫描
        downloaded_prompts.add(prompt_id)
        if not images_found:
            logging.debug("Prompt %s 无图片输出，跳过。", prompt_id[:8])

    return new_files


def watch(base_url: str, output_dir: Path) -> None:
    """主监听循环，Ctrl-C 退出。"""
    base_url = strip_trailing_slash(base_url)
    output_dir.mkdir(parents=True, exist_ok=True)

    downloaded_prompts: set[str] = set()
    total_downloaded = 0

    session = requests.Session()
    # 给 requests 一个友好的 User-Agent，避免被某些代理拦截
    session.headers.update({"User-Agent": "comfyui-watch/1.0"})

    logging.info("🔍 开始监听 ComfyUI: %s", base_url)
    logging.info("📁 本地输出目录: %s", output_dir.resolve())
    logging.info("⏱  轮询间隔: %d 秒（Ctrl-C 退出）", POLL_INTERVAL)

    # 启动时先扫描一次，将历史 prompt 标记为已处理（不重复下载旧图）
    try:
        resp = session.get(f"{base_url}/history", timeout=10)
        resp.raise_for_status()
        existing = set(resp.json().keys())
        downloaded_prompts.update(existing)
        logging.info("已忽略 %d 条历史记录（仅监听新生成）。", len(existing))
    except Exception as exc:
        logging.warning("初始化历史记录失败（将从空集合开始）: %s", exc)

    try:
        while True:
            try:
                n = poll_once(base_url, session, downloaded_prompts, output_dir)
                if n > 0:
                    total_downloaded += n
                    logging.info("本次下载 %d 张，累计 %d 张。", n, total_downloaded)
            except requests.exceptions.ConnectionError as exc:
                logging.warning("连接失败（ComfyUI 是否已启动？）: %s", exc)
            except requests.exceptions.Timeout:
                logging.warning("请求超时，稍后重试…")
            except Exception as exc:
                logging.warning("轮询异常: %s", exc)

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("已停止监听。共下载 %d 张图片。", total_downloaded)


def main() -> int:
    setup_logger()

    # 支持命令行传入 URL，或交互输入
    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()
    else:
        url = input("请输入 Modal Web UI URL（如 https://xxx.modal.run）: ").strip()

    if not url:
        logging.error("URL 不能为空。")
        return 1

    if not url.startswith(("http://", "https://")):
        logging.error("URL 必须以 http:// 或 https:// 开头。")
        return 1

    output_dir = DEFAULT_OUTPUT_DIR
    watch(url, output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
