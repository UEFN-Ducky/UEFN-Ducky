"""Strip personal data from text before it is written to a log or sent with feedback.

Every log line Ducky keeps (Settings → Logs / Errors, crash and plugin error logs) and
every log attached to feedback goes through :func:`scrub`. It removes what identifies a
person or unlocks an account: the user's folders, emails, keys and tokens, passwords,
signed links and network addresses. The rest of the line stays readable for debugging.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REDACTED = "[redacted]"

# Token shapes that are secrets on their own, wherever they appear.
_TOKENS = re.compile(
    r"(?x)"
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{8,}"  # OpenAI / Anthropic keys
    r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"  # Stripe secret / restricted keys
    r"|\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}"  # GitHub tokens
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"  # Slack
    r"|\bAKIA[0-9A-Z]{16}\b"  # AWS access key id
    r"|\bAIza[0-9A-Za-z_-]{35}"  # Google API key
    r"|\bxai-[A-Za-z0-9]{20,}"  # xAI
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"  # JWT
    r"|\b[MN][A-Za-z0-9]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"  # Discord bot token
)
# "Authorization: Bearer x", "Basic x".
_AUTH_SCHEME = re.compile(r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}")
# key=value / key: value / "key": "value" for secret-looking names.
_SECRET_FIELD = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|token|session[_-]?id|"
    r"cookie|authorization|private[_-]?key)[\"']?\s*[:=]\s*)([\"']?)[^\s\"',;&)}\]]{3,}"
)
# Secret query parameters in URLs (presigned links, OAuth codes, tokens).
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|id_token|code|key|api_key|apikey|secret|"
    r"signature|sig|password|auth|session|x-amz-signature|x-amz-credential|"
    r"x-amz-security-token)=)[^&\s#\"']+"
)
# https://user:pass@host
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Other people's / other spellings of user folders: C:\Users\name, /Users/name, /home/name.
_USER_DIR = re.compile(r"(?i)\b([A-Z]:[\\/]+Users[\\/]+|/Users/|/home/)(?!Public\b|Default\b)([^\\/\s\"':]+)")
# IPv4 that is not loopback / unspecified.
_IPV4 = re.compile(r"\b(?!127\.)(?!0\.0\.0\.0\b)(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")


@lru_cache(maxsize=1)
def _home_pattern() -> re.Pattern[str] | None:
    home = str(Path.home()).rstrip("\\/")
    if not home:
        return None
    spellings = {home.replace("\\", "\\\\"), home, home.replace("\\", "/")}
    ordered = sorted((s for s in spellings if s), key=len, reverse=True)
    return re.compile("|".join(re.escape(s) for s in ordered), re.IGNORECASE)


def scrub_home(text: str) -> str:
    """Replace the user's home folder with ``~`` (all three spellings logs produce)."""
    pattern = _home_pattern()
    return pattern.sub("~", text) if pattern and text else text


def scrub(text: str) -> str:
    """``text`` without personal data: folders, emails, keys, tokens, passwords, IPs."""
    if not text:
        return text
    out = scrub_home(str(text))
    out = _URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED + "@", out)
    out = _SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, out)
    out = _TOKENS.sub(REDACTED, out)
    out = _AUTH_SCHEME.sub(lambda m: m.group(1) + " " + REDACTED, out)
    out = _SECRET_FIELD.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    out = _EMAIL.sub("[email]", out)
    out = _USER_DIR.sub(lambda m: m.group(1) + "<user>", out)
    out = _IPV4.sub("[ip]", out)
    return out
