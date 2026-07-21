"""Conservative caller type and enterprise identity rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

IDENTITY_MAPPING = {
    "fddbr": "法定代表人",
    "cwfzr": "财务负责人",
    "bsry": "办税人员",
    "qt": "其他",
    "zrr": "自然人",
}

ENTERPRISE_IDENTITIES = {"法定代表人", "财务负责人", "办税人员", "其他"}


@dataclass(frozen=True)
class IdentityDecision:
    identity: str
    source: str
    conflict: bool


def map_identity_label(value: object) -> str | None:
    if value is None:
        return None
    return IDENTITY_MAPPING.get(str(value).strip().lower())


def normalize_caller_type(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if rendered in {"个人", "自然人", "个体"}:
        return "个人"
    if rendered in {"企业", "单位", "公司", "组织"}:
        return "企业"
    return None


def infer_enterprise_identity(
    caller_type: str | None, raw_identity: object, transcript: object
) -> str:
    """Prefer explicit transcript evidence, then fall back to a compatible source label."""

    if caller_type == "个人":
        return "不适用"
    if caller_type != "企业":
        return "无法判断"

    text = "" if transcript is None else str(transcript)
    explicit_patterns = (
        ("法定代表人", r"(?:我是|本人是|我作为).{0,8}(?:法定代表人|法人)"),
        ("财务负责人", r"(?:我是|本人是|我作为).{0,8}财务负责人"),
        ("办税人员", r"(?:我是|本人是|我作为).{0,8}(?:办税员|办税人员)"),
    )
    for identity, pattern in explicit_patterns:
        if re.search(pattern, text):
            return identity

    source_identity = map_identity_label(raw_identity)
    if source_identity in {"法定代表人", "财务负责人", "办税人员", "其他"}:
        return source_identity

    if re.search(r"(?:我是|本人是|我作为).{0,8}(?:会计|经办人|股东)", text):
        return "其他"
    return "无法判断"


def resolve_enterprise_identity(
    *,
    caller_type: str | None,
    raw_identity: object,
    explicit_identity: str | None,
) -> IdentityDecision:
    """Resolve model-extracted explicit identity against the original label."""

    if caller_type == "个人":
        return IdentityDecision("不适用", "caller_type", False)
    if caller_type != "企业":
        return IdentityDecision("无法判断", "unknown", False)

    source_identity = map_identity_label(raw_identity)
    source_enterprise_identity = (
        source_identity if source_identity in ENTERPRISE_IDENTITIES else None
    )
    explicit = (
        explicit_identity if explicit_identity in ENTERPRISE_IDENTITIES else None
    )
    if explicit is not None:
        conflict = bool(
            source_enterprise_identity is not None
            and source_enterprise_identity != explicit
        )
        return IdentityDecision(explicit, "transcript", conflict)
    if source_enterprise_identity is not None:
        return IdentityDecision(source_enterprise_identity, "source_label", False)
    if source_identity == "自然人":
        return IdentityDecision("无法判断", "conflict", True)
    return IdentityDecision("无法判断", "unknown", False)
