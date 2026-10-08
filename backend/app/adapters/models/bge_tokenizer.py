"""Pinned BAAI tokenizer, loaded locally without network or model weights.

7680 is a conservative CLIENT policy: 8192 documented model tokens minus
512 tokens of headroom (6.25%). It is not a supplier non-truncation guarantee.
The full header and body, including <s>/</s>, are counted without truncation.
"""
import hashlib
from importlib.metadata import version
from pathlib import Path

MODEL_ID = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
TOKENIZER_SHA256 = "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"
TOKENIZER_BYTES = 17098108
TOKENIZERS_VERSION = "0.23.2"
MODEL_TOKEN_LIMIT = 8192
CLIENT_TOKEN_LIMIT = 7680
TOKENIZER_URL = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/tokenizer.json"


class PinnedBgeM3Tokenizer:
    """Exact local counting; provider-internal tokenization remains UNKNOWN."""

    def __init__(self, path):
        raw = Path(path).read_bytes()
        if len(raw) != TOKENIZER_BYTES or hashlib.sha256(raw).hexdigest() != TOKENIZER_SHA256:
            raise ValueError("BGE_TOKENIZER_INTEGRITY_INVALID")
        if version("tokenizers") != TOKENIZERS_VERSION:
            raise ValueError("BGE_TOKENIZER_RUNTIME_UNVERIFIED")
        from tokenizers import Tokenizer
        self._tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        self.identity = f"{MODEL_ID}@{REVISION}:sha256:{TOKENIZER_SHA256}:tokenizers/{TOKENIZERS_VERSION}"
        self.provider_non_truncation = "UNKNOWN"
        self.max_input_tokens = CLIENT_TOKEN_LIMIT
        # The frozen file supplies XLM-R's complete single-sequence processor.
        encoded = self._tokenizer.encode("BGE integrity probe", add_special_tokens=True).ids
        plain = self._tokenizer.encode("BGE integrity probe", add_special_tokens=False).ids
        if encoded != [0, *plain, 2]:
            raise ValueError("BGE_SPECIAL_TOKEN_CONTRACT_INVALID")

    def __call__(self, text):
        return len(self._tokenizer.encode(text, add_special_tokens=True).ids)
