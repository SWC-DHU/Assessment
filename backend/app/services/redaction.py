"""Remove credentials before saving or returning diagnostics."""
import re
from urllib.parse import quote

SECRET_FIELD = re.compile(r"^(authorization|proxy.authorization|api.key|access.token|secret|password|cookie|set.cookie)$", re.I)
ASSIGNMENT = re.compile(r"""(?i)((?:api[_ -]?key|authorization|access[_ -]?token|password)\s*['"]?\s*[:=]\s*['"]?)(?:bearer\s+)?[^\s,'"\]}]+""")
TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-/+=]+|\b(?:sk|sess|ghp)-[a-z0-9_-]{10,}")
URL_AUTH = re.compile(r"(https?://)[^\s/]+@")
URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+")


def redact(value, *secrets):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if SECRET_FIELD.fullmatch(key) else redact(item, *secrets))
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, *secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in {secret for secret in secrets if secret}:
        value = value.replace(secret, "[REDACTED]").replace(quote(secret, safe=""), "[REDACTED]")
    value = ASSIGNMENT.sub(r"\1[REDACTED]", value)
    value = URL_AUTH.sub(r"\1[REDACTED]@", value)
    return TOKEN.sub("[REDACTED]", value)


def safe_endpoint(value: str) -> str:
    return URL_QUERY.sub(r"\1?[REDACTED]", redact(value))
