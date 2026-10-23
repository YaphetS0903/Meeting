import argparse
import getpass
import json
import os
import secrets
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import passwords
from app.models import Department, ImportBatch, ImportCandidate, ImportSource, User, ValidationIssue
from app.services.importing import ingest_file


def main():
    parser = argparse.ArgumentParser(description="会议任务系统管理命令")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap", help="创建首个管理员，不覆盖已有账号")
    bootstrap.add_argument("--username", default="admin")
    bootstrap.add_argument("--name", default="系统管理员")
    bootstrap.add_argument(
        "--generate-password", action="store_true", help="生成随机密码，保存为本地仅本人可读文件"
    )
    archive = sub.add_parser("import-directory", help="只导入到校验暂存区，不发送通知")
    archive.add_argument("directory", type=Path)
    archive.add_argument("--month", default="2026-08")
    archive.add_argument("--username", default="admin")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.command == "bootstrap":
            if db.scalar(select(User).where(User.username == args.username)):
                parser.error("账号已存在，不会覆盖")
            password = (
                secrets.token_urlsafe(24)
                if args.generate_password
                else getpass.getpass("管理员密码（至少 12 位）: ")
            )
            if len(password) < 12:
                parser.error("密码长度不足")
            if not args.generate_password and getpass.getpass("再次输入: ") != password:
                parser.error("两次密码不一致")
            dep = Department(name="系统管理")
            db.add(dep)
            db.flush()
            db.add(
                User(
                    name=args.name,
                    username=args.username,
                    password_hash=passwords.hash(password),
                    is_admin=True,
                    department_id=dep.id,
                )
            )
            if args.generate_password:
                folder = get_settings().storage_dir
                folder.mkdir(parents=True, exist_ok=True)
                location = folder / "local-admin-credentials.txt"
                fd = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(
                        f"账号：{args.username}\n密码：{password}\n仅用于此本地实例，请勿提交或共享。\n"
                    )
                print(f"随机密码已保存至 {location}")
            print("管理员已创建")
        else:
            import calendar

            user = db.scalar(select(User).where(User.username == args.username, User.is_admin.is_(True)))
            if not user:
                parser.error("请先 bootstrap 创建管理员")
            year, month = map(int, args.month.split("-"))
            batch = ImportBatch(
                name=f"{args.month} 历史会议纪要",
                creator_id=user.id,
                scope_start=date(year, month, 1),
                scope_end=date(year, month, calendar.monthrange(year, month)[1]),
            )
            db.add(batch)
            db.flush()
            files = sorted(
                f for f in args.directory.rglob("*") if f.suffix.lower() in {".xls", ".xlsx", ".docx"}
            )
            for index, path in enumerate(files, 1):
                ingest_file(db, user, batch, path)
                print(f"[{index}/{len(files)}] {path.name}", flush=True)
            candidates = list(
                db.scalars(
                    select(ImportCandidate).join(ImportSource).where(ImportSource.batch_id == batch.id)
                )
            )
            sources = list(db.scalars(select(ImportSource).where(ImportSource.batch_id == batch.id)))
            issues = list(
                db.scalars(
                    select(ValidationIssue).join(ImportSource).where(ImportSource.batch_id == batch.id)
                )
            )
            batch.state = "review"
            batch.summary = {
                "files": len(files),
                "duplicate_files": sum(s.duplicate_of is not None for s in sources),
                "candidates": dict(Counter(c.state for c in candidates)),
                "issues": dict(Counter(i.code for i in issues)),
            }
            print(json.dumps({"batch_id": batch.id, **batch.summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
