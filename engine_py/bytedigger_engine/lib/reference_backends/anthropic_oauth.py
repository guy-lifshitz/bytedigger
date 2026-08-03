"""Stdlib-only OAuth helper for the Claude subscription (OAuth) token (#852).

Zero non-stdlib deps — safe to import unconditionally from
`pydantic_anthropic.py`'s module top level. Seams `path`/`now`/`http_post`
are injection points so tests never touch real time, network, or the
user's real credentials file (§1i pre-stage, no race).
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

OAUTH_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_MARGIN_SEC = 300
OAUTH_BETA_HEADER = "oauth-2025-04-20"


class OAuthCredentialsError(Exception):
    """Single failure type for all credentials-file / refresh problems."""


def default_credentials_path() -> Path:
    env_path = os.environ.get("CLAUDE_CODE_OAUTH_CREDENTIALS")
    if env_path:
        return Path(env_path)
    # local import — keeps this module stdlib-only at import time
    from bytedigger_engine.config_provider import get_config

    home: Path = get_config().home_root()  # type: ignore[attr-defined]  # home_root is provider-concrete, off the minimal Protocol (see config_provider free functions)
    return home / ".credentials.json"


def _urllib_post_json(url: str, body: dict[str, object]) -> dict[str, object]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - OAuth token endpoint, trusted URL constant
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise OAuthCredentialsError(f"OAuth refresh returned non-object JSON: {parsed!r}")
    return parsed


def get_access_token(
    *,
    path: Path | None = None,
    now: float | None = None,
    http_post: Callable[[str, dict[str, object]], dict[str, object]] | None = None,
) -> str:
    """Return a valid OAuth access token, refreshing (and persisting) if
    the stored token is within REFRESH_MARGIN_SEC of expiry.
    """
    creds_path = path if path is not None else default_credentials_path()

    try:
        raw_text = creds_path.read_text()
        data = json.loads(raw_text)
        creds = data["claudeAiOauth"]
    except Exception as e:
        raise OAuthCredentialsError(
            f"could not read OAuth credentials at {creds_path}: {e}"
        ) from e

    current_time = now if now is not None else time.time()
    expires_at_ms = creds.get("expiresAt")
    remaining = (expires_at_ms / 1000) - current_time if expires_at_ms is not None else -1

    if remaining > REFRESH_MARGIN_SEC:
        return str(creds["accessToken"])

    post = http_post if http_post is not None else _urllib_post_json
    try:
        resp = post(
            OAUTH_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": creds.get("refreshToken"),
                "client_id": OAUTH_CLIENT_ID,
            },
        )
        if not isinstance(resp, dict) or "access_token" not in resp:
            raise OAuthCredentialsError(
                f"OAuth refresh response missing access_token: {resp!r}"
            )
    except OAuthCredentialsError:
        raise
    except Exception as e:
        raise OAuthCredentialsError(f"OAuth token refresh failed: {e}") from e

    new_access_token = str(resp["access_token"])
    new_refresh_token = resp.get("refresh_token", creds.get("refreshToken"))
    expires_in_raw = resp.get("expires_in", 0)
    expires_in = int(expires_in_raw) if isinstance(expires_in_raw, (int, float, str)) else 0
    new_expires_at = int((current_time + expires_in) * 1000)

    data["claudeAiOauth"] = {
        **creds,
        "accessToken": new_access_token,
        "refreshToken": new_refresh_token,
        "expiresAt": new_expires_at,
    }

    creds_dir = creds_path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(creds_dir), prefix=".oauth-creds-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_name, creds_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    return new_access_token
