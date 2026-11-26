"""报告生成器领域服务：管理实验分析报告的模板编排、数据窗口聚合与生成重试。

数据链路位置：报告生产层，消费归因分析与显著性检验的结论数据，按模板聚合成
分析报告，并把成品交给报告分发推送给业务接收方。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ReportGenService(BaseModuleService):
    MODULE = "report_gen"
    ENTITY = "分析报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编码", "实验编码", "报告模板", "数据窗口", "生成状态"]
    REQUIRED_FIELDS = ["报告编码", "实验编码", "报告模板"]
    STATUS_ORDER = ["排队中", "生成中", "已完成", "生成失败"]
    ACTION_RULES = {"开始生成": "生成中", "重试生成": "生成失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：报告编排与成品分发 ----

    def generate_report(self, entry_id: int) -> dict[str, Any]:
        """聚合检验与归因结论生成报告，完成后登记分发批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "排队中":
            return {"ok": False, "message": "仅排队中报告可生成"}
        exp_code = str(entry.get("实验编码", ""))
        significant = [
            r for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已确认"
        ]
        attributed = [
            r for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        ]
        if not significant:
            entry["status"] = "生成失败"
            entry["生成状态"] = "生成失败"
            entry["abnormal"] = True
            entry["失败原因"] = "无已检验结论"
            return {"ok": False, "message": "实验无已检验结论，报告生成失败"}
        entry["status"] = "已完成"
        entry["生成状态"] = "已完成"
        entry["pending"] = False
        entry["结论条数"] = str(len(significant) + len(attributed))
        dist_rows = store.rows("report_dist")
        dist_rows.append({
            "id": max((int(r.get("id", 0)) for r in dist_rows), default=0) + 1,
            "分发批次": f"RD-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "报告编码": entry.get("报告编码", ""),
            "接收方": "实验负责人",
            "分发渠道": "站内信",
            "送达状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"报告已生成（结论 {entry['结论条数']} 条）并排队分发"}

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试生成失败报告，重新校验数据依赖后回到排队。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "生成失败":
            return {"ok": False, "message": "仅生成失败报告可重试"}
        exp_code = str(entry.get("实验编码", ""))
        has_result = any(
            r.get("实验编码") == exp_code and r.get("status") == "已确认"
            for r in store.rows("significance_test")
        )
        if not has_result:
            return {"ok": False, "message": "依赖的检验结论仍未就绪，暂不能重试"}
        entry["status"] = "排队中"
        entry["生成状态"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"报告已回到排队（第 {entry['重试次数']} 次重试）"}

    def batch_generate(self) -> dict[str, Any]:
        """批量生成全部排队中报告。"""
        queued = [r for r in store.rows(self.MODULE) if r.get("status") == "排队中"]
        done, failed = 0, 0
        for row in queued:
            if self.generate_report(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                failed += 1
        return {"ok": True, "message": f"批量生成完成：成功 {done}，失败 {failed}"}

    def consume_from_attr_analysis(self, exp_code: str) -> list[dict[str, Any]]:
        """读取归因分析层的因子权重，编排报告归因章节。"""
        return [
            {"贡献因子": r.get("贡献因子", ""), "贡献权重": r.get("贡献权重", "")}
            for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        ]

    def consume_from_exp_design(self, exp_code: str) -> dict[str, Any]:
        """读取实验设计层方案信息，写入报告背景章节。"""
        exp = next((r for r in store.rows("exp_design") if r.get("实验编码") == exp_code), None)
        if exp is None:
            return {"ok": False, "message": f"实验方案 {exp_code} 不存在"}
        return {
            "ok": True,
            "实验名称": exp.get("实验名称", ""),
            "分组策略": exp.get("分组策略", ""),
            "状态": exp.get("status", ""),
        }

    def compose_summary(self, entry_id: int) -> dict[str, Any]:
        """合成报告摘要文本，包含显著结论与归因因子数量。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "报告不存在"}
        exp_code = str(entry.get("实验编码", ""))
        sig_count = sum(
            1 for r in store.rows("significance_test")
            if r.get("实验编码") == exp_code and r.get("status") == "已确认"
        )
        attr_count = sum(
            1 for r in store.rows("attr_analysis")
            if r.get("实验编码") == exp_code and r.get("status") == "已归因"
        )
        return {
            "ok": True,
            "摘要": f"实验 {exp_code} 共 {sig_count} 条显著结论、{attr_count} 个归因因子",
            "数据窗口": entry.get("数据窗口", ""),
        }

    def archive_finished(self, entry_id: int) -> dict[str, Any]:
        """把完成报告的结论写入实验归档库。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已完成":
            return {"ok": False, "message": "仅已完成报告可归档"}
        archive_rows = store.rows("exp_archive")
        archive_rows.append({
            "id": max((int(r.get("id", 0)) for r in archive_rows), default=0) + 1,
            "实验编码": entry.get("实验编码", ""),
            "结束时间": datetime.now(timezone.utc).isoformat(),
            "结论摘要": f"报告 {entry.get('报告编码', '')} 结论 {entry.get('结论条数', '0')} 条",
            "归档位置": f"report/{entry.get('报告编码', '')}",
            "保留等级": "标准",
            "status": "待归档",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "报告结论已写入归档库"}
