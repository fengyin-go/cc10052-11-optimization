"""归因分析器领域服务：管理实验差异的贡献因子分解、归因方法调度与结果复核。

数据链路位置：归因分析层，消费显著性检验的显著结果与指标口径，把实验差异
分解到具体因子贡献，并输出给报告生成器形成归因章节。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AttrAnalysisService(BaseModuleService):
    MODULE = "attr_analysis"
    ENTITY = "归因结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["归因批次", "实验编码", "贡献因子", "贡献权重", "归因方法"]
    REQUIRED_FIELDS = ["归因批次", "实验编码", "贡献因子"]
    STATUS_ORDER = ["排队中", "已归因", "归因失败"]
    ACTION_RULES = {"开始归因": "已归因", "重试归因": "归因失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：归因计算与报告衔接 ----

    def run_attribution(self, entry_id: int) -> dict[str, Any]:
        """执行归因分析，归一化贡献权重并写入报告生成队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "排队中":
            return {"ok": False, "message": "仅排队中批次可开始归因"}
        weight = str(entry.get("贡献权重", "0"))
        try:
            weight_num = float(weight)
        except ValueError:
            entry["status"] = "归因失败"
            entry["abnormal"] = True
            return {"ok": False, "message": "贡献权重非法，批次归因失败"}
        if weight_num <= 0 or weight_num > 1:
            entry["贡献权重"] = "0.5"
        entry["status"] = "已归因"
        entry["pending"] = False
        report_rows = store.rows("report_gen")
        report_rows.append({
            "id": max((int(r.get("id", 0)) for r in report_rows), default=0) + 1,
            "报告编码": f"RPT-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": entry.get("实验编码", ""),
            "报告模板": "归因分析模板",
            "数据窗口": "最近7天",
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"归因完成（{entry.get('归因方法', 'shapley')}），报告已排队"}

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试归因失败批次，切换归因方法重新排队。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "归因失败":
            return {"ok": False, "message": "仅归因失败批次可重试"}
        method = str(entry.get("归因方法", "shapley"))
        entry["归因方法"] = "last_touch" if method == "shapley" else "shapley"
        entry["status"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        return {"ok": True, "message": f"已切换为 {entry['归因方法']} 并重新排队"}

    def batch_run_queued(self) -> dict[str, Any]:
        """批量执行排队中的归因批次。"""
        queued = [r for r in store.rows(self.MODULE) if r.get("status") == "排队中"]
        done = 0
        for row in queued:
            if self.run_attribution(int(row.get("id", 0))).get("ok"):
                done += 1
        return {"ok": True, "message": f"批量归因完成 {done} 条", "total": len(queued)}

    def consume_from_significance_test(self, exp_code: str) -> list[dict[str, Any]]:
        """读取显著性检验层的显著结果，确定可归因因子清单。"""
        factors = []
        for r in store.rows("significance_test"):
            if r.get("实验编码") != exp_code or r.get("status") != "已确认":
                continue
            try:
                if float(r.get("P值", "1.0")) < 0.05:
                    factors.append({
                        "贡献因子": r.get("指标编码", ""),
                        "置信区间": r.get("置信区间", ""),
                    })
            except ValueError:
                continue
        return factors

    def consume_from_metric_catalog(self, metric_code: str) -> dict[str, Any]:
        """读取指标口径中心的口径定义，校验归因因子是否可计算。"""
        metric = next(
            (m for m in store.rows("metric_catalog") if m.get("指标编码") == metric_code),
            None,
        )
        if metric is None:
            return {"ok": False, "message": f"口径 {metric_code} 不存在"}
        return {
            "ok": True,
            "计算口径": metric.get("计算口径", ""),
            "口径版本": metric.get("口径版本", ""),
            "状态": metric.get("status", ""),
        }

    def export_factor_weights(self, exp_code: str) -> list[dict[str, Any]]:
        """导出实验的因子贡献权重清单，供切片分析器对照分组差异。"""
        return [
            {
                "贡献因子": r.get("贡献因子", ""),
                "贡献权重": r.get("贡献权重", ""),
                "归因方法": r.get("归因方法", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        ]

    def normalize_weights(self, exp_code: str) -> dict[str, Any]:
        """归一化实验全部因子的贡献权重使总和为1。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code and r.get("status") == "已归因"]
        if not rows:
            return {"ok": False, "message": "无已归因结果可归一化"}
        weights = []
        for r in rows:
            try:
                weights.append(float(r.get("贡献权重", "0")))
            except ValueError:
                weights.append(0.0)
        total = sum(weights)
        if total <= 0:
            for r in rows:
                r["贡献权重"] = str(round(1.0 / len(rows), 4))
        else:
            for r, w in zip(rows, weights):
                r["贡献权重"] = str(round(w / total, 4))
        return {"ok": True, "message": f"已归一化 {len(rows)} 个因子权重"}
