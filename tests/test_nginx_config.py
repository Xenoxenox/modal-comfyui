from __future__ import annotations

import re
from pathlib import Path

NGINX_CONF = Path(__file__).resolve().parents[1] / "server" / "nginx.conf"

LOCATION_RE = re.compile(r"location\s+[^{\n]+\{")
ERROR_PAGE_RE = re.compile(
    r"^\s*error_page\s+(?P<codes>[0-9 ]+?)\s*=\s*(?P<target>\S+);",
    re.MULTILINE,
)

REBOOT_LOCATION = "location = /api/v2/manager/reboot"


def _read_config() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _blocks(config: str) -> dict[str, str]:
    """Map every ``location ... {`` header to its brace-balanced body."""
    blocks: dict[str, str] = {}
    for match in LOCATION_RE.finditer(config):
        open_brace = config.index("{", match.end() - 1)
        depth = 0
        index = open_brace
        while index < len(config):
            if config[index] == "{":
                depth += 1
            elif config[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        header = match.group(0)[:-1].strip()
        blocks[header] = config[open_brace + 1 : index]
    return blocks


WORKFLOW_LOCATION = "location ~ ^/api/userdata/workflows/(.+)$"
WORKFLOW_MAP = "map $request_uri $workflow_proxy_path"


def test_workflow_proxy_handles_nested_paths_and_preserves_queries() -> None:
    config = _read_config()
    blocks = _blocks(config)
    workflow = blocks[WORKFLOW_LOCATION]
    route = re.compile(r"^/api/userdata/workflows/(.+)$")

    assert WORKFLOW_MAP in config
    assert route.fullmatch("/api/userdata/workflows/top.json")
    assert route.fullmatch("/api/userdata/workflows/curated/nested.json")
    assert "proxy_pass http://comfyui$workflow_proxy_path$is_args$args;" in workflow
    assert "%2F" in config
    assert "workflow_part10" in config
    assert "workflow_part9" in config
    assert "workflow_part6" in config
    assert "workflow_part3" in config
    assert "proxy_set_header Host $host;" in workflow
    assert "proxy_set_header X-Real-IP $remote_addr;" in workflow
    assert "proxy_http_version 1.1;" in workflow
    assert 'proxy_set_header Connection "";' in workflow
    assert config.index(WORKFLOW_LOCATION) < config.index("location / {")


def test_workflow_proxy_does_not_intercept_upstream_errors() -> None:
    workflow = _blocks(_read_config())[WORKFLOW_LOCATION]

    assert "proxy_intercept_errors" not in workflow

def test_manager_reboot_is_an_exact_location_proxying_to_comfyui() -> None:
    reboot = _blocks(_read_config())[REBOOT_LOCATION]

    assert "proxy_pass http://comfyui;" in reboot
    assert "proxy_http_version 1.1;" in reboot
    assert "proxy_set_header Host $host;" in reboot
    assert "proxy_set_header X-Real-IP $remote_addr;" in reboot
    assert 'proxy_set_header Connection "";' in reboot


def test_only_reboot_maps_upstream_teardown_codes_to_success() -> None:
    reboot = _blocks(_read_config())[REBOOT_LOCATION]

    assert "proxy_intercept_errors on;" in reboot
    matches = ERROR_PAGE_RE.findall(reboot)
    assert len(matches) == 1
    codes, target = matches[0]
    assert set(codes.split()) == {"502", "503", "504"}
    assert target.startswith("/")


def test_reboot_fallback_is_internal_and_returns_accepted_json() -> None:
    config = _read_config()
    reboot = _blocks(config)[REBOOT_LOCATION]
    _, target = ERROR_PAGE_RE.findall(reboot)[0]

    fallback = _blocks(config)[f"location = {target}"]
    assert "internal;" in fallback
    assert "default_type application/json;" in fallback
    assert 'return 202 "{}";' in fallback


def test_upstream_error_interception_stays_scoped_to_reboot() -> None:
    config = _read_config()
    blocks = _blocks(config)

    interceptors = [
        header for header, body in blocks.items() if "proxy_intercept_errors" in body
    ]
    assert interceptors == [REBOOT_LOCATION]
    assert config.count("proxy_intercept_errors") == 1


def test_default_catch_all_still_proxies_every_other_request() -> None:
    config = _read_config()
    catch_all = _blocks(config)["location /"]

    assert "proxy_pass http://comfyui;" in catch_all
    assert "proxy_intercept_errors" not in catch_all


def test_nginx_keeps_the_8000_to_8188_topology() -> None:
    config = _read_config()

    assert "listen 8000;" in config
    assert re.search(r"upstream comfyui \{\s*server 127\.0\.0\.1:8188;", config)
