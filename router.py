"""Choose a worker from Gemini's task classification. Preview is entirely local."""

import argparse
import json
import os
import sys
from pathlib import Path
from codex_worker import run_codex
from config import load_config


POOLS = {"neon": "gemini", "carbon": "gemini", "claude": "claude", "gpt": "codex"}
# Order matters. A secondary choice only applies when a quota pool was excluded.
CANDIDATES = {
    "chat": ("neon",),
    "design": ("carbon", "gpt", "claude"),
    "implement": ("gpt", "neon"),
    "review": ("claude", "gpt", "carbon"),
}


def plan(kind: str, override: str | None, avoid: set[str], reason: str, config: dict | None = None) -> dict:
    config = load_config() if config is None else config
    choices = CANDIDATES[kind]
    if override:
        if override not in choices:
            raise ValueError(f"{kind} supports these workers: {', '.join(choices)}")
        choices = (override,)
    for worker in choices:
        pool = POOLS[worker]
        if pool in avoid or worker in config["disabled_workers"]:
            continue
        profile = config["profiles"][worker]["hard" if reason else "standard"]
        return {
            "task": kind,
            "worker": worker,
            "quota_pool": pool,
            "model": profile["model"],
            "effort": profile["effort"],
            "native_slug": profile.get("slug"),
            "escalation_reason": reason or None,
            "access": "workspace-write" if kind == "implement" else "read-only",
            "delivery": "codex-exec" if worker == "gpt" else "antigravity-native",
        }
    raise ValueError("No eligible worker. Check disabled_workers and excluded quota pools.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=CANDIDATES)
    parser.add_argument("--worker", choices=POOLS, help="Pin one eligible worker")
    parser.add_argument("--config", type=Path, help="Machine-local override file (default beside router.py)")
    parser.add_argument("--output", type=Path, help="Save UTF-8 JSON to a new file")
    parser.add_argument("--avoid-pool", action="append", default=[], choices=("gemini", "claude", "codex"))
    upgrade = parser.add_mutually_exclusive_group()
    upgrade.add_argument("--hard-reason", help="A specific difficulty that justifies the top tier")
    upgrade.add_argument("--failed-check", help="A substantive failed acceptance check, with evidence")
    parser.add_argument("--run", action="store_true", help="Execute Codex, or prepare a native handoff")
    parser.add_argument("--brief", type=Path, help="UTF-8 task packet with goal, scope and acceptance checks")
    parser.add_argument("--workspace", type=Path, help="Target project directory")
    args = parser.parse_args()
    reason = args.hard_reason if args.hard_reason is not None else args.failed_check
    if reason is not None and not reason.strip():
        parser.error("Give the concrete reason for escalating.")
    if args.run and (not args.brief or not args.workspace):
        parser.error("--run requires --brief and --workspace")
    output = None
    try:
        if args.run and (os.getenv("GEMINI_ROUTER_NATIVE_CHILD") or os.getenv("GEMINI_ROUTER_CODEX_CHILD")):
            raise ValueError("A delegated worker cannot dispatch another worker.")
        config = load_config(args.config)
        route = plan(args.kind, args.worker, set(args.avoid_pool), (reason or "").strip(), config)
        # Reserve the output before a real worker can consume quota.
        if args.output:
            output = args.output.open("x", encoding="utf-8")
        if not args.run:
            route["status"] = "preview"
        else:
            workspace = args.workspace.resolve(strict=True)
            if not workspace.is_dir():
                raise ValueError("Workspace must be a directory")
            brief = args.brief.read_text(encoding="utf-8-sig").strip()
            if not brief:
                raise ValueError("Brief is empty")
            if route["worker"] == "gpt":
                role = {
                    "implement": "Implement only the scoped change. Run the specified checks and report results.",
                    "design": "Independently design a solution using the supplied constraints. Read-only.",
                    "review": "Independently verify the evidence. Report findings and unverified claims. Read-only.",
                }[args.kind]
                if reason:
                    role += f"\nFocus on this difficulty or failed check: {reason.strip()}"
                route["reply"] = run_codex(
                    f"{role}\nComplete this task directly; do not delegate or run the router.\nWorkspace: {workspace}\n\n{brief}",
                    workspace=workspace,
                    hard=bool(reason),
                    write=args.kind == "implement",
                    model=route["model"],
                    effort=route["effort"],
                    executable=config["codex_path"],
                )
                route["status"] = "worker_replied"
            else:
                route.update(
                    status="handoff_ready",
                    workspace=str(workspace),
                    brief=brief,
                    instruction="Use the selected model in native Antigravity. Return its actual reply to Neon.",
                )
        result = json.dumps(route, ensure_ascii=False, indent=2)
        if output:
            output.write(result + "\n")
        print(result)
    except (OSError, ValueError, RuntimeError) as exc:
        error = json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        if output and output.tell() == 0:
            output.write(error + "\n")
        print(error, file=sys.stderr)
        return 1
    finally:
        if output:
            output.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
