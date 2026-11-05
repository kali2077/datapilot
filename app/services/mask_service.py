"""列级数据脱敏：规则匹配（列名+数据源）→ 出参后处理。

只作用于面向人的出参（查询工作台/智能问数/数据服务/资产样例），
同步任务走独立读取链路不受影响（同步数据必须明文）。
"""
import re
from typing import List, Tuple

from sqlalchemy.orm import Session

from ..models import MaskRule, DataSource


def _mask_phone(v: str) -> str:
    return re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", v)


def _mask_id(v: str) -> str:
    return v[:6] + "********" + v[-4:] if len(v) > 10 else "****"


def _mask_email(v: str) -> str:
    if "@" not in v:
        return v[:1] + "***"
    name, _, dom = v.partition("@")
    return (name[:1] or "*") + "***@" + dom


def _mask_generic(v: str) -> str:
    if len(v) <= 4:
        return "*" * len(v)
    return v[:2] + "*" * (len(v) - 4) + v[-2:]


def _apply_strategy(strategy: str, custom_regex: str, v) -> str:
    s = str(v)
    if strategy == "phone":
        return _mask_phone(s) if re.fullmatch(r"\d{11}", s) else _mask_generic(s)
    if strategy == "id_card":
        return _mask_id(s)
    if strategy == "email":
        return _mask_email(s)
    if strategy == "custom" and custom_regex:
        try:
            return re.sub(custom_regex, "***", s)
        except re.error:
            return _mask_generic(s)
    return _mask_generic(s)


def active_rules(db: Session, ds: DataSource) -> List[MaskRule]:
    q = (db.query(MaskRule)
           .filter(MaskRule.enabled == True,  # noqa: E712
                   MaskRule.datasource_id.in_([0, ds.id])))
    return q.all()


def apply(db: Session, ds: DataSource, columns: List[str], rows: List[list]) -> Tuple[List[str], List[list], List[str]]:
    """按列名匹配规则，就地掩码。返回 (columns, rows, 被脱敏的列名)。"""
    rules = active_rules(db, ds)
    if not rules or not rows:
        return columns, rows, []
    hit = {}
    for rule in rules:
        for i, col in enumerate(columns):
            if col.lower() == rule.column_name.lower():
                hit[i] = rule
    if not hit:
        return columns, rows, []
    for i, rule in hit.items():
        for r in rows:
            if r[i] is not None:
                r[i] = _apply_strategy(rule.strategy, rule.custom_regex, r[i])
    return columns, rows, [columns[i] for i in sorted(hit)]
