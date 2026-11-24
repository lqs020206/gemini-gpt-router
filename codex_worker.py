"""One-shot Codex worker using the existing ChatGPT login."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile


class CodexWorkerError(RuntimeError):
    """The requested job has no verified final response."""


STANDARD_MODEL = "gpt-6.2-sol"
HARD_MODEL = "gpt-6.2-astra"
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
REQUIRED_FLAGS = {"--config", "--model", "--sandbox", "--output-last-message"}


def find_codex(executable=None):
    if executable is not None:
        requested = os.fspath(Path(executable).expanduser())
        found = shutil.which(requested)
        if not found:
            raise CodexWorkerError("指定的 Codex 可执行文件不存在或没有执行权限。")
        if os.name == "nt" and Path(found).suffix.casefold() != ".exe":
            raise CodexWorkerError("Windows 请指定 Codex 原生 .exe 的完整路径。")
        return os.path.abspath(found)
    if os.name == "nt":
        # Global npm prefixes and the native package layout have both varied.
        arch = "arm64" if platform.machine().lower() in {"aarch64", "arm64"} else "x64"
        shim = shutil.which("codex.cmd")
        prefixes = [Path(shim).parent] if shim else []
        if os.environ.get("APPDATA"):
            prefixes.append(Path(os.environ["APPDATA"]) / "npm")
        for prefix in dict.fromkeys(prefixes):
            codex = prefix / "node_modules/@openai/codex"
            nested = codex / "node_modules/@openai" / f"codex-win32-{arch}"
            for package in (nested, codex):
                for folder in ("bin", "codex"):
                    for binary in sorted(package.glob(f"vendor/*/{folder}/codex.exe")):
                        if binary.is_file():
                            return str(binary.resolve())
    binary = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if binary:
        return os.path.abspath(binary)
    # A fresh standalone install may have updated the user's next shell only.
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        standalone = Path(local) / "Programs/OpenAI/Codex/bin/codex.exe" if local else None
    else:
        standalone = Path.home() / ".local/bin/codex"
    if standalone and standalone.is_file() and os.access(standalone, os.X_OK):
        return str(standalone.absolute())
    raise CodexWorkerError("未找到 Codex CLI 可执行文件。请检查 Codex 的安装和 PATH。")


def _environment():
    skip = {"CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CHATGPT_BASE_URL",
            "CODEX_CHATGPT_BASE_URL", "GEMINI_API_KEY", "GOOGLE_API_KEY"}
    return {
        name: value for name, value in os.environ.items()
        if name.upper() not in skip and not name.upper().startswith(("OPENAI_", "ANTHROPIC_"))
    }


def _catalog_efforts():
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        cache = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    efforts = {}
    if isinstance(cache, dict) and isinstance(cache.get("models"), list):
        for item in cache["models"]:
            if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                continue
            levels = item.get("supported_reasoning_levels")
            if isinstance(levels, list):
                efforts[item["slug"]] = [level["effort"] for level in levels
                                         if isinstance(level, dict) and isinstance(level.get("effort"), str)]
    return efforts


def _probe(binary, *args):
    probe = subprocess.run(
        [binary, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=15,
        env=_environment(),
    )
    return probe.stdout if probe.returncode == 0 else ""


def codex_status(executable=None) -> dict:
    """Inspect CLI help, login status and the local model catalog without inference."""
    result = {"available": False, "chatgpt_login": False, "executable": None,
              "standard_model": STANDARD_MODEL, "hard_model": HARD_MODEL,
              "capabilities": [], "missing_flags": sorted(REQUIRED_FLAGS),
              "model_efforts": _catalog_efforts()}
    try:
        binary = find_codex(executable)
        result.update(available=True, executable=binary)
        login = _probe(binary, "login", "status")
        result["chatgpt_login"] = any(
            re.fullmatch(r"logged in using chatgpt(?:\s+\([^)]*\))?", line.strip(), re.I)
            for line in login.splitlines())
        # Only recognize documented options on help lines, never example text.
        flags = set(re.findall(r"^\s*(?:-\w,?\s+)?(--[a-z][a-z0-9-]*)",
                               _probe(binary, "exec", "--help"), re.M))
        if re.search(r"^\s*--no-daemon\b", _probe(binary, "--help"), re.M):
            flags.add("--no-daemon")
        result["capabilities"] = sorted(flags & (REQUIRED_FLAGS | {
            "--no-daemon", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check"}))
        result["missing_flags"] = sorted(REQUIRED_FLAGS - flags)
    except CodexWorkerError as exc:
        result["error"] = str(exc)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = type(exc).__name__
    return result


def _select_model(hard, model, effort, catalog):
    selected = model if model is not None else (HARD_MODEL if hard else STANDARD_MODEL)
    if not isinstance(selected, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*", selected):
        raise CodexWorkerError("Codex 模型 ID 无效。")
    supported = catalog.get(selected, [])
    if model is None and catalog and selected not in catalog:
        raise CodexWorkerError(f"本机模型目录未列出 {selected}，请在路由配置中指定可用模型。")
    if effort is None:
        if hard:
            ranked = [level for level in EFFORT_ORDER if level in supported]
            if not ranked or any(level not in EFFORT_ORDER for level in supported):
                raise CodexWorkerError(f"无法从本机目录确定 {selected} 的最高思考等级，请显式配置。")
            effort = ranked[-1]
        else:
            effort = "medium"
    if not isinstance(effort, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", effort):
        raise CodexWorkerError("Codex 思考等级无效。")
    if supported and effort not in supported:
        raise CodexWorkerError(f"{selected} 的缓存目录支持 {', '.join(supported)}，未列出 {effort}。")
    return selected, effort


def _stop_process(process):
    if process.poll() is None:
        if os.name == "nt":
            killer = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
            try:
                subprocess.run([str(killer), "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait()


def run_codex(prompt: str, *, workspace, hard=False, write=False, timeout=600,
              model=None, effort=None, executable=None) -> str:
    """Run one worker, returning only its saved final answer on successful exit."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise CodexWorkerError("Codex 任务为空。")
    folder = Path(workspace).resolve()
    if not folder.is_dir() or timeout <= 0:
        raise CodexWorkerError("Codex 需要有效的工作目录和超时时间。")
    status = codex_status(executable)
    if not status["available"]:
        raise CodexWorkerError(status.get("error", "未检测到可用的 Codex CLI。"))
    if not status["chatgpt_login"]:
        raise CodexWorkerError("未确认 Codex 已使用 ChatGPT 登录。请在终端运行 codex login。")

    if status["missing_flags"]:
        raise CodexWorkerError(f"Codex exec 缺少必要参数 {', '.join(status['missing_flags'])}。")
    model, effort = _select_model(hard, model, effort, status["model_efforts"])
    flags = status["capabilities"]
    with tempfile.TemporaryDirectory(prefix="gemini-codex-") as temp:
        answer = Path(temp) / "answer.txt"
        command = [status["executable"]]
        if "--no-daemon" in flags:
            command.append("--no-daemon")
        command.append("exec")
        for option in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check"):
            if option in flags:
                command.append(option)
        command.extend([
            "--config", 'model_provider="openai"', "--config", 'forced_login_method="chatgpt"',
            "--config", 'approval_policy="never"', "--model", model,
            "--config", 'shell_environment_policy.set.GEMINI_ROUTER_CODEX_CHILD="1"',
            "--config", f'model_reasoning_effort="{effort}"',
            "--sandbox", "workspace-write" if write else "read-only",
            "--output-last-message", str(answer), "-",
        ])
        # CLI diagnostics may echo the input or tool data. Do not forward them.
        with tempfile.TemporaryFile() as diagnostics:
            try:
                worker_env = _environment()
                worker_env["GEMINI_ROUTER_CODEX_CHILD"] = "1"
                process = subprocess.Popen(
                    command, env=worker_env, cwd=folder, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=diagnostics,
                    start_new_session=os.name != "nt",
                )
            except OSError as exc:
                raise CodexWorkerError("Codex 进程启动失败。") from exc
            try:
                process.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _stop_process(process)
                raise CodexWorkerError("Codex 任务超时，未自动重试。") from exc
            except BaseException:
                _stop_process(process)
                raise
            if process.returncode:
                diagnostics.seek(0, os.SEEK_END)
                diagnostics.seek(max(0, diagnostics.tell() - 8192))
                tail = diagnostics.read().lower()
                if any(word in tail for word in (b"rate limit", b"usage limit", b"quota exceeded")):
                    raise CodexWorkerError("Codex 报告额度或速率受限，未自动重试。")
                raise CodexWorkerError(f"Codex 退出码 {process.returncode}，未确认任务完成。")
        try:
            result = answer.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise CodexWorkerError("Codex 未写出有效的 UTF-8 最终答复。") from exc
        if not result:
            raise CodexWorkerError("Codex 最终答复为空。")
        return result
