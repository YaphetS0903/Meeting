"""Optional OpenAI-compatible suggestions for ambiguous historical rows.

The provider never writes business data. Suggestions are validated here and must
still be applied and confirmed by an administrator in the validation page.
"""

import json
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Department, User, UserAlias
from app.services.departments import department_paths


class AIRecognitionUnavailable(Exception):
    pass


class AIRecognitionFailed(Exception):
    pass


class OwnerMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    original_name: str = Field(min_length=1, max_length=120)
    user_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)


class ItemSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    section: Literal["review", "new", "attachment"] | None = None
    status: Literal["已完成", "未完成", "未到期", "不统计", "关闭"] | None = None
    due_date: date | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    owner_interpretation: str | None = Field(default=None, max_length=500)
    owner_matches: list[OwnerMatch] = Field(default_factory=list, max_length=20)


class SuggestionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suggestions: list[ItemSuggestion] = Field(max_length=20)


def configured() -> bool:
    settings = get_settings()
    return bool(settings.ai_base_url and settings.ai_api_key and settings.ai_model)


def _json_text(value: str) -> dict:
    text = value.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIRecognitionFailed("模型未返回可识别的 JSON 建议")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIRecognitionFailed("模型返回格式不正确，请重试") from exc


def _provider_request(body: dict) -> dict:
    settings = get_settings()
    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=settings.ai_timeout_seconds) as client:
            response = client.post(
                url,
                headers={"Authorization": "Bearer " + settings.ai_api_key},
                json=body,
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise AIRecognitionFailed("AI 识别超时，请稍后重试或减少本次条目") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise AIRecognitionFailed("AI 服务调用失败，请检查地址、密钥、模型和网络") from exc


def _person_options(db: Session, candidate, originals: list[dict]) -> dict[str, dict[str, list[dict]]]:
    users = list(db.scalars(select(User).where(User.active.is_(True))))
    aliases: dict[str, list[str]] = defaultdict(list)
    for alias in db.scalars(select(UserAlias)):
        aliases[alias.user_id].append(alias.alias)
    paths = department_paths(db.scalars(select(Department)))
    meeting_department_id = getattr(candidate, "corrected", {}).get("department_id")
    meeting_path = paths.get(meeting_department_id, "")
    corrected_items = {
        item.get("id"): item for item in getattr(candidate, "corrected", {}).get("items", [])
    }
    result: dict[str, dict[str, list[dict]]] = {}
    for item in originals:
        selected_ids = set(corrected_items.get(item.get("id"), {}).get("owner_ids", []))
        selected_names = {user.name for user in users if user.id in selected_ids}
        raw_names = [
            str(name).strip()
            for name in item.get("owner_names", [])
            if str(name).strip() and str(name).strip() not in selected_names
        ]
        item_options: dict[str, list[dict]] = {}
        for raw_name in dict.fromkeys(raw_names):
            ranked = []
            for user in users:
                user_path = paths.get(user.department_id, "")
                related_department = bool(
                    meeting_path
                    and user_path
                    and (
                        user_path == meeting_path
                        or user_path.startswith(meeting_path + " / ")
                        or meeting_path.startswith(user_path + " / ")
                    )
                )
                compared_names = [user.name, *aliases.get(user.id, [])]
                similarity = max(
                    SequenceMatcher(None, raw_name, compared).ratio() for compared in compared_names
                )
                score = similarity
                if raw_name[:1] == user.name[:1]:
                    score += 0.12
                if len(raw_name) == len(user.name):
                    score += 0.05
                if related_department:
                    score += 0.24
                ranked.append(
                    (
                        score,
                        {
                            "user_id": user.id,
                            "name": user.name,
                            "department": user_path,
                            "name_similarity": round(similarity, 3),
                            "same_department_branch": related_department,
                        },
                    )
                )
            ranked.sort(key=lambda value: (-value[0], value[1]["name"], value[1]["user_id"]))
            plausible = [
                option
                for score, option in ranked
                if score >= 0.45 or option["name"][:1] == raw_name[:1]
            ]
            item_options[raw_name] = (plausible or [option for _, option in ranked])[:24]
        result[item.get("id")] = item_options
    return result


def suggest(db: Session, candidate, item_ids: list[str], sender=_provider_request) -> dict:
    settings = get_settings()
    if not configured():
        raise AIRecognitionUnavailable("AI 识别尚未配置 Base URL、API Key 和模型")
    requested = set(item_ids)
    originals = [item for item in candidate.original.get("items", []) if item.get("id") in requested]
    if len(originals) != len(requested):
        raise AIRecognitionFailed("部分工作条目不存在或已变化，请刷新后重试")
    person_options = _person_options(db, candidate, originals)
    input_data = {
        "meeting": {
            "title": candidate.original.get("title"),
            "department": candidate.original.get("department_name"),
            "meeting_date": candidate.original.get("meeting_date"),
        },
        "items": [
            {
                "id": item.get("id"),
                "category": item.get("category"),
                "content": item.get("content"),
                "recognized_section": item.get("section"),
                "raw_status": item.get("raw_status") or item.get("status"),
                "raw_due_date": item.get("raw_due_date") or item.get("due_date"),
                "owner_text": item.get("owner_names", []),
                "owner_candidates": [
                    {"original_name": name, "options": options}
                    for name, options in person_options.get(item.get("id"), {}).items()
                ],
                "verifier_text": item.get("verifier_name"),
            }
            for item in originals
        ],
    }
    system = (
        "你是会议纪要历史数据校验助手。只根据提供的原文给出建议，不得编造人员、日期或完成结果。"
        "section 只能是 review（上期完成情况）、new（本次新安排）、attachment（附件或非任务）；"
        "status 只能是已完成、未完成、未到期、不统计、关闭。无法确定的字段必须返回 null，并降低 confidence。"
        "会议日期晚于期限时不能建议未到期。‘完成’通常可建议已完成，但混合描述必须结合全文说明。"
        "owner_candidates 是系统从有效企业微信通讯录筛出的候选。结合错别字、同音字、所属部门和工作内容判断；"
        "owner_matches 中的 user_id 只能从对应 original_name 的 options 选择，不能确定时必须为 null，禁止编造人员。"
        '输出严格 JSON：{"suggestions":[{"id":原id,"section":值或null,'
        '"status":值或null,"due_date":YYYY-MM-DD或null,"confidence":0到1,'
        '"reason":中文依据,"owner_interpretation":责任人原文解释或null,'
        '"owner_matches":[{"original_name":原文姓名,"user_id":候选ID或null,'
        '"confidence":0到1,"reason":中文依据}]}]}。'
    )
    response = sender(
        {
            "model": settings.ai_model,
            "temperature": 0,
            # AgentPlan models enable deep reasoning by default. Historical row
            # normalization is a constrained extraction task, and the default
            # reasoning mode can take longer than the browser/proxy idle limit.
            "thinking": {"type": "disabled"},
            "max_tokens": min(5000, max(1000, len(originals) * 700)),
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": "请识别以下会议条目：\n" + json.dumps(input_data, ensure_ascii=False),
                },
            ],
        }
    )
    try:
        content = response["choices"][0]["message"]["content"]
        envelope = SuggestionEnvelope.model_validate(_json_text(content))
    except (KeyError, IndexError, TypeError, ValidationError) as exc:
        raise AIRecognitionFailed("AI 返回内容不符合校验规则，请重试") from exc
    seen = set()
    for item in envelope.suggestions:
        if item.id not in requested or item.id in seen:
            raise AIRecognitionFailed("AI 返回了未请求或重复的工作条目")
        seen.add(item.id)
        allowed_by_name = person_options.get(item.id, {})
        matched_names = set()
        for match in item.owner_matches:
            if match.original_name not in allowed_by_name or match.original_name in matched_names:
                raise AIRecognitionFailed("AI 返回了未请求或重复的责任人姓名")
            matched_names.add(match.original_name)
            allowed_ids = {option["user_id"] for option in allowed_by_name[match.original_name]}
            if match.user_id is not None and match.user_id not in allowed_ids:
                raise AIRecognitionFailed("AI 返回了候选名单之外的人员")
    serialized = []
    for item in envelope.suggestions:
        value = item.model_dump(mode="json")
        options_by_name = person_options.get(item.id, {})
        for match in value["owner_matches"]:
            selected = next(
                (
                    option
                    for option in options_by_name.get(match["original_name"], [])
                    if option["user_id"] == match["user_id"]
                ),
                None,
            )
            if selected:
                match["name"] = selected["name"]
                match["department"] = selected["department"]
        serialized.append(value)
    return {
        "model": settings.ai_model,
        "suggestions": serialized,
    }
