"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。

除通用资源路由外，显著性检验与切片分析器各有一组业务接口：前者负责
检索查询签名、检验确认/更正/撤销与取证单登记；后者负责按取证单领取依据
并执行切片。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for
from app.services.segment_slice import SegmentSliceService
from app.services.significance_test import SignificanceTestService

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def significance_service() -> SignificanceTestService:
    return service_for("significance_test")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 显著性检验：检索 / 查询签名 / 更正 / 撤销 / 取证单
# 这些固定路径必须在 /{module}/{entry_id} 之前注册，否则会被动态段吞掉。
# ---------------------------------------------------------------------------


@router.get("/significance_test/search", response_model=dict)
def search_significance(
    experiment: str = Query(default=""),
    metric: str = Query(default=""),
    windows: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    status: str = Query(default=""),
    sort: str = Query(default="start_asc"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    service = significance_service()
    return service.search(
        {
            "experiment": experiment,
            "metric": metric,
            "windows": windows,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
            "sort": sort,
        },
        page=page,
        size=size,
    )


@router.get("/significance_test/queries", response_model=list[dict])
def list_queries() -> list[dict[str, Any]]:
    return significance_service().list_queries()


@router.get("/significance_test/queries/{signature}", response_model=dict)
def get_query(signature: str) -> dict[str, Any]:
    saved = significance_service().get_query(signature)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"查询签名 {signature} 不存在")
    return saved


@router.get("/significance_test/queries/{signature}/rerun", response_model=dict)
def rerun_query(signature: str) -> dict[str, Any]:
    result = significance_service().rerun_query(signature)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=str(result.get("message")))
    return result


@router.get("/significance_test/evidence-tickets", response_model=list[dict])
def list_tickets(
    experiment: str = Query(default=""),
    metric: str = Query(default=""),
) -> list[dict[str, Any]]:
    return significance_service().list_tickets(experiment, metric)


@router.get("/significance_test/evidence-tickets/{ticket_id}", response_model=dict)
def get_ticket(ticket_id: int) -> dict[str, Any]:
    ticket = significance_service().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"取证单 {ticket_id} 不存在")
    return ticket


@router.post("/significance_test/evidence-tickets", response_model=ActionResult)
def create_ticket(payload: EntryPayload) -> ActionResult:
    result = significance_service().create_ticket(payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("ticket"),
    )


@router.post("/significance_test/evidence-tickets/{ticket_id}/claim", response_model=ActionResult)
def claim_ticket(ticket_id: int, payload: EntryPayload) -> ActionResult:
    result = significance_service().claim_ticket(ticket_id, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("ticket"),
    )


@router.post("/significance_test/evidence-tickets/{ticket_id}/reissue", response_model=ActionResult)
def reissue_ticket(ticket_id: int, payload: EntryPayload) -> ActionResult:
    result = significance_service().reissue_ticket(ticket_id, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("ticket"),
    )


@router.get("/significance_test/{entry_id}/trace", response_model=dict)
def trace_entry(entry_id: int) -> dict[str, Any]:
    result = significance_service().trace_entry(entry_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=str(result.get("message")))
    return result


@router.post("/significance_test/{entry_id}/revise", response_model=ActionResult)
def revise_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    result = significance_service().revise_entry(entry_id, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("entry"),
    )


@router.post("/significance_test/{entry_id}/revoke", response_model=ActionResult)
def revoke_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    service = significance_service()
    result = service.revoke_entry(entry_id, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=service.get_entry(entry_id),
    )


@router.get("/segment_slice/evidence/{ticket_id}", response_model=dict)
def slice_evidence(ticket_id: int) -> dict[str, Any]:
    ticket = significance_service().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"取证单 {ticket_id} 不存在")
    return ticket


@router.post("/segment_slice/{entry_id}/run", response_model=ActionResult)
def run_slice(entry_id: int) -> ActionResult:
    service = service_for("segment_slice")
    assert isinstance(service, SegmentSliceService)
    result = service.run_slice(entry_id)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=service.get_entry(entry_id),
    )


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
