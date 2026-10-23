"""Local, deterministic parsing. No spreadsheet macros, formulas or embedded code execute.

Parsing preserves raw fields and coordinates. It never treats an inferred status,
name match, ambiguous date, or nonstandard table as a confirmed assessment.
"""

import hashlib
import json
import re
import unicodedata
import warnings
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import openpyxl
import xlrd
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table

STATUSES = {"已完成", "未完成", "未到期", "不统计", "关闭"}
REVIEW_HEADERS = (
    "上期会议任务回顾",
    "上次会议核心工作部署",
    "上周例会任务单",
    "上周工作完成",
    "上期工作完成",
)
NEW_HEADERS = ("本次会议核心工作部署", "本周任务单", "本周工作安排", "本次新生成任务")
DECISION_HEADERS = ("本次会议正式决议", "管理要求")


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return unicodedata.normalize("NFKC", str(value)).replace("\u200b", "").strip()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def parse_date(value: Any, year: int | None = None) -> tuple[str | None, str | None]:
    if isinstance(value, datetime):
        return value.date().isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    if isinstance(value, (int, float)) and 20000 < value < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat(), None
    raw = text(value)
    if not raw or raw in {"/", "—", "-"}:
        return None, "缺少日期"
    cleaned = re.sub(r"[_\s]", "", raw)
    if re.search(r"每|长期|持续|待定|TBD|月底|下周|本周|暂|后|前完成", cleaned, re.I):
        return None, "周期或文字日期需确认有效截止日期"
    full = re.findall(r"(?<!\d)(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", cleaned)
    if full:
        try:
            dates = list(dict.fromkeys(date(*map(int, group)).isoformat() for group in full))
        except ValueError:
            return None, "日期不存在"
        remainder = re.sub(r"(?<!\d)20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", "", cleaned)
        if len(dates) != 1 or remainder not in {"", "前", "截止"}:
            return None, "多个日期或日期混有文字，需要确认"
        return dates[0], None
    short = re.fullmatch(r"(\d{1,2})[月./-](\d{1,2})日?", cleaned)
    if short and year:
        try:
            return date(year, *map(int, short.groups())).isoformat(), None
        except ValueError:
            return None, "日期不存在"
    return None, "无法识别日期"


def split_people(value: Any) -> list[str]:
    raw = text(value)
    raw = re.sub(r"[（(](?:线上|线下|现场)[）)]", "", raw)
    return list(dict.fromkeys(x.strip() for x in re.split(r"[/、,，;；\n\s+]+", raw) if x.strip()))


@dataclass
class Issue:
    code: str
    field_path: str
    message: str
    row: int | None = None
    raw: str = ""


@dataclass
class Candidate:
    sheet: str
    payload: dict
    issues: list[Issue] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass
class ParsedFile:
    filename: str
    sha256: str
    candidates: list[Candidate] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    attachment_sheets: list[str] = field(default_factory=list)


def new_payload(title="") -> dict:
    return {
        "title": title,
        "department_name": "",
        "meeting_date": None,
        "raw_meeting_date": "",
        "meeting_time": "",
        "kind": "unknown",
        "location": "",
        "host_name": "",
        "attendee_names": [],
        "absentees": "",
        "decisions": "",
        "recorder_name": "",
        "reviewer_name": "",
        "items": [],
        "historical": True,
    }


def make_item(values: dict, section: str, row: int, sheet: str, year: int | None):
    due, error = parse_date(values.get("due"), year)
    status = text(values.get("status"))
    item = {
        "id": digest([sheet, row])[:32],
        "section": section,
        "category": text(values.get("category")),
        "content": text(values.get("content")),
        "owner_names": split_people(values.get("owners")),
        "raw_owners": text(values.get("owners")),
        "due_date": due,
        "raw_due_date": text(values.get("due")),
        "status": status if status in STATUSES else None,
        "raw_status": status,
        "verifier_name": text(values.get("verifier")),
        "notes": text(values.get("notes")),
        "deliverable": text(values.get("deliverable")),
        "source_row": row,
        "source_sheet": sheet,
    }
    issues = []
    if section == "unknown":
        issues.append(Issue("section_unknown", "section", "请确认该任务属于上期回顾、本次安排或附件", row))
    if section != "new" and status not in STATUSES:
        issues.append(Issue("invalid_status", "status", "请选择五种合法状态之一", row, status))
    if error:
        issues.append(Issue("invalid_due_date", "due_date", error, row, item["raw_due_date"]))
    if not item["owner_names"]:
        issues.append(Issue("missing_owners", "owner_names", "缺少责任人", row))
    if not item["content"]:
        issues.append(Issue("missing_content", "content", "工作内容为空，请核对原始行", row))
    return item, issues


