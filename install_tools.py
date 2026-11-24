"""Install missing vendor CLIs for the current user. Run this on the target machine."""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

from codex_worker import find_codex, _stop_process
from config import load_config
from native_handoff import find_agy


DOCS = {
    "agy": "https://antigravity.google/docs/getting-started?tab=cli",
    "codex": "https://developers.openai.com/codex/cli/",
}
INSTALL_BASE = {
    "agy": "https://antigravity.google/cli/install",
    "codex": "https://chatgpt.com/codex/install",
}


def locate(tool, override):
    finder = find_agy if tool == "agy" else find_codex
    try:
        return finder(override)
    except RuntimeError:
        if override:
            # A broken configured path should not trigger a duplicate install.
            try:
                return finder()
            except RuntimeError:
                pass
        return None


def installer_command(tool, system, directory):
    if system == "Windows":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not shell:
            raise RuntimeError("需要 Windows PowerShell 或 PowerShell 7。")
        file = directory / "install.ps1"
        return INSTALL_BASE[tool] + ".ps1", file, [
            shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(file)]
    if system not in {"Darwin", "Linux"}:
        raise RuntimeError(f"不支持此系统的自动安装：{system}")
    interpreter = "bash" if tool == "agy" else "sh"
    shell = shutil.which(interpreter)
    if not shell:
        raise RuntimeError(f"需要 {interpreter} 运行发行方安装脚本。")
    file = directory / "install.sh"
    return INSTALL_BASE[tool] + ".sh", file, [shell, str(file)]


def install(tool, override=None):
    existing = locate(tool, override)
    if existing:
        print(f"{tool} 已存在，跳过安装。{existing}")
        return
    with tempfile.TemporaryDirectory(prefix=f"{tool}-installer-") as temp:
        url, script, command = installer_command(tool, platform.system(), Path(temp))
        print(f"正在下载 {tool} 官方安装器 {url}", flush=True)
        with urlopen(url, timeout=45) as response:
            if urlsplit(response.geturl()).scheme != "https":
                raise RuntimeError("下载发生非 HTTPS 跳转。")
            contents = response.read(2 * 1024 * 1024 + 1)
        if (not contents.strip() or len(contents) > 2 * 1024 * 1024
                or contents.lstrip().lower().startswith((b"<!doctype html", b"<html"))):
            raise RuntimeError("下载内容不是预期的安装脚本。")
        script.write_bytes(contents)
        # Keep OS, proxy, TLS and destination settings. Do not forward API keys.
        allowed = {
            "PATH", "PATHEXT", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG",
            "USERPROFILE", "USERNAME", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT",
            "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOMEDRIVE", "HOMEPATH",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
            "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
            "PSMODULEPATH", "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        if tool == "codex":
            env["CODEX_NON_INTERACTIVE"] = "1"
        process = subprocess.Popen(command, cwd=temp, stdin=subprocess.DEVNULL,
                                   env=env, start_new_session=os.name != "nt")
        try:
            status = process.wait(timeout=900)
        except subprocess.TimeoutExpired as exc:
            _stop_process(process)
            raise RuntimeError("安装超过 15 分钟，已停止。") from exc
        except BaseException:
            _stop_process(process)
            raise
        if status:
            raise RuntimeError(f"官方安装器退出码为 {status}，请查看上面的输出。")
    binary = locate(tool, None)
    if not binary:
        raise RuntimeError("安装器已退出，仍未找到程序。请重新打开终端并核对 PATH。")
    version = subprocess.run([binary, "--version"], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=20)
    if version.returncode:
        raise RuntimeError("程序已落盘，运行 --version 失败。")
    print(f"{tool} 安装通过。{binary}")
    if override:
        print(f"请将 router.local.json 中的 {tool}_path 更新为上面的路径。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=["agy", "codex", "all"])
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except (ValueError, OSError) as exc:
        print(f"读取路由配置失败。{exc}", file=sys.stderr)
        return 1
    failed = False
    for tool in (["agy", "codex"] if args.tool == "all" else [args.tool]):
        try:
            install(tool, config[f"{tool}_path"])
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"{tool} 安装未完成。{exc}\n官方安装说明 {DOCS[tool]}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
