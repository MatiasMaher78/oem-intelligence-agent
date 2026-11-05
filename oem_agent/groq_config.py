"""Small local .env loader for Groq; no interpolation, networking or diagnostics of values."""
import os
from pathlib import Path
import re

MODEL = "qwen/qwen3.8-27b"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_ALLOWED = {"GROQ_API_KEY", "GROQ_MODEL"}


class GroqConfigError(ValueError):
    """Fixed codes only: file contents and OS exception messages stay private."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_local_env(path=None):
    """Load only two names into this process. Existing environment values win.

    Supported: UTF-8/BOM, blank lines, full-line comments, NAME=value and optional
    matching single/double quotes. No export, interpolation, escapes or commands.
    Parse and validate before changing the process environment. Never writes files.
    """
    path = ENV_PATH if path is None else Path(path)
    values = {}
    present = True
    try:
        with path.open("rb") as stream:
            content = stream.read(16385)
    except FileNotFoundError:
        present = False
        content = b""
    except OSError:
        raise GroqConfigError("local_env_unavailable") from None
    if len(content) > 16384:
        raise GroqConfigError("local_env_too_large")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        raise GroqConfigError("invalid_local_env") from None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise GroqConfigError("invalid_local_env")
        if name not in _ALLOWED:
            continue  # Do not export unrelated credentials, proxy or endpoint overrides.
        if name in values:
            raise GroqConfigError("duplicate_local_env_name")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise GroqConfigError("invalid_local_env")
            value = value[1:-1]
        if any(c in value for c in ("$", "`", "\\", "'", '"')):
            raise GroqConfigError("invalid_local_env")
        values[name] = value
    effective = {name: os.environ.get(name, values.get(name, MODEL if name == "GROQ_MODEL" else ""))
                 for name in _ALLOWED}
    if effective["GROQ_MODEL"] != MODEL:
        raise GroqConfigError("groq_model_not_allowed")
    key = effective["GROQ_API_KEY"]
    if key and re.fullmatch(r"[A-Za-z0-9_-]{1,4096}", key) is None:
        raise GroqConfigError("groq_api_key_invalid")
    for name, value in values.items():
        os.environ.setdefault(name, value)
    return {"dotenv_present": present, "api_key_available": bool(key), "model_supported": True}