def finish_candidate(candidate: Candidate):
    p = candidate.payload
    if not p["meeting_date"]:
        candidate.issues.append(
            Issue("invalid_meeting_date", "meeting_date", "确认实际会议日期", raw=p["raw_meeting_date"])
        )
    if not p["department_name"]:
        candidate.issues.append(Issue("missing_department", "department_name", "确认所属部门"))
    if not p["host_name"]:
        candidate.issues.append(
            Issue("missing_host", "host_name", "确认主持人，未指定验证人的任务将由主持人验证")
        )
    if p["kind"] == "unknown":
        candidate.issues.append(Issue("meeting_kind", "kind", "确认会议类型"))
    for i, item in enumerate(p["items"]):
        if (
            item["section"] != "new"
            and item["status"] == "未到期"
            and p["meeting_date"]
            and item["due_date"]
            and p["meeting_date"] > item["due_date"]
        ):
            candidate.issues.append(
                Issue(
                    "overdue_undue",
                    f"items.{i}.status",
                    "会议日期晚于计划截止日期，状态仍为未到期",
                    item["source_row"],
                    "未到期",
                )
            )


def parse_grid(sheet: str, grid: list[list]) -> Candidate | None:
    top = " ".join(text(v) for row in grid[:10] for v in row)
    is_standard = "会议召开日期" in top or "会议所属部门" in top
    is_special = ("待办清单" in sheet or "事项清单" in top) and "责任人" in top
    if not is_standard and not is_special:
        return None
    p = new_payload()
    candidate = Candidate(sheet, p)
    for row in grid[:3]:
        value = text(row[0]) if row else ""
        if "纪要" in value and "模板" not in value and "填报" not in value:
            p["title"] = value
    if not p["title"]:
        p["title"] = text(grid[0][0]) if grid and grid[0] else sheet
    labels = {
        "会议所属部门": "department_name",
        "会议召开日期": "raw_meeting_date",
        "会议召开时间": "meeting_time",
        "会议地点": "location",
        "主持人": "host_name",
        "参会人员": "attendee_names",
        "缺席人员及原因": "absentees",
    }
    for row in grid[:10]:
        for col, val in enumerate(row):
            label = text(val)
            for key, target in labels.items():
                if label.rstrip(":： ") == key:
                    next_values = [v for v in row[col + 1 :] if text(v)]
                    value = next_values[0] if next_values else ""
                    if text(value) in labels or text(value).startswith("会议"):
                        value = ""
                    p[target] = (
                        split_people(re.sub(r"[（(]?全员姓名逐一列明[）)]?[:：]?", "", text(value)))
                        if target == "attendee_names"
                        else text(value)
                    )
                    if target == "raw_meeting_date":
                        p["meeting_date"], _ = parse_date(value)
                elif label.startswith(key + ":") or label.startswith(key + "："):
                    p[target] = label[len(key) + 1 :].strip()
            if re.search(r"[☑√R]\s*部门周例会", label):
                p["kind"] = "department"
            elif re.search(r"[☑√R]\s*专项", label):
                p["kind"] = "special"
    year = int(p["meeting_date"][:4]) if p["meeting_date"] else None
    section = "unknown"
    columns = {}
    for rn, row in enumerate(grid, 1):
        vals = [text(v) for v in row]
        # Section titles occupy the first merged cell; task text can contain these words too.
        heading = re.sub(r"\s", "", vals[0]) if vals else ""
        if any(heading.startswith(x) for x in REVIEW_HEADERS):
            section = "review"
            columns = {}
            continue
        if any(heading.startswith(x) for x in NEW_HEADERS):
            section = "new"
            columns = {}
            continue
        if any(heading.startswith(x) for x in DECISION_HEADERS):
            section = "decision"
            columns = {}
            continue
        if heading.startswith("本次会议任务回顾"):
            section = "unknown"
            columns = {}
            continue
        if "责任人" in vals and ("序号" in vals or "事项" in vals):
            columns = {"owners": vals.index("责任人")}
            for j, val in enumerate(vals):
                if val == "序号":
                    columns["sequence"] = j
                elif any(x in val for x in ["计划完成时间", "关闭时间", "完成时间"]):
                    columns["due"] = j
                elif any(x in val for x in ["完成结果", "完成情况"]):
                    columns["status"] = j
                elif val in ["备注", "说明"]:
                    columns["notes"] = j
                elif val in ["验证人", "验收人"]:
                    columns["verifier"] = j
                elif val in ["输出物", "交付物"]:
                    columns["deliverable"] = j
                elif any(x in val for x in ["具体工作", "工作内容", "事项", "任务"]):
                    columns["content"] = j
            continue
        if columns and re.fullmatch(r"\d+(?:\.0)?", vals[columns.get("sequence", 0)] or ""):
            if not any(v for j, v in enumerate(vals) if j != columns.get("sequence", 0)):
                continue
            if section == "decision":
                section = "unknown"
            values = {k: row[j] if j < len(row) else None for k, j in columns.items()}
            start, stop = columns.get("content", 1), columns["owners"]
            contents = list(dict.fromkeys(text(v) for v in row[start:stop] if text(v)))
            values["content"] = contents[-1] if contents else ""
            values["category"] = " / ".join(contents[:-1])
            item, issues = make_item(values, section, rn, sheet, year)
            idx = len(p["items"])
            for issue in issues:
                issue.field_path = f"items.{idx}.{issue.field_path}"
            candidate.issues.extend(issues)
            p["items"].append(item)
        elif section == "decision" and any(vals) and not any("审核" in x or "编制" in x for x in vals):
            p["decisions"] += "\n" + " ".join(dict.fromkeys(x for x in vals if x))
        for value in vals:
            for label, target in [("审核", "reviewer_name"), ("编制", "recorder_name")]:
                if value.startswith(label + ":") or value.startswith(label + "："):
                    p[target] = value[3:].strip()
    finish_candidate(candidate)
    return candidate


