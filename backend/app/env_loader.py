"""Backend-only dotenv loader. No interpolation, logging, or frontend export."""
from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NAME = re.compile(r'[A-Z][A-Z0-9_]*\Z')
_SF_KEY = 'SILICONFLOW_API_KEY'
_SF_ALIAS = 'RAG_SILICONFLOW_API_KEY'


def load_backend_env(path: Path | None = None) -> tuple[str, ...]:
    """Process canonical > process SF alias > file canonical > file SF alias.

    An explicit empty process value is preserved. Only this exact SF alias is
    normalized; other names keep the existing allowlist and no key is logged.
    """
    path = Path(path) if path is not None else PROJECT_ROOT / '.env'
    parsed = {}
    try:
        for line in (path.read_text(encoding='utf-8-sig').splitlines() if path.is_file() else ()):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].strip()
            name, sep, value = line.partition('=')
            name, value = name.strip(), value.strip()
            if not sep or not _NAME.fullmatch(name):
                raise ValueError()
            allowed = name.startswith(('RAG_', 'OLLAMA_', 'LANGFUSE_', 'POSTGRES_')) or name in (
                'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'COMPOSE_OLLAMA_BASE_URL', _SF_KEY)
            if not allowed:
                continue
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError()
                value = value[1:-1]
            else:
                value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
            parsed[name] = value
    except (OSError, UnicodeError, ValueError):
        raise ValueError('BACKEND_ENV_INVALID') from None
    if _SF_KEY not in os.environ:
        if _SF_ALIAS in os.environ:
            parsed[_SF_KEY] = os.environ[_SF_ALIAS]
        elif _SF_KEY not in parsed and _SF_ALIAS in parsed:
            parsed[_SF_KEY] = parsed[_SF_ALIAS]
    loaded = []
    for name, value in parsed.items():
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return tuple(loaded)
