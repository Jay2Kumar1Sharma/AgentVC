"""Secret scrubber (plan §6.2, G6): redact known secret formats before anything is stored.

Removes known token formats, assigned secrets (`PASSWORD=...`, `api_key: ...`), bearer tokens,
private keys and email addresses; values under secret-looking keys are dropped. Optionally
replaces the user's home folder with `~` and the user name with `<user>`.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-ant-[\w-]{20,}",
        r"\bsk-(?:proj-|live_|test_)?[A-Za-z0-9_-]{20,}",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_pat_[A-Za-z0-9_]{30,}",
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{35}",
        r"\bglpat-[A-Za-z0-9_-]{20,}",
        r"\bnpm_[A-Za-z0-9]{36}",
        r"\bpypi-[A-Za-z0-9_-]{30,}",
        r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}",
        r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}",
        r"\bya29\.[\w-]{20,}",
        r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
        r"\bhf_[A-Za-z0-9]{30,}",
    )
]
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_SECRET_NAME = (
    r"[\w.-]*(?:api[_-]?key|token|secret|password|passwd|credential|access[_-]?key"
    r"|account[_-]?key|sharedaccesssignature)[\w.-]*"
)
_ASSIGNED = re.compile(rf"(?i)\b({_SECRET_NAME})(\s*[:=]\s*)(['\"]?)([^\s'\"]{{6,}})\3")
_SECRET_KEY = re.compile(rf"(?i)^{_SECRET_NAME}$")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scrub_text(text: str, *, home: str | None, user: str | None) -> str:
    """Remove secrets and personal identifiers from one string."""
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = _BEARER.sub(r"\1 [REDACTED]", text)
    text = _ASSIGNED.sub(r"\1\2\3[REDACTED]\3", text)
    text = _URL_CREDENTIALS.sub(r"\1\2:[REDACTED]@", text)
    text = _EMAIL.sub("<email>", text)
    if home and len(home) > 3:
        variants = {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}
        if drive := re.match(r"([A-Za-z]):[\\/](.*)", home):  # Git Bash form: /c/Users/name
            variants.add(f"/{drive.group(1).lower()}/" + drive.group(2).replace("\\", "/"))
        for variant in variants:
            text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE)
    if user and len(user) >= 3:
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text)
    return text


def scrub_value(value: Any, *, home: str | None, user: str | None) -> Any:
    """Scrub every string in a JSON value; values under secret-looking keys are dropped."""
    if isinstance(value, str):
        return scrub_text(value, home=home, user=user)
    if isinstance(value, list):
        return [scrub_value(v, home=home, user=user) for v in value]
    if isinstance(value, dict):
        return {
            scrub_text(str(k), home=home, user=user): (
                "[REDACTED]"
                if isinstance(v, str) and _SECRET_KEY.match(str(k))
                else scrub_value(v, home=home, user=user)
            )
            for k, v in value.items()
        }
    return value