def check_zip(path: Path):
    with ZipFile(path) as z:
        if len(z.infolist()) > 10000 or sum(x.file_size for x in z.infolist()) > 250 * 1024 * 1024:
            raise ValueError("压缩文件展开后过大，停止解析")


def parse_docx(path: Path, result: ParsedFile):
    check_zip(path)
    doc = Document(path)
    p = new_payload()
    candidate = Candidate("正文", p)
    section = "unknown"
    table_index = 0
    for element in doc.element.body:
        if element.tag == qn("w:p"):
            value = "".join(element.itertext())
            # itertext repeats runs on some OOXML proxy versions; use Paragraph's normalized text.
            from docx.text.paragraph import Paragraph

            value = Paragraph(element, doc).text.strip()
            compact = re.sub(r"\s", "", value)
            if not p["title"] and "会议纪要" in compact:
                p["title"] = compact
            if compact.startswith("时间:") or compact.startswith("时间："):
                match = re.search(r"20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?", value)
                p["raw_meeting_date"] = value
                if match:
                    p["meeting_date"], _ = parse_date(match.group())
                p["meeting_time"] = value
            for prefix, key in [("地点", "location"), ("主持", "host_name"), ("记录", "recorder_name")]:
                match = re.match(prefix + r"[:：]\s*(.*)", value)
                if match:
                    p[key] = match[1]
            if value.startswith(("现场参会", "线上参会")):
                p["attendee_names"] += split_people(re.split("[:：]", value, maxsplit=1)[-1])
            if "TOP任务单" in compact:
                section = "unknown"
            elif any(x in compact for x in REVIEW_HEADERS):
                section = "review"
            elif any(x in compact for x in NEW_HEADERS):
                section = "new"
            elif compact == "管理要求":
                section = "decision"
            elif section == "decision" and value:
                p["decisions"] += value + "\n"
        elif element.tag == qn("w:tbl"):
            table_index += 1
            table = Table(element, doc)
            grid = [[c.text for c in row.cells] for row in table.rows]
            if not grid or "责任人" not in grid[0]:
                result.attachment_sheets.append(f"表{table_index}")
                continue
            heads = grid[0]
            cols = {"owners": heads.index("责任人")}
            for i, h in enumerate(heads):
                if h in ["任务", "工作内容"]:
                    cols["content"] = i
                elif h in ["关闭时间", "完成时间"]:
                    cols["due"] = i
                elif h == "完成情况":
                    cols["status"] = i
                elif h == "备注":
                    cols["notes"] = i
            year = int(p["meeting_date"][:4]) if p["meeting_date"] else None
            for rn, row in enumerate(grid[1:], 2):
                if not any(x.strip() for x in row):
                    continue
                item, issues = make_item(
                    {k: row[v] for k, v in cols.items()}, section, rn, f"表{table_index}", year
                )
                idx = len(p["items"])
                for issue in issues:
                    issue.field_path = f"items.{idx}.{issue.field_path}"
                candidate.issues.extend(issues)
                p["items"].append(item)
    finish_candidate(candidate)
    result.candidates.append(candidate)


