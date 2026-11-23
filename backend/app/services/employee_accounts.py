"""Shared report population rule matching the employee picker's service filters."""

import re
import unicodedata


def is_employee_account(name, position=""):
    def normalize(value):
        return unicodedata.normalize("NFKC", value or "").strip().lower()

    name = normalize(name)
    return bool(
        name
        and name not in {"oa", "ceshi", "cs", "测试", "管理员", "董事长信箱"}
        and "机器人" not in name
        and not re.fullmatch(r"seeyon\d*", name)
        and not re.fullmatch(r"(?:rpa|robot|bot)(?:[\s_-]*\d+)?", name)
        and not re.fullmatch(r"(?:测试|ceshi|test|cs)(?:账号|账户)?[\s_-]*\d*", name)
        and normalize(position) not in {"虚拟岗位", "rpa机器人"}
    )
