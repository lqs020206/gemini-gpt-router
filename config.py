"""Machine-local settings. Resolve configuration alongside the installed source."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLE = ROOT / "router.example.json"
LOCAL_CONFIG = ROOT / "router.local.json"


def _merge(base: dict, changes: dict, prefix: str = "") -> None:
    if not isinstance(changes, dict):
        raise ValueError(f"{prefix or 'configuration'} must be an object")
    for key, value in changes.items():
        if key not in base:
            raise ValueError(f"Unknown setting: {prefix}{key}")
        if isinstance(base[key], dict):
            _merge(base[key], value, f"{prefix}{key}.")
        else:
            base[key] = value


def load_config(path: Path | None = None) -> dict:
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if path is not None or LOCAL_CONFIG.exists():
        local = Path(path) if path is not None else LOCAL_CONFIG
        _merge(config, json.loads(local.read_text(encoding="utf-8-sig")))
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported schema_version")
    disabled = config["disabled_workers"]
    if not isinstance(disabled, list) or any(
        not isinstance(w, str) or w not in config["profiles"] for w in disabled
    ):
        raise ValueError("disabled_workers must list known worker names")
    for key in ("codex_path", "agy_path"):
        path_value = config[key]
        if path_value is not None and (not isinstance(path_value, str) or not Path(path_value).is_absolute()):
            raise ValueError(f"{key} must be null or an absolute executable path")
    for variants in config["profiles"].values():
        for variant in variants.values():
            for key, value in variant.items():
                if value is None and key == "slug":
                    continue
                if not isinstance(value, str) or not value.strip() or value.startswith("-"):
                    raise ValueError(f"Invalid model profile field: {key}")
    return config