def parse_file(path: Path) -> ParsedFile:
    content = path.read_bytes()
    result = ParsedFile(path.name, hashlib.sha256(content).hexdigest())
    try:
        if path.suffix.lower() == ".docx":
            parse_docx(path, result)
            return result
        sheets = []
        if content[:2] == b"PK":
            check_zip(path)
            with warnings.catch_warnings(record=True) as caught:
                workbook = openpyxl.load_workbook(path.open("rb"), data_only=True)
            for warning in caught:
                result.diagnostics.append({"code": "format_warning", "message": str(warning.message)})
            for sheet in workbook:
                if sheet.max_row > 10000 or sheet.max_column > 300:
                    result.diagnostics.append(
                        {"code": "sheet_limit", "sheet": sheet.title, "message": "表格超出安全解析范围"}
                    )
                    continue
                grid = [list(row) for row in sheet.iter_rows(values_only=True)]
                # Fill merged cells from the actual merge anchor, never forward-fill ordinary blanks.
                for merged in sheet.merged_cells.ranges:
                    anchor = grid[merged.min_row - 1][merged.min_col - 1]
                    # Only vertical category/owner merges affect task rows. Do not duplicate title/header labels.
                    if merged.max_row > merged.min_row:
                        for r in range(merged.min_row, merged.max_row):
                            grid[r][merged.min_col - 1] = anchor
                sheets.append((sheet.title, grid))
            workbook.close()
        elif content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True, formatting_info=True)
            for index in range(workbook.nsheets):
                name = workbook.sheet_names()[index]
                try:
                    sheet = workbook.sheet_by_index(index)
                    if sheet.nrows > 10000 or sheet.ncols > 300:
                        raise ValueError("表格超出安全解析范围")
                    grid = []
                    for r in range(sheet.nrows):
                        row = []
                        for c in range(sheet.ncols):
                            cell = sheet.cell(r, c)
                            row.append(
                                xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                                if cell.ctype == xlrd.XL_CELL_DATE
                                else cell.value
                            )
                        grid.append(row)
                    for r1, r2, c1, c2 in sheet.merged_cells:
                        if r2 > r1 + 1:
                            for r in range(r1 + 1, r2):
                                grid[r][c1] = grid[r1][c1]
                    sheets.append((name, grid))
                except Exception as exc:
                    result.diagnostics.append(
                        {"code": "sheet_parse_error", "sheet": name, "message": str(exc)[:500]}
                    )
            workbook.release_resources()
        else:
            raise ValueError("文件内容不是支持的 XLS、XLSX 或 DOCX")
        for name, grid in sheets:
            try:
                candidate = parse_grid(name, grid)
                if candidate:
                    result.candidates.append(candidate)
                elif any(text(c) for row in grid for c in row):
                    result.attachment_sheets.append(name)
            except Exception as exc:
                result.diagnostics.append(
                    {"code": "sheet_parse_error", "sheet": name, "message": str(exc)[:500]}
                )
        if not result.candidates:
            result.diagnostics.append({"code": "no_meeting", "message": "没有识别到会议区域，请人工指定"})
    except Exception as exc:
        result.diagnostics.append({"code": "file_parse_error", "message": str(exc)[:500]})
    return result
