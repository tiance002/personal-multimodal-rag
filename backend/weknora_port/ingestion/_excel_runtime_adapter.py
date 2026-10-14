"""DocReader DTO bridge only; no alternative spreadsheet parsing algorithm."""
from dataclasses import dataclass,field

@dataclass
class Chunk:
    content: str
    seq: int
    start: int
    end: int

@dataclass
class Document:
    content: str = ''
    chunks: list = field(default_factory=list)
