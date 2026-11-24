"""Initialize machine-local model bindings or inspect readiness without inference."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from codex_worker import EFFORT_ORDER, codex_status
from config import EXAMPLE, LOCAL_CONFIG, ROOT, load_config
from native_handoff import find_agy, list_models, select_model


def inspect(config: dict) -> tuple[dict, dict]:
    """Return diagnostics and unambiguous settings to fill on this machine."""
    patches = {}
    cx = codex_status(config["codex_path"])
    if not config["codex_path"] and cx.get("executable"):
        patches["codex_path"] = cx["executable"]
    catalog = {}
    native = {"path": None, "catalog_size": 0}
    try:
        native["path"] = find_agy(config["agy_path"])
        if not config["agy_path"]:
            patches["agy_path"] = native["path"]
        catalog = list_models(native["path"])
        native["catalog_size"] = len(catalog)
    except (RuntimeError, OSError, ValueError) as exc:
        native["action"] = str(exc)

    checks = {}
    for worker, variants in config["profiles"].items():
        for tier, profile in variants.items():
            label = f"{worker}.{tier}"
            if worker in config["disabled_workers"]:
                checks[label] = "disabled"
                continue
            update = {}
            if worker == "gpt":
                supported = cx["model_efforts"].get(profile["model"], [])
                if profile["effort"] not in supported and tier == "hard" and supported:
                    ranked = [n for n in EFFORT_ORDER if n in supported]
                    if ranked and set(supported) <= set(EFFORT_ORDER):
                        update["effort"] = ranked[-1]
                effort = update.get("effort", profile["effort"])
                if not cx["chatgpt_login"] or cx["missing_flags"]:
                    checks[label] = "check_codex_cli_or_chatgpt_login"
                elif effort not in supported:
                    checks[label] = "confirm_model_and_effort_in_codex"
                else:
                    checks[label] = "available_in_local_catalog"
            else:
                valid = []
                candidates = [profile["slug"]] if profile["slug"] else catalog
                for slug in candidates:
                    try:
                        select_model(profile, slug, catalog)
                        valid.append(slug)
                    except ValueError:
                        continue
                if len(valid) == 1:
                    if not profile["slug"]:
                        update["slug"] = valid[0]
                    checks[label] = "available_in_agy_models"
                else:
                    checks[label] = "check_native_model_picker_or_eligible_slug"
            if update:
                patches.setdefault("profiles", {}).setdefault(worker, {})[tier] = update

    all_bound = all(v in {"available_in_local_catalog", "available_in_agy_models"}
                    for v in checks.values())
    report = {
        "status": "full_preflight" if all_bound else "partial",
        "inference_requests": 0,
        "python": sys.version.split()[0],
        "antigravity": native,
        "codex": {key: cx.get(key) for key in ("available", "chatgpt_login", "executable", "missing_flags")},
        "profiles": checks,
        "suggested_settings": patches,
    }
    return report, patches


def initialize(path: Path) -> dict:
    # Serialize concurrent init processes; doctor only reads the final atomic file.
    lock = path.with_name(path.name + ".lock")
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    temp = None
    try:
        config = load_config(path if path.exists() else EXAMPLE)
        report, patches = inspect(config)
        for key in ("agy_path", "codex_path"):
            if key in patches:
                config[key] = patches[key]
        for worker, variants in patches.get("profiles", {}).items():
            for tier, values in variants.items():
                config["profiles"][worker][tier].update(values)
        data = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8-sig") == data:
            report["config_action"] = "unchanged"
        else:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(data)
            os.replace(temp, path)
            report["config_action"] = "saved"
        report["config"] = str(path)
        return report
    finally:
        if temp:
            temp.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def link_workspace(workspace: Path) -> dict:
    target = workspace.resolve(strict=True)
    if not target.is_dir():
        raise ValueError("Expected an existing project directory")
    if target == ROOT:
        return {"status": "already_linked", "rule": str(ROOT / "GEMINI.md")}
    rules = target / ".agents" / "rules"
    if not rules.resolve().is_relative_to(target):
        raise ValueError("Rules directory resolves outside the target workspace")
    rules.mkdir(parents=True, exist_ok=True)
    rule = rules / "gemini-gpt-router.md"
    text = (
        "---\ntrigger: always_on\n---\n\n"
        f"本项目使用已安装的协作框架，工具目录为 `{ROOT}`。"
        f"主会话分流前读取 `{ROOT / 'GEMINI.md'}`，从该目录运行脚本或使用其绝对路径。"
        "派工时 --workspace 指向当前业务项目。仅实施已获授权的用户任务。\n"
    )
    if rule.is_symlink():
        raise ValueError(f"Rule path is a symlink: {rule}")
    try:
        with rule.open("x", encoding="utf-8") as out:
            out.write(text)
    except FileExistsError:
        if rule.read_text(encoding="utf-8-sig") == text:
            return {"status": "already_linked", "rule": str(rule)}
        raise ValueError(f"Existing rule differs; review and merge it: {rule}")
    return {"status": "linked", "rule": str(rule)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "doctor", "link"))
    parser.add_argument("--workspace", type=Path, help="Existing business workspace to link to this installation")
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    parser.add_argument("--strict", action="store_true", help="Exit 2 unless all eight model profiles are available")
    args = parser.parse_args()
    try:
        if args.action == "link":
            if not args.workspace:
                parser.error("link requires --workspace")
            print(json.dumps(link_workspace(args.workspace), ensure_ascii=False, indent=2))
            return 0
        path = args.config.resolve()
        if args.action == "init":
            report = initialize(path)
        else:
            if path != LOCAL_CONFIG and not path.exists():
                raise FileNotFoundError(path)
            config = load_config(path if path.exists() else EXAMPLE)
            report, patches = inspect(config)
            if patches.get("profiles") and report["status"] == "full_preflight":
                report["status"] = "pending_init"
            report["config"] = str(path) if path.exists() else "template defaults"
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and report["status"] != "full_preflight" else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
