from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def reference_profile() -> dict[str, Any]:
    path = Path(__file__).parent.parent / "retrieval_profiles" / "v1_reference.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != "retrieval-profile-v1":
        raise ValueError("unsupported retrieval profile")
    parameters = profile["parameters"]
    if set(parameters) != set(profile["parameter_sources"]):
        raise ValueError("every retrieval profile parameter requires provenance")
    return profile
