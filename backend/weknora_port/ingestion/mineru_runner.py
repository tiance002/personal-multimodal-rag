"""Standalone entry in the isolated MinerU environment; no RAG imports."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import multiprocessing
import socket
import sys
from pathlib import Path


def deny_network():
    def denied(*args, **kwargs):
        raise RuntimeError("MINERU_NETWORK_DENIED")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def main():
    deny_network()
    if importlib.metadata.version("mineru") != "4.0.12":
        raise RuntimeError("MINERU_VERSION_UNSUPPORTED")
    source, output, receipt = map(Path, sys.argv[1:4])
    from mineru import parse
    from mineru.kit.common import save_parse_result
    # Basic OCR local small models only. No VLM image analysis/caption step.
    result = parse(source, tier="basic", ocr_mode="ocr", image_analysis=False)
    save_parse_result(result, output, "zip")
    # -I deliberately excludes the script directory from import search paths.
    # Load only this code-owned sibling, without adding the workspace to sys.path.
    spec = importlib.util.spec_from_file_location('weknora_mineru_image_ocr',
        Path(__file__).with_name('mineru_image_ocr.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    output.write_bytes(helper.supplement_archive(output.read_bytes(),
        hashlib.sha256(source.read_bytes()).hexdigest(), helper.local_recognizer()))
    receipt.write_text(json.dumps({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tier": "basic", "ocr_mode": "ocr", "image_analysis": False,
        "network_policy": "deny_all_socket_connect", "mineru_version": "4.0.12"}), encoding="utf-8")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
