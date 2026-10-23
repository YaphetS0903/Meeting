#!/usr/bin/env python3
"""Package one committed source revision, with commit metadata and file hashes."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "Meeting/"
PRIVATE_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "node_modules", ".npm-cache", "dist", "coverage", "backups",
    "monitor-state", "native-todo", "storage", "8月会议纪要", "交接包",
    "test-results", "playwright-report",
}
PRIVATE_NAMES = {"会议纪要系统企业微信信息.txt", ".envrc", ".DS_Store"}
PRIVATE_SUFFIXES = {".pem", ".key", ".db", ".sqlite", ".sqlite3", ".dump"}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args]).decode().strip()


def private_path(name):
    path = PurePosixPath(name)
    return (
        path.is_absolute()
        or ".." in path.parts
        or bool(set(path.parts) & PRIVATE_DIRS)
        or path.name in PRIVATE_NAMES
        or path.name.startswith("._")
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.name.endswith("-credentials.txt")
        or path.suffix in PRIVATE_SUFFIXES
        or path.suffix == ".pyc"
    )


def add_file(archive, name, content, timestamp):
    entry = ZipInfo(PREFIX + name, timestamp)
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    entry.create_system = 3
    archive.writestr(entry, content)


def main():
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", nargs="?", type=Path,
        default=ROOT / "交接包" / f"Meeting-source-{now:%Y%m%d}.zip",
    )
    args = parser.parse_args()
    if git("status", "--porcelain", "--untracked-files=normal"):
        parser.error("请先提交源码与文档；交接包只包含已提交内容。")
    output = args.output.resolve()
    if output.suffix != ".zip":
        parser.error("输出文件必须使用 .zip 扩展名。")
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".meeting-source-", suffix=".zip", dir=output.parent)
    os.close(handle)
    try:
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=zip", "--prefix=" + PREFIX,
             "--output=" + temporary, commit],
            check=True,
        )
        manifest = {}
        with ZipFile(temporary, "a") as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                relative = entry.filename.removeprefix(PREFIX)
                if not entry.filename.startswith(PREFIX) or private_path(relative):
                    raise RuntimeError("交接包包含禁止的路径：" + relative)
                if relative in {"HANDOFF.json", "SOURCE-MANIFEST.sha256"}:
                    raise RuntimeError("源码占用了交接元数据文件名：" + relative)
                manifest[relative] = hashlib.sha256(archive.read(entry)).hexdigest()
            metadata = {
                "format_version": 1,
                "repository": "https://github.com/YaphetS0903/Meeting",
                "source_commit": commit,
                "source_tree": tree,
                "created_at": now.isoformat(timespec="seconds"),
                "source_file_count": len(manifest),
                "contents": "Committed source, migrations, tests, documentation, fonts and licenses.",
                "excluded": "Private configuration, credentials, authorization cache, business data and dependencies.",
            }
            data = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
            add_file(archive, "HANDOFF.json", data, now.timetuple()[:6])
            manifest["HANDOFF.json"] = hashlib.sha256(data).hexdigest()
            lines = "".join(f"{digest}  {name}\n" for name, digest in sorted(manifest.items()))
            add_file(archive, "SOURCE-MANIFEST.sha256", lines.encode(), now.timetuple()[:6])
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("ZIP 内容校验失败。")
        digest = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        os.replace(temporary, output)
        checksum = output.with_suffix(output.suffix + ".sha256")
        checksum.write_text(f"{digest}  {output.name}\n")
        print(json.dumps({
            "archive": str(output), "checksum": str(checksum),
            "commit": commit, "source_files": metadata["source_file_count"],
            "bytes": output.stat().st_size, "sha256": digest,
        }, ensure_ascii=False, indent=2))
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
