"""Run one prepared handoff directly with the official, signed-in Antigravity CLI."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


LEVELS = ("low", "medium", "high", "xhigh", "max")


def normalize(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def find_agy(override: str | None = None) -> str:
    name = "agy.exe" if os.name == "nt" else "agy"
    if override:
        candidates = [shutil.which(str(override))]
    else:
        candidates = [shutil.which(name), Path.home() / ".local" / "bin" / name]
        local = os.environ.get("LOCALAPPDATA")
        if os.name == "nt" and local:
            candidates.append(Path(local) / "agy" / "bin" / name)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            path = Path(candidate).resolve()
            if os.name != "nt" or path.suffix.lower() == ".exe":
                return str(path)
    raise RuntimeError("未找到官方 agy CLI。安装后在终端运行 agy，以个人 Google 账号登录，"
                       "在 /config 关闭 Use AI Credits，再运行 agy models。也可指定 --agy 可执行文件路径。")


def account_environment() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in {
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AGY_ADC_AUTH",
    }}


def list_models(binary: str) -> dict[str, str]:
    try:
        listed = subprocess.run([binary, "models"], stdin=subprocess.DEVNULL, capture_output=True,
                                env=account_environment(), timeout=30)
        listing = listed.stdout.decode("utf-8-sig")
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法读取 agy models。请在终端检查登录和 CLI 版本。") from exc
    if listed.returncode:
        raise RuntimeError("agy models 查询失败。请在终端运行 agy，确认登录后重试。")
    catalog = {}
    for line in listing.splitlines():
        fields = line.strip().split(None, 1)
        if not fields or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)+", fields[0]):
            continue
        catalog[fields[0]] = fields[-1]
    if not catalog:
        raise RuntimeError("agy models 未返回可识别的模型列表。请在终端检查登录和 CLI 版本。")
    return catalog


def select_model(packet: dict, override: str | None, listing: dict[str, str]) -> tuple[str, str, str]:
    family = normalize(packet["model"])
    slug = override or packet.get("native_slug")
    catalog = {}
    for token, label in listing.items():
        parts = (normalize(token), normalize(label))
        if not any(part == family or part.startswith(family + "-") for part in parts):
            continue
        levels = [level for level in LEVELS if any(part.endswith("-" + level) for part in parts)]
        catalog[token] = (label, levels[0] if len(levels) == 1 else "native")
    if not catalog:
        raise ValueError(f"当前 agy models 没有列出 {packet['model']}。请核对登录账号及模型权限。")
    if slug not in catalog:
        raise ValueError("请用 --model 指定同型号的账户模型 ID。可选项为 " + ", ".join(catalog))
    label, selected_level = catalog[slug]
    expected = packet["effort"]
    available = [level for level in LEVELS if any(row[1] == level for row in catalog.values())]
    if expected == "highest offered":
        expected = available[-1] if available else "native"
    elif expected == "medium if offered":
        expected = "medium" if "medium" in available else "native"
    if selected_level != expected:
        raise ValueError(f"交接单要求 {expected}，所选模型档位为 {selected_level}。请查看 agy models。")
    return slug, label, selected_level


def read_handoff(path: Path) -> dict:
    packet = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(packet, dict) or (
        packet.get("status"), packet.get("delivery")
    ) != ("handoff_ready", "antigravity-native"):
        raise ValueError("需要 router.py --run --output 生成的 Antigravity 原生交接单。")
    required = ("worker", "access", "model", "effort", "workspace", "brief", "task")
    if any(not isinstance(packet.get(k), str) or not packet[k].strip() for k in required):
        raise ValueError("交接单缺少工作区、模型、档位或任务正文。")
    if packet["worker"] not in {"neon", "carbon", "claude"}:
        raise ValueError("该执行者不是 Antigravity 原生模型。")
    if packet.get("native_slug") is not None and not isinstance(packet["native_slug"], str):
        raise ValueError("native_slug 应为模型 ID 字符串或 null。")
    if packet["access"] not in {"workspace-write", "read-only"}:
        raise ValueError("交接单包含未知的工作区访问模式。")
    return packet


def run_handoff(packet: dict, override: str | None, executable: str | None) -> dict:
    if os.environ.get("GEMINI_ROUTER_NATIVE_CHILD") or os.environ.get("GEMINI_ROUTER_CODEX_CHILD"):
        raise ValueError("此任务已在独立工作会话内执行。")
    workspace = Path(packet["workspace"]).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("交接工作区应为现有目录。")
    binary = find_agy(executable)
    env = account_environment()
    env["GEMINI_ROUTER_NATIVE_CHILD"] = "1"
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict) or settings.get("modelProvider") or (
            settings.get("useG1Credits", False) not in (False, "off", "false")
        ) or settings.get("gcp"):
            raise ValueError("请在官方 agy 核对个人账号登录并关闭 Use AI Credits。")
    slug, label, level = select_model(packet, override, list_models(binary))
    command = [binary, "--model", slug, "--input-format", "stream-json",
               "--output-format", "stream-json", "--disable-slash-commands", "--print-timeout", "10m"]
    if packet["access"] == "read-only":
        command += ["--mode", "plan"]
    prompt = (f"独立完成本次 {packet['task']}。访问范围 {packet['access']}。直接给主会话提供结果、"
              "证据及未能验证的项目。请勿继续委派、启动其他模型或调用分流脚本。\n\n"
              + packet["brief"])
    if packet.get("escalation_reason"):
        prompt += "\n\n需要重点处理的难点或失败记录：" + str(packet["escalation_reason"])
    event = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
    completed = subprocess.run(command, input=event.encode("utf-8"), capture_output=True,
                               env=env, cwd=workspace, timeout=630)
    if completed.returncode:
        raise ValueError(f"agy 退出码 {completed.returncode}。请核对原生 CLI 登录、额度及版本。")
    init_model = None
    result = None
    for line in completed.stdout.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("agy 返回了无效的流事件。")
        if row.get("event") == "init" and isinstance(row.get("init"), dict):
            init_model = row["init"].get("model")
        if row.get("event") == "result":
            if result is not None:
                raise ValueError("期望本次单轮任务的唯一回执。")
            result = row.get("result")
    if init_model is not None and init_model not in (slug, label):
        raise ValueError("agy 报告的模型与本次指定型号不符。")
    if not isinstance(result, dict) or result.get("status") != "SUCCESS":
        raise ValueError("agy 未返回本轮 SUCCESS 回执。")
    reply = result.get("response")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("agy 返回了空回复。")
    # Some official stream versions omit init.model, including the public example.
    evidence = "reported_by_cli" if init_model is not None else "pinned_cli_argument"
    return {"status": "worker_replied", "worker": packet["worker"], "model": slug, "model_evidence": evidence,
            "effort": level, "reply": reply, "usage": result.get("usage"),
            "conversation_id": result.get("conversation_id")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path, help="UTF-8 JSON from router --run --output")
    parser.add_argument("--model", help="Override with an exact ID shown by agy models")
    parser.add_argument("--agy", help="Path to the official Antigravity CLI executable")
    parser.add_argument("--config", type=Path, help="Machine-local override for agy_path")
    args = parser.parse_args()
    try:
        from config import load_config
        config = load_config(args.config)
        packet = read_handoff(args.handoff)
        if packet["worker"] in config["disabled_workers"]:
            raise ValueError(f"当前配置已禁用 {packet['worker']}。请重新选择派工路线。")
        reply = run_handoff(packet, args.model, args.agy or config["agy_path"])
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
