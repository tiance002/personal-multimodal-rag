"""Start this worktree; --no-env uses only explicitly supplied process settings."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-env', action='store_true')
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if not args.no_env:
        from backend.app.env_loader import load_backend_env
        load_backend_env()
    from backend.app.config import Settings
    settings = Settings.from_env()
    import uvicorn
    uvicorn.run('backend.app.main:app', host=settings.host, port=settings.port)


if __name__ == '__main__':
    main()
