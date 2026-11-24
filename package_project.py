"""Build a portable source-only ZIP, or verify its extracted file checksums."""

import argparse
import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = (
    ".gitignore", "README.md", "AGENTS.md", "GEMINI.md", "方案与使用.md",
    "router.example.json", "config.py", "router.py", "setup_router.py",
    "codex_worker.py", "native_handoff.py", "install_tools.py", "package_project.py",
    "examples/review.md", "examples/brief-template.md", "examples/workspace/check_fixture.py",
    "tests/test_project.py",
)
MANIFEST = "SHA256SUMS"


def build(path: Path) -> None:
    archive = io.BytesIO()
    records = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in FILES:
            src = ROOT / name
            if src.is_symlink() or not src.resolve().is_relative_to(ROOT):
                raise ValueError(f"Unexpected file location: {name}")
            data = src.read_bytes()
            records.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
            bundle.writestr("gemini_gpt_router/" + name, data)
        bundle.writestr("gemini_gpt_router/" + MANIFEST, "".join(records))
    with path.open("xb") as output:
        output.write(archive.getvalue())
    print(f"{path.resolve()}\nSHA256 {hashlib.sha256(archive.getvalue()).hexdigest()}")


def verify() -> None:
    records = {}
    for line in (ROOT / MANIFEST).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] not in FILES or match[2] in records:
            raise ValueError("Unexpected checksum record")
        records[match[2]] = match[1]
    if set(records) != set(FILES):
        raise ValueError("Incomplete source manifest")
    for name, expected in records.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {name}")
    print(f"{len(records)} project files verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build").add_argument("zip", type=Path)
    sub.add_parser("verify")
    args = parser.parse_args()
    try:
        if args.action == "build":
            build(args.zip)
        else:
            verify()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
