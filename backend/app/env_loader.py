"""Backend-only dotenv loader. No interpolation, logging, or frontend export."""
from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NAME = re.compile(r'[A-Z][A-Z0-9_]*\Z')


def load_backend_env(path: Path | None = None) -> tuple[str, ...]:
    path = Path(path) if path is not None else PROJECT_ROOT / '.env'
    if not path.is_file():
        return ()
    parsed = {}
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
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
                'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'COMPOSE_OLLAMA_BASE_URL')
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
    loaded = []
    for name, value in parsed.items():
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return tuple(loaded)
