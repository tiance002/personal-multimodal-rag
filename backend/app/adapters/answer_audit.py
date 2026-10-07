"""Private local rejected-answer audit; never logs payload or exports it."""
import json
import os
import uuid
from pathlib import Path


class LocalAnswerAudit:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def save(self, record: dict) -> bool:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f'{uuid.uuid4()}.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return True
