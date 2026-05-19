from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from collections.abc import Sequence


TOKEN_RE = re.compile(r"\b(?:ak|as)-[A-Za-z0-9_-]+\b")
AUTH_ERROR_MARKERS = (
    "authenticate",
    "authentication",
    "credential",
    "login",
    "profile",
    "token",
)


@dataclasses.dataclass(frozen=True)
class ExpectedModalSecret:
    label: str
    name: str | None
    key: str
    env_var: str


@dataclasses.dataclass(frozen=True)
class ModalAccountStatus:
    status: str
    detail: str
    profile: str | None = None


@dataclasses.dataclass(frozen=True)
class ModalSecretStatus:
    label: str
    name: str | None
    key: str
    status: str
    detail: str


@dataclasses.dataclass(frozen=True)
class ModalStatusSnapshot:
    account: ModalAccountStatus
    secrets: list[ModalSecretStatus]


def sanitize_modal_error(detail: str) -> str:
    return TOKEN_RE.sub("[redacted]", detail)


def modal_auth_error_text(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


def modal_auth_is_missing(snapshot: ModalStatusSnapshot) -> bool:
    return snapshot.account.status == "missing"


def _secret_statuses_from_names(
    expected: Sequence[ExpectedModalSecret],
    secret_names: set[str],
    *,
    account_status: str,
    list_error: str | None = None,
    known_existing: set[str] | None = None,
) -> list[ModalSecretStatus]:
    if known_existing:
        secret_names.update(known_existing)

    statuses: list[ModalSecretStatus] = []
    for spec in expected:
        if spec.name is None:
            status = "disabled"
            detail = f"disabled via {spec.env_var}"
        elif account_status == "missing":
            status = "skipped"
            detail = "blocked by sign-in"
        elif list_error:
            status = "unknown"
            detail = sanitize_modal_error(list_error)
        elif spec.name in secret_names:
            status = "ok"
            detail = f"{spec.name} ({spec.key})"
        else:
            status = "missing"
            detail = f"{spec.name} ({spec.key})"
        statuses.append(
            ModalSecretStatus(
                label=spec.label,
                name=spec.name,
                key=spec.key,
                status=status,
                detail=detail,
            )
        )
    return statuses


def _fresh_status_probe_code() -> str:
    return f"""
import json
import subprocess
import sys

auth_markers = {AUTH_ERROR_MARKERS!r}
result = {{
    "account_status": "unknown",
    "account_detail": "",
    "profile": None,
    "secret_names": [],
    "list_error": None,
}}

try:
    import modal
except Exception as exc:
    result["account_detail"] = str(exc)
    result["list_error"] = str(exc)
    print(json.dumps(result))
    raise SystemExit(0)

try:
    profile_proc = subprocess.run(
        [sys.executable, "-m", "modal", "profile", "current"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if profile_proc.returncode == 0:
        profile = profile_proc.stdout.strip() or None
        result["profile"] = profile
except Exception:
    pass

try:
    result["secret_names"] = [
        secret.name
        for secret in modal.Secret.objects.list()
        if getattr(secret, "name", None)
    ]
except Exception as exc:
    detail = str(exc)
    result["list_error"] = detail
    lowered = detail.lower()
    if any(marker in lowered for marker in auth_markers):
        result["account_status"] = "missing"
        result["account_detail"] = "Token missing. Run `modal setup` to sign in."
    else:
        result["account_status"] = "unknown"
        result["account_detail"] = detail
else:
    result["account_status"] = "ok"
    result["account_detail"] = result["profile"] or "Authenticated"

print(json.dumps(result))
"""


def fresh_modal_status_snapshot(
    expected: Sequence[ExpectedModalSecret],
    *,
    known_existing: set[str] | None = None,
) -> ModalStatusSnapshot:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _fresh_status_probe_code()],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        account = ModalAccountStatus("unknown", sanitize_modal_error(str(exc)))
        secrets = _secret_statuses_from_names(
            expected,
            set(),
            account_status=account.status,
            list_error=account.detail,
            known_existing=known_existing,
        )
        return ModalStatusSnapshot(account=account, secrets=secrets)

    output = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not output:
        detail = (
            result.stderr
            or result.stdout
            or f"status probe exited with code {result.returncode}"
        ).strip()
        detail = sanitize_modal_error(detail)
        status = "missing" if modal_auth_error_text(detail) else "unknown"
        account = ModalAccountStatus(
            status,
            "Token missing. Run `modal setup` to sign in." if status == "missing" else detail,
        )
        secrets = _secret_statuses_from_names(
            expected,
            set(),
            account_status=account.status,
            list_error=detail,
            known_existing=known_existing,
        )
        return ModalStatusSnapshot(account=account, secrets=secrets)

    try:
        parsed = json.loads(output[-1])
    except json.JSONDecodeError:
        account = ModalAccountStatus("unknown", "Could not parse Modal status probe output.")
        secrets = _secret_statuses_from_names(
            expected,
            set(),
            account_status=account.status,
            list_error=account.detail,
            known_existing=known_existing,
        )
        return ModalStatusSnapshot(account=account, secrets=secrets)

    account_status = str(parsed.get("account_status") or "unknown")
    account_detail = sanitize_modal_error(str(parsed.get("account_detail") or ""))
    if account_status == "missing":
        account_detail = "Token missing. Run `modal setup` to sign in."
    profile = parsed.get("profile") if isinstance(parsed.get("profile"), str) else None
    account = ModalAccountStatus(account_status, account_detail, profile=profile)
    secret_names = {name for name in parsed.get("secret_names", []) if isinstance(name, str)}
    list_error = parsed.get("list_error")
    secrets = _secret_statuses_from_names(
        expected,
        secret_names,
        account_status=account.status,
        list_error=sanitize_modal_error(str(list_error)) if list_error else None,
        known_existing=known_existing,
    )
    return ModalStatusSnapshot(account=account, secrets=secrets)
