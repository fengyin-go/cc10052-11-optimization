"""显著性检验领域服务：管理实验分组差异的P值、置信区间计算与检验批次流转。

数据链路位置：统计检验层，消费指标计算层产出的指标结果，对分组差异执行
显著性检验，并把显著结果推送给归因分析与切片分析继续下钻。

检索侧约束：
- 查询必须同时锁定实验编码与指标编码，且按编码精确匹配，避免把同指标名的
  别组实验混进结果；
- 窗口筛选按窗口起点/终点与查询日期区间求相交，排序固定带批次与id兜底，
  翻页和切换排序都不会改变同一窗口对应的检验批次；
- 每次检索留下查询签名，之后可以按当时的排序和筛选条件重查；
- 确认后的检验可以更正，原结果保持已确认可追溯，更正形成新的轮次。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store

TIER_TERM = "检验"

SORT_MODES: dict[str, str] = {
    "start_asc": "窗口起点升序",
    "start_desc": "窗口起点降序",
    "end_asc": "窗口终点升序",
    "end_desc": "窗口终点降序",
    "batch_asc": "检验批次升序",
    "confirm_desc": "确认时间倒序",
}

DIRECTION_POSITIVE = "正向"
DIRECTION_NEGATIVE = "负向"
DIRECTION_UNKNOWN = "无法判定"


def parse_interval(text: Any) -> tuple[float, float] | None:
    """解析 [下限, 上限] 形式的置信区间，无法解析时返回 None。"""
    raw = str(text or "").strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return None
    parts = [part.strip() for part in raw[1:-1].split(",")]
    if len(parts) != 2:
        return None
    try:
        low, high = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if low > high:
        low, high = high, low
    return low, high


def direction_of(text: Any) -> str:
    """置信区间跨零时只能判为无法判定差异方向。"""
    interval = parse_interval(text)
    if interval is None:
        return DIRECTION_UNKNOWN
    low, high = interval
    if low > 0:
        return DIRECTION_POSITIVE
    if high < 0:
        return DIRECTION_NEGATIVE
    return DIRECTION_UNKNOWN


def split_windows(text: Any) -> list[str]:
    if text is None:
        return []
    if isinstance(text, (list, tuple, set)):
        parts: list[str] = []
        for item in text:
            parts.extend(split_windows(item))
        return parts
    return [part for part in re.split(r"[,，、;\s]+", str(text).strip()) if part]


def stable_key(row: dict[str, Any]) -> str:
    """检验批次的稳定身份：id + 批次号 + 轮次，排序和翻页都改不了它。"""
    return f"{row.get('id')}:{row.get('检验批次', '')}:{row.get('轮次', '')}"


def _round_number(row: dict[str, Any]) -> int:
    try:
        return int(str(row.get("轮次", "1")) or 1)
    except ValueError:
        return 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignificanceTestService(BaseModuleService):
    MODULE = "significance_test"
    ENTITY = "检验结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "检验批次", "实验编码", "指标编码", "P值", "置信区间",
        "检验窗口", "轮次", "修订状态",
    ]
    REQUIRED_FIELDS = ["检验批次", "实验编码", "指标编码"]
    STATUS_ORDER = ["待检验", "已检验", "已确认", "检验异常", "已撤销"]
    ACTION_RULES = {
        "执行检验": "已检验",
        "确认检验": "已确认",
        "重试检验": "检验异常",
        "撤销检验": "已撤销",
    }
    NEGATIVE_ACTIONS = ["重试检验"]
    # 编码字段精确过滤：E1 不能命中 E10，M 不能命中 M10。
    EXACT_FILTER_FIELDS = ["实验编码", "指标编码", "检验批次", "检验窗口"]
    QUERY_TABLE = "significance_query"
    TICKET_TABLE = "slice_evidence_ticket"

    # ---------------------------------------------------------------
    # 检索：实验 + 指标锁定，窗口/日期筛选，稳定排序与查询签名
    # ---------------------------------------------------------------

    def normalize_conditions(self, conditions: dict[str, Any] | None) -> dict[str, Any]:
        raw = conditions or {}
        sort = str(raw.get("sort") or "start_asc")
        if sort not in SORT_MODES:
            sort = "start_asc"
        return {
            "experiment": str(raw.get("experiment") or raw.get("实验编码") or "").strip(),
            "metric": str(raw.get("metric") or raw.get("指标编码") or "").strip(),
            "windows": split_windows(raw.get("windows") or raw.get("检验窗口")),
            "date_from": str(raw.get("date_from") or raw.get("日期起") or "").strip(),
            "date_to": str(raw.get("date_to") or raw.get("日期止") or "").strip(),
            "status": str(raw.get("status") or "").strip(),
            "sort": sort,
        }

    def _context_rows(self, conditions: dict[str, Any]) -> list[dict[str, Any]]:
        exp = conditions["experiment"].casefold()
        metric = conditions["metric"].casefold()
        if not exp or not metric:
            return []
        rows = [
            row for row in store.rows(self.MODULE)
            if str(row.get("实验编码", "")).casefold() == exp
            and str(row.get("指标编码", "")).casefold() == metric
        ]
        windows = {window.casefold() for window in conditions["windows"]}
        if windows:
            rows = [
                row for row in rows
                if str(row.get("检验窗口", "")).casefold() in windows
            ]
        date_from = conditions["date_from"]
        date_to = conditions["date_to"]
        if date_from or date_to:
            kept = []
            for row in rows:
                start = str(row.get("窗口起点", ""))
                end = str(row.get("窗口终点", ""))
                if not start or not end:
                    continue
                # 日期相交按窗口起点与终点判断。
                if date_from and end < date_from:
                    continue
                if date_to and start > date_to:
                    continue
                kept.append(row)
            rows = kept
        status = conditions["status"]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def _sort_rows(self, rows: Iterable[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
        def base(row: dict[str, Any]) -> tuple[str, str, int, int]:
            return (
                str(row.get("窗口起点", "")),
                str(row.get("窗口终点", "")),
                _round_number(row),
                int(row.get("id", 0)),
            )

        ordered = list(rows)
        if sort == "start_desc":
            ordered.sort(key=base, reverse=True)
        elif sort == "end_asc":
            ordered.sort(key=lambda row: (
                str(row.get("窗口终点", "")), str(row.get("窗口起点", "")),
                _round_number(row), int(row.get("id", 0)),
            ))
        elif sort == "end_desc":
            ordered.sort(key=lambda row: (
                str(row.get("窗口终点", "")), str(row.get("窗口起点", "")),
                _round_number(row), int(row.get("id", 0)),
            ), reverse=True)
        elif sort == "batch_asc":
            ordered.sort(key=lambda row: (
                str(row.get("检验批次", "")), _round_number(row), int(row.get("id", 0)),
            ))
        elif sort == "confirm_desc":
            ordered.sort(key=lambda row: (
                str(row.get("确认时间", "")), int(row.get("id", 0)),
            ), reverse=True)
        else:
            ordered.sort(key=base)
        return ordered

    def search(
        self,
        conditions: dict[str, Any] | None,
        *,
        page: int = 1,
        size: int = 20,
        save: bool = True,
    ) -> dict[str, Any]:
        ctx = self.normalize_conditions(conditions)
        rows = self._sort_rows(self._context_rows(ctx), ctx["sort"])
        ids = [int(row.get("id", 0)) for row in rows]
        signature = self._save_query(ctx, ids) if save and ctx["experiment"] and ctx["metric"] else ""
        start = max(page - 1, 0) * size
        page_rows = [self._decorate(row) for row in rows[start:start + size]]
        return {
            "ok": bool(ctx["experiment"] and ctx["metric"]),
            "message": "" if ctx["experiment"] and ctx["metric"] else "必须同时指定实验编码与指标编码",
            "query_signature": signature,
            "conditions": ctx,
            "sort_label": SORT_MODES[ctx["sort"]],
            "items": page_rows,
            "total": len(rows),
            "page": page,
            "size": size,
        }

    def _decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["stable_key"] = stable_key(row)
        item["差异方向"] = row.get("差异方向") or direction_of(row.get("置信区间"))
        item["有效依据"] = row.get("status") == "已确认"
        return item

    def _save_query(self, ctx: dict[str, Any], ids: list[int]) -> str:
        rows = store.rows(self.QUERY_TABLE)
        # 同一组排序与筛选条件只保留一个签名，翻页和重复点查询不会刷出一串记录。
        for row in rows:
            if (
                row.get("实验编码") == ctx["experiment"]
                and row.get("指标编码") == ctx["metric"]
                and split_windows(row.get("窗口集合", "")) == ctx["windows"]
                and row.get("日期起", "") == ctx["date_from"]
                and row.get("日期止", "") == ctx["date_to"]
                and row.get("状态筛选", "") == ctx["status"]
                and row.get("排序", "") == ctx["sort"]
            ):
                row["结果快照"] = ids
                row["最近重查时间"] = _now()
                return str(row.get("查询签名"))
        seq = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        signature = f"QS-{seq:04d}"
        rows.append({
            "id": seq,
            "查询签名": signature,
            "实验编码": ctx["experiment"],
            "指标编码": ctx["metric"],
            "窗口集合": "、".join(ctx["windows"]),
            "日期起": ctx["date_from"],
            "日期止": ctx["date_to"],
            "状态筛选": ctx["status"],
            "排序": ctx["sort"],
            "排序说明": SORT_MODES[ctx["sort"]],
            "结果快照": ids,
            "创建时间": _now(),
        })
        return signature

    def get_query(self, signature: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows(self.QUERY_TABLE) if row.get("查询签名") == signature),
            None,
        )

    def list_queries(self) -> list[dict[str, Any]]:
        """列出可重查的查询签名，最新的排在最前。"""
        return sorted(
            (dict(row) for row in store.rows(self.QUERY_TABLE)),
            key=lambda row: int(row.get("id", 0)),
            reverse=True,
        )

    def rerun_query(self, signature: str) -> dict[str, Any]:
        """按当时保存的排序与筛选条件重查；后续更检以新批次身份出现。"""
        saved = self.get_query(signature)
        if saved is None:
            return {"ok": False, "message": f"查询签名 {signature} 不存在"}
        ctx = {
            "experiment": saved.get("实验编码", ""),
            "metric": saved.get("指标编码", ""),
            "windows": split_windows(saved.get("窗口集合", "")),
            "date_from": saved.get("日期起", ""),
            "date_to": saved.get("日期止", ""),
            "status": saved.get("状态筛选", ""),
            "sort": saved.get("排序", "start_asc"),
        }
        result = self.search(ctx, page=1, size=200, save=False)
        snapshot = [int(value) for value in saved.get("结果快照", [])]
        current = [int(row["id"]) for row in result["items"]]
        added = [row for row in result["items"] if int(row["id"]) not in snapshot]
        missing = [
            row for row in (
                self._decorate(store.find(self.MODULE, entry_id) or {})
                for entry_id in snapshot if store.find(self.MODULE, entry_id)
            )
            if int(row.get("id", 0)) not in current
        ]
        result["query_signature"] = signature
        result["conditions"] = ctx
        result["snapshot"] = snapshot
        result["added"] = added
        result["missing"] = missing
        result["message"] = (
            f"按原条件重查：新增 {len(added)} 条，移出 {len(missing)} 条"
        )
        return result

    # ---------------------------------------------------------------
    # 确认 / 更正 / 撤销：修订前后的窗口都能辨认并各自追查
    # ---------------------------------------------------------------

    def latest_confirmed(self, experiment: str, metric: str, window: str) -> dict[str, Any] | None:
        candidates = [
            row for row in store.rows(self.MODULE)
            if str(row.get("实验编码", "")).casefold() == experiment.casefold()
            and str(row.get("指标编码", "")).casefold() == metric.casefold()
            and str(row.get("检验窗口", "")).casefold() == window.casefold()
            and row.get("status") == "已确认"
            and parse_interval(row.get("置信区间")) is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: (_round_number(row), int(row.get("id", 0))))

    def revise_entry(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """确认后的检验更正：原结果保留已确认，新轮次作为可辨认结果留档。"""
        source = store.find(self.MODULE, entry_id)
        if source is None:
            return {"ok": False, "message": f"检验批次 {entry_id} 不存在"}
        if source.get("status") != "已确认":
            return {"ok": False, "message": "只有已确认的检验批次可以更正"}
        if source.get("后续批次"):
            return {"ok": False, "message": "该批次已被后续轮次取代，请在最新轮次上更正"}
        interval = parse_interval(values.get("置信区间"))
        if interval is None:
            return {"ok": False, "message": "置信区间格式应为 [下限, 上限]"}
        ci = f"[{interval[0]}, {interval[1]}]"
        rows = store.rows(self.MODULE)
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        round_no = _round_number(source) + 1
        batch = f"ST-{source.get('实验编码', '')}-{source.get('指标编码', '')}-" \
                f"{source.get('检验窗口', '')}-R{round_no}"
        new_row = {
            "id": new_id,
            "status": "已确认",
            "pending": False,
            "abnormal": False,
            "检验批次": batch,
            "实验编码": source.get("实验编码", ""),
            "指标编码": source.get("指标编码", ""),
            "指标名称": source.get("指标名称", ""),
            "检验窗口": source.get("检验窗口", ""),
            "窗口起点": source.get("窗口起点", ""),
            "窗口终点": source.get("窗口终点", ""),
            "P值": str(values.get("P值") or source.get("P值", "")),
            "置信区间": ci,
            "差异方向": direction_of(ci),
            "轮次": str(round_no),
            "修订状态": "修订后",
            "前序批次": source.get("检验批次", ""),
            "后续批次": "",
            "修订原因": str(values.get("原因") or ""),
            "确认人": str(values.get("确认人") or source.get("确认人", "")),
            "确认时间": _now(),
            "确认查询": source.get("确认查询", ""),
        }
        rows.append(new_row)
        source["修订状态"] = "修订前"
        source["后续批次"] = batch
        return {"ok": True, "message": f"已登记更正轮次 {batch}，原结果保持已确认", "entry": new_row}

    def revoke_entry(self, entry_id: int, values: dict[str, Any] | None = None) -> dict[str, Any]:
        """撤销已确认的检验；撤销后切片不得再采用该窗口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"检验批次 {entry_id} 不存在"}
        if entry.get("status") == "已撤销":
            return {"ok": False, "message": "该检验批次已经撤销"}
        if entry.get("status") != "已确认":
            return {"ok": False, "message": "只有已确认的检验批次可以撤销"}
        entry["status"] = "已撤销"
        entry["pending"] = False
        entry["撤销时间"] = _now()
        entry["撤销原因"] = str((values or {}).get("原因") or "确认结果作废")
        return {"ok": True, "message": f"检验批次 {entry.get('检验批次')} 已撤销"}

    def trace_entry(self, entry_id: int) -> dict[str, Any]:
        """追查同一窗口的修订链和引用它的取证单，原结果与修订各自可查。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"检验批次 {entry_id} 不存在"}
        chain = [entry]
        seen = {int(entry.get("id", 0))}
        cursor = entry
        while cursor.get("前序批次"):
            previous = next(
                (row for row in store.rows(self.MODULE)
                 if row.get("检验批次") == cursor.get("前序批次")
                 and str(row.get("实验编码", "")).casefold() == str(entry.get("实验编码", "")).casefold()),
                None,
            )
            if previous is None or int(previous.get("id", 0)) in seen:
                break
            seen.add(int(previous.get("id", 0)))
            chain.insert(0, previous)
            cursor = previous
        cursor = entry
        while cursor.get("后续批次"):
            following = next(
                (row for row in store.rows(self.MODULE)
                 if row.get("检验批次") == cursor.get("后续批次")
                 and str(row.get("实验编码", "")).casefold() == str(entry.get("实验编码", "")).casefold()),
                None,
            )
            if following is None or int(following.get("id", 0)) in seen:
                break
            seen.add(int(following.get("id", 0)))
            chain.append(following)
            cursor = following
        batches = {row.get("检验批次") for row in chain}
        tickets = [
            ticket for ticket in store.rows(self.TICKET_TABLE)
            if any(item.get("检验批次") in batches for item in ticket.get("依据", []))
        ]
        return {
            "ok": True,
            "entry": self._decorate(entry),
            "chain": [self._decorate(row) for row in chain],
            "tickets": [
                {
                    "id": ticket.get("id"),
                    "取证单号": ticket.get("取证单号"),
                    "状态": ticket.get("状态"),
                    "采用依据": ticket.get("采用依据"),
                    "领取时间": ticket.get("领取时间", ""),
                }
                for ticket in tickets
            ],
        }

    # ---------------------------------------------------------------
    # 切片取证单：登记时锁依据，领取时按实际确认区间核验
    # ---------------------------------------------------------------

    def create_ticket(self, values: dict[str, Any]) -> dict[str, Any]:
        signature = str(values.get("query_signature") or "").strip()
        saved = self.get_query(signature) if signature else None
        experiment = str(values.get("experiment") or (saved or {}).get("实验编码") or "").strip()
        metric = str(values.get("metric") or (saved or {}).get("指标编码") or "").strip()
        if not experiment or not metric:
            return {"ok": False, "message": "取证单必须锁定实验编码与指标编码"}
        if saved is not None and (
            saved.get("实验编码", "").casefold() != experiment.casefold()
            or saved.get("指标编码", "").casefold() != metric.casefold()
        ):
            return {"ok": False, "message": "查询签名与所选实验/指标不一致"}
        ctx = {
            "experiment": experiment,
            "metric": metric,
            "windows": split_windows(values.get("windows") or (saved or {}).get("窗口集合")),
            "date_from": str(values.get("date_from") or (saved or {}).get("日期起") or ""),
            "date_to": str(values.get("date_to") or (saved or {}).get("日期止") or ""),
            "status": str(values.get("status") or (saved or {}).get("状态筛选") or ""),
            "sort": str((saved or {}).get("排序") or values.get("sort") or "start_asc"),
        }
        selected_ids = values.get("selected_ids") or values.get("selected") or []
        try:
            selected_ids = [int(item) for item in selected_ids]
        except (TypeError, ValueError):
            return {"ok": False, "message": "选中的检验批次不合法"}
        if not selected_ids:
            return {"ok": False, "message": "请先选择至少一个检验窗口"}
        if len(set(selected_ids)) != len(selected_ids):
            return {"ok": False, "message": "选中的窗口存在重复批次"}
        allowed = {int(row.get("id", 0)) for row in self._context_rows(ctx)}
        picked: list[dict[str, Any]] = []
        windows: set[str] = set()
        for entry_id in selected_ids:
            row = store.find(self.MODULE, entry_id)
            if row is None:
                return {"ok": False, "message": f"检验批次 {entry_id} 不存在"}
            if str(row.get("实验编码", "")).casefold() != experiment.casefold() \
                    or str(row.get("指标编码", "")).casefold() != metric.casefold():
                return {
                    "ok": False,
                    "message": f"检验批次 {row.get('检验批次')} 不属于实验 {experiment} 的指标 {metric}",
                }
            if entry_id not in allowed:
                return {
                    "ok": False,
                    "message": f"窗口 {row.get('检验窗口')} 不在本次排序与筛选条件内，已拒绝登记",
                }
            window = str(row.get("检验窗口", ""))
            if window.casefold() in windows:
                return {"ok": False, "message": f"同一窗口 {window} 只能选择一个检验版次"}
            windows.add(window.casefold())
            picked.append(row)
        ordered = self._sort_rows(picked, ctx["sort"])
        basis = [
            {
                "id": int(row.get("id", 0)),
                "检验批次": row.get("检验批次", ""),
                "检验窗口": row.get("检验窗口", ""),
                "窗口起点": row.get("窗口起点", ""),
                "窗口终点": row.get("窗口终点", ""),
                "轮次": str(row.get("轮次", "1")),
                "修订状态": row.get("修订状态", "原结果"),
                "P值": row.get("P值", ""),
                "置信区间": row.get("置信区间", ""),
                "差异方向": row.get("差异方向") or direction_of(row.get("置信区间")),
                "状态": row.get("status", ""),
                "确认时间": row.get("确认时间", ""),
                "stable_key": stable_key(row),
            }
            for row in ordered
        ]
        tickets = store.rows(self.TICKET_TABLE)
        seq = max((int(row.get("id", 0)) for row in tickets), default=0) + 1
        ticket_no = f"EV-{seq:04d}"
        ticket = {
            "id": seq,
            "取证单号": ticket_no,
            "状态": "待领取",
            "查询签名": signature,
            "实验编码": experiment,
            "指标编码": metric,
            "窗口集合": "、".join(item["检验窗口"] for item in basis),
            "排序": ctx["sort"],
            "排序说明": SORT_MODES[ctx["sort"]],
            "筛选条件": self._condition_text(ctx),
            "依据": basis,
            "采用依据": "",
            "实际引用": [],
            "缺失依据": [],
            "切片批次": "",
            "申请人": str(values.get("applicant") or ""),
            "申请时间": _now(),
            "领取人": "",
            "领取时间": "",
            "核对提示": "",
        }
        tickets.append(ticket)
        return {"ok": True, "message": f"已登记切片取证单 {ticket_no}", "ticket": ticket}

    @staticmethod
    def _condition_text(ctx: dict[str, Any]) -> str:
        parts = [f"实验 {ctx['experiment']}", f"指标 {ctx['metric']}"]
        if ctx["windows"]:
            parts.append(f"窗口 {'、'.join(ctx['windows'])}")
        if ctx["date_from"] or ctx["date_to"]:
            parts.append(f"日期 {ctx['date_from'] or '不限'}~{ctx['date_to'] or '不限'}")
        if ctx["status"]:
            parts.append(f"状态 {ctx['status']}")
        parts.append(f"排序 {SORT_MODES[ctx['sort']]}")
        return "；".join(parts)

    def list_tickets(self, experiment: str = "", metric: str = "") -> list[dict[str, Any]]:
        tickets = list(store.rows(self.TICKET_TABLE))
        if experiment:
            tickets = [
                ticket for ticket in tickets
                if str(ticket.get("实验编码", "")) == experiment
            ]
        if metric:
            tickets = [
                ticket for ticket in tickets
                if str(ticket.get("指标编码", "")) == metric
            ]
        return tickets

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        return next(
            (ticket for ticket in store.rows(self.TICKET_TABLE)
             if int(ticket.get("id", 0)) == ticket_id),
            None,
        )

    def claim_ticket(self, ticket_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """切片分析器领取取证单：核验所选批次，按实际确认区间确定取样依据。"""
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            return {"ok": False, "message": f"取证单 {ticket_id} 不存在"}
        if ticket.get("状态") != "待领取":
            return {"ok": False, "message": f"取证单当前为{ticket.get('状态')}，不能再次领取"}
        basis_mode = str(values.get("basis") or "pinned")
        if basis_mode not in ("pinned", "latest"):
            return {"ok": False, "message": "领取依据只能是 pinned（沿用登记时的版本）或 latest（采用最新确认）"}
        experiment = str(ticket.get("实验编码", ""))
        metric = str(ticket.get("指标编码", ""))
        adopted: list[dict[str, Any]] = []
        missing: list[str] = []
        notices: list[str] = []
        for item in ticket.get("依据", []):
            row = store.find(self.MODULE, int(item.get("id", 0)))
            if row is None or row.get("检验批次") != item.get("检验批次"):
                missing.append(f"{item.get('检验窗口')}（批次已不存在）")
                continue
            if row.get("status") == "已撤销":
                missing.append(f"{item.get('检验窗口')}（{item.get('检验批次')} 已撤销）")
                continue
            if row.get("status") != "已确认" or parse_interval(row.get("置信区间")) is None:
                missing.append(f"{item.get('检验窗口')}（{row.get('status')}，缺少有效确认区间）")
                continue
            adopted_row = row
            latest = self.latest_confirmed(experiment, metric, str(row.get("检验窗口", "")))
            superseded = latest is not None and int(latest.get("id", 0)) != int(row.get("id", 0))
            if superseded and basis_mode == "latest":
                adopted_row = latest
                notices.append(
                    f"{row.get('检验窗口')}：登记时批次 {row.get('检验批次')} 已被 "
                    f"{latest.get('检验批次')} 更正，本次采用更正后的最新确认区间"
                )
            elif superseded:
                notices.append(
                    f"{row.get('检验窗口')}：登记依据 {row.get('检验批次')} 仍为已确认的原结果，"
                    f"现行版本为 {latest.get('检验批次')}；本次沿用更正前区间"
                )
            elif row.get("后续批次"):
                notices.append(
                    f"{row.get('检验窗口')}：后续修订 {row.get('后续批次')} 已撤销，"
                    f"本次仍以原确认 {row.get('检验批次')} 为有效依据"
                )
            interval = parse_interval(adopted_row.get("置信区间"))
            assert interval is not None
            direction = direction_of(adopted_row.get("置信区间"))
            adopted.append({
                "id": int(adopted_row.get("id", 0)),
                "检验批次": adopted_row.get("检验批次", ""),
                "检验窗口": adopted_row.get("检验窗口", ""),
                "窗口起点": adopted_row.get("窗口起点", ""),
                "窗口终点": adopted_row.get("窗口终点", ""),
                "轮次": str(adopted_row.get("轮次", "1")),
                "修订状态": adopted_row.get("修订状态", ""),
                "置信区间": adopted_row.get("置信区间", ""),
                "差异方向": direction,
                "P值": adopted_row.get("P值", ""),
                "状态": adopted_row.get("status", ""),
                "依据来源": "更正前原确认" if adopted_row is row and superseded
                            else ("更正后最新确认" if adopted_row is not row else "登记时的确认版本"),
                "现行批次": latest.get("检验批次", "") if superseded and latest else "",
            })
        if missing:
            ticket["缺失依据"] = missing
            ticket["核对提示"] = f"选中的检验缺少有效依据：{'；'.join(missing)}"
            return {
                "ok": False,
                "message": f"取证单 {ticket.get('取证单号')} 不能领取：{'；'.join(missing)}",
                "ticket": ticket,
            }
        if not adopted:
            ticket["缺失依据"] = ["全部窗口均无有效依据"]
            return {"ok": False, "message": "取证单没有可采用的检验依据", "ticket": ticket}
        ticket["状态"] = "已领取"
        ticket["领取人"] = str(values.get("claimed_by") or "切片分析器")
        ticket["领取时间"] = _now()
        ticket["采用依据"] = "更正后最新确认" if basis_mode == "latest" else "登记时确认版本"
        ticket["实际引用"] = adopted
        ticket["缺失依据"] = []
        ticket["核对提示"] = "；".join(notices)
        sliced = self._register_slices(ticket, adopted)
        ticket["切片批次"] = "、".join(row["切片批次"] for row in sliced)
        return {
            "ok": True,
            "message": f"取证单 {ticket.get('取证单号')} 已领取，登记切片 {ticket['切片批次']}",
            "ticket": ticket,
            "slices": sliced,
        }

    def _register_slices(
        self, ticket: dict[str, Any], adopted: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows = store.rows("segment_slice")
        created = []
        for item in adopted:
            slice_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            batch = f"SG-{ticket.get('取证单号')}-{item['检验窗口']}"
            row = {
                "id": slice_id,
                "status": "待计算",
                "pending": True,
                "abnormal": False,
                "切片批次": batch,
                "实验编码": ticket.get("实验编码", ""),
                "指标编码": ticket.get("指标编码", ""),
                "切片维度": "分组",
                "切片值": item["检验窗口"],
                "分组差异": "",
                "置信区间": item["置信区间"],
                "差异方向": item["差异方向"],
                "检验批次": item["检验批次"],
                "窗口范围": f"{item['窗口起点']}~{item['窗口终点']}",
                "取证单号": ticket.get("取证单号", ""),
                "status": "待计算",
            }
            rows.append(row)
            created.append(row)
        return created

    def reissue_ticket(self, ticket_id: int, values: dict[str, Any] | None = None) -> dict[str, Any]:
        """重新发起下钻：保留窗口范围，改用当前最新有效确认区间重新登记。"""
        source = self.get_ticket(ticket_id)
        if source is None:
            return {"ok": False, "message": f"取证单 {ticket_id} 不存在"}
        if source.get("已重发为"):
            return {
                "ok": False,
                "message": f"取证单 {source.get('取证单号')} 已重发为 {source.get('已重发为')}，请从新取证单继续",
            }
        experiment = str(source.get("实验编码", ""))
        metric = str(source.get("指标编码", ""))
        basis: list[dict[str, Any]] = []
        missing: list[str] = []
        origin: list[dict[str, Any]] = []
        for item in source.get("依据", []):
            # 重新发起时以该窗口当前的有效确认（排除已撤销）为准，而不是任意最新轮次。
            registered = store.find(self.MODULE, int(item.get("id", 0)))
            latest = self.latest_confirmed(experiment, metric, str(item.get("检验窗口", "")))
            if latest is None:
                missing.append(str(item.get("检验窗口", "")))
                continue
            origin.append({
                "检验窗口": item.get("检验窗口", ""),
                "登记批次": item.get("检验批次", ""),
                "登记区间": item.get("置信区间", ""),
                "本次批次": latest.get("检验批次", ""),
                "本次区间": latest.get("置信区间", ""),
                "登记批次状态": (registered or {}).get("status", "已不存在"),
                "区间变化": item.get("置信区间", "") != latest.get("置信区间", ""),
            })
            basis.append({
                "id": int(latest.get("id", 0)),
                "检验批次": latest.get("检验批次", ""),
                "检验窗口": latest.get("检验窗口", ""),
                "窗口起点": latest.get("窗口起点", ""),
                "窗口终点": latest.get("窗口终点", ""),
                "轮次": str(latest.get("轮次", "1")),
                "修订状态": latest.get("修订状态", ""),
                "P值": latest.get("P值", ""),
                "置信区间": latest.get("置信区间", ""),
                "差异方向": latest.get("差异方向") or direction_of(latest.get("置信区间")),
                "状态": latest.get("status", ""),
                "确认时间": latest.get("确认时间", ""),
                "stable_key": stable_key(latest),
            })
        if missing:
            return {
                "ok": False,
                "message": f"窗口 {'、'.join(missing)} 缺少有效确认依据，不能重新发起",
            }
        tickets = store.rows(self.TICKET_TABLE)
        seq = max((int(row.get("id", 0)) for row in tickets), default=0) + 1
        ticket_no = f"EV-{seq:04d}"
        ticket = {
            "id": seq,
            "取证单号": ticket_no,
            "状态": "待领取",
            "查询签名": source.get("查询签名", ""),
            "实验编码": experiment,
            "指标编码": metric,
            "窗口集合": "、".join(item["检验窗口"] for item in basis),
            "排序": source.get("排序", "start_asc"),
            "排序说明": source.get("排序说明", ""),
            "筛选条件": source.get("筛选条件", ""),
            "依据": basis,
            "采用依据": "",
            "实际引用": [],
            "缺失依据": [],
            "切片批次": "",
            "申请人": str((values or {}).get("applicant") or source.get("申请人", "")),
            "申请时间": _now(),
            "领取人": "",
            "领取时间": "",
            "核对提示": f"由取证单 {source.get('取证单号')} 重新发起，采用最新有效确认区间",
            "重发来源": source.get("取证单号", ""),
            "区间变化": origin,
        }
        tickets.append(ticket)
        source["已重发为"] = ticket_no
        return {
            "ok": True,
            "message": f"已按最新确认区间重新发起取证单 {ticket_no}",
            "ticket": ticket,
        }

    # ---------------------------------------------------------------
    # 通用动作：兼容资源路由，同时补齐确认/撤销的留痕
    # ---------------------------------------------------------------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "确认检验":
            if entry.get("status") != "已检验":
                return None, "仅已检验批次可确认"
            if parse_interval(entry.get("置信区间")) is None:
                return None, "置信区间格式非法，不能确认"
            entry["status"] = "已确认"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["差异方向"] = direction_of(entry.get("置信区间"))
            entry["确认时间"] = _now()
            return entry, "检验结果已确认"
        if action == "撤销检验":
            result = self.revoke_entry(entry_id, {})
            return (entry, str(result["message"])) if result.get("ok") else (None, str(result["message"]))
        return super().run_action(entry_id, action)

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        # 编码字段按精确匹配，避免同名指标的其他实验混入。
        for field, expected in (filters or {}).items():
            if field in self.EXACT_FILTER_FIELDS:
                rows = [
                    row for row in rows
                    if str(row.get(field, "")).casefold() == expected.casefold()
                ]
            elif field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            rows = [
                row for row in rows
                if keyword.casefold() in str(row.get(self.LIST_FIELDS[0], "")).casefold()
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows = self._sort_rows(rows, "start_asc")
        total = len(rows)
        start = (page - 1) * size
        return [self._decorate(row) for row in rows[start:start + size]], total

    # ---- 既有跨模块能力 ----

    def run_test(self, entry_id: int) -> dict[str, Any]:
        """执行显著性检验，P值低于阈值时登记归因分析批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待检验":
            return {"ok": False, "message": "仅待检验批次可执行"}
        p_value = str(entry.get("P值", "1.0"))
        try:
            p_num = float(p_value)
        except ValueError:
            entry["status"] = "检验异常"
            entry["abnormal"] = True
            return {"ok": False, "message": "P值格式非法，批次转入检验异常"}
        entry["status"] = "已检验"
        entry["pending"] = True
        entry["差异方向"] = direction_of(entry.get("置信区间"))
        if p_num < 0.05:
            attr_rows = store.rows("attr_analysis")
            attr_rows.append({
                "id": max((int(r.get("id", 0)) for r in attr_rows), default=0) + 1,
                "归因批次": f"AT-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
                "实验编码": entry.get("实验编码", ""),
                "贡献因子": entry.get("指标编码", ""),
                "贡献权重": "0.62",
                "归因方法": "shapley",
                "status": "排队中",
                "pending": True,
                "abnormal": False,
            })
            return {"ok": True, "message": f"检验显著（P={p_num}），已登记归因批次"}
        return {"ok": True, "message": f"检验不显著（P={p_num}），无需归因"}

    def retry_abnormal(self, entry_id: int) -> dict[str, Any]:
        """重试检验异常批次，重新解析P值与置信区间。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "检验异常":
            return {"ok": False, "message": "仅检验异常批次可重试"}
        ci = str(entry.get("置信区间", ""))
        if parse_interval(ci) is None:
            entry["置信区间"] = "[-0.05, 0.05]"
        entry["status"] = "待检验"
        entry["pending"] = True
        entry["abnormal"] = False
        return {"ok": True, "message": f"批次 {entry_id} 已重置为待检验"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行全部待检验批次并推送显著结果。"""
        pending_rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待检验"]
        significant = 0
        for row in pending_rows:
            result = self.run_test(int(row.get("id", 0)))
            if result.get("ok") and "显著" in str(result.get("message", "")) and "不显著" not in str(result.get("message", "")):
                significant += 1
        return {"ok": True, "message": f"批量检验完成，显著批次 {significant}", "total": len(pending_rows)}

    def produce_to_segment_slice(self, exp_code: str) -> dict[str, Any]:
        """把显著检验结果推送给切片分析器，登记待计算切片。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已检验"
        ]
        if not rows:
            return {"ok": False, "message": f"实验 {exp_code} 无已检验批次"}
        slice_rows = store.rows("segment_slice")
        slice_rows.append({
            "id": max((int(r.get("id", 0)) for r in slice_rows), default=0) + 1,
            "切片批次": f"SG-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "切片维度": "分组",
            "切片值": "实验组/对照组",
            "分组差异": rows[-1].get("置信区间", ""),
            "status": "待计算",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"切片批次已登记（覆盖 {len(rows)} 条检验结果）"}

    def consume_from_metric_compute(self, metric_code: str) -> list[dict[str, Any]]:
        """读取指标计算层该指标的完成作业，返回可检验窗口。"""
        return [
            {"作业名称": r.get("作业名称", ""), "窗口水位": r.get("窗口水位", "")}
            for r in store.rows("metric_compute")
            if r.get("指标编码") == metric_code and r.get("status") == "已完成"
        ]

    def list_significant(self, exp_code: str) -> list[dict[str, Any]]:
        """列出实验的显著检验结果，供报告生成器写入结论。"""
        results = []
        for r in store.rows(self.MODULE):
            if r.get("实验编码") != exp_code or r.get("status") != "已确认":
                continue
            try:
                if float(r.get("P值", "1.0")) < 0.05:
                    results.append({
                        "指标编码": r.get("指标编码", ""),
                        "检验窗口": r.get("检验窗口", ""),
                        "P值": r.get("P值", ""),
                        "置信区间": r.get("置信区间", ""),
                        "差异方向": direction_of(r.get("置信区间")),
                    })
            except ValueError:
                continue
        return results

    def sync_to_freshness(self, exp_code: str) -> dict[str, Any]:
        """同步检验批次完成时间到数据时效监控。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code]
        if not rows:
            return {"ok": False, "message": "无检验批次"}
        latest = max(str(r.get("检验时间", "")) for r in rows) if rows else ""
        freshness_rows = store.rows("data_freshness")
        freshness_rows.append({
            "id": max((int(r.get("id", 0)) for r in freshness_rows), default=0) + 1,
            "监控对象": f"significance_test:{exp_code}",
            "数据域": "统计检验",
            "最新水位": latest or datetime.now(timezone.utc).isoformat(),
            "延迟分钟": "0",
            "时效等级": "新鲜",
            "status": "新鲜",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "时效监控已同步"}
