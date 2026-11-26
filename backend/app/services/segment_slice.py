"""切片分析器领域服务：管理实验差异的维度切片、切片值计算与失败重试。

数据链路位置：下钻分析层，消费显著性检验与归因分析的差异结果，按维度切片
计算分组差异，输出给报告生成器形成下钻章节。

取数约束：切片必须从检验批次领取取样依据，计算时按该批次实际确认的区间与
窗口范围执行；批次被撤销或不再是有效确认时，切片不能采用它，必须指出缺依据
的窗口。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.significance_test import direction_of, parse_interval
from app.store import store


class SegmentSliceService(BaseModuleService):
    MODULE = "segment_slice"
    ENTITY = "切片结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["切片批次", "实验编码", "切片维度", "切片值", "分组差异", "置信区间", "检验批次"]
    REQUIRED_FIELDS = ["切片批次", "实验编码", "切片维度"]
    STATUS_ORDER = ["待计算", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []
    EXACT_FILTER_FIELDS = ["实验编码", "指标编码", "检验批次", "切片批次", "取证单号"]

    # ---- 业务扩展：维度切片与差异计算 ----

    def _basis_of(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """按切片登记的检验批次领取取样依据，返回批次与不可用原因。"""
        batch = str(entry.get("检验批次", "")).strip()
        window = str(entry.get("切片值", ""))
        if not batch:
            return None, f"窗口 {window} 未关联检验批次，缺少有效依据"
        test_rows = store.rows("significance_test")
        candidates = [
            row for row in test_rows
            if str(row.get("检验批次", "")) == batch
            and str(row.get("实验编码", "")).casefold() == str(entry.get("实验编码", "")).casefold()
        ]
        metric = str(entry.get("指标编码", "")).strip()
        if metric:
            candidates = [
                row for row in candidates
                if str(row.get("指标编码", "")).casefold() == metric.casefold()
            ]
        if not candidates:
            return None, f"窗口 {window} 的检验批次 {batch} 已不存在，缺少有效依据"
        basis = candidates[-1]
        if basis.get("status") == "已撤销":
            return basis, f"窗口 {window} 的检验批次 {batch} 已撤销，缺少有效依据"
        if basis.get("status") != "已确认":
            return basis, f"窗口 {window} 的检验批次 {batch} 当前为{basis.get('status')}，缺少有效依据"
        if parse_interval(basis.get("置信区间")) is None:
            return basis, f"窗口 {window} 的检验批次 {batch} 置信区间非法，缺少有效依据"
        return basis, ""

    def run_slice(self, entry_id: int) -> dict[str, Any]:
        """执行切片计算：按检验批次的实际确认区间与窗口范围取证后计算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待计算":
            return {"ok": False, "message": "仅待计算切片可执行"}
        exp_code = str(entry.get("实验编码", ""))
        dimension = str(entry.get("切片维度", ""))
        basis, reason = self._basis_of(entry)
        if basis is None or reason:
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = reason
            entry["缺失依据"] = reason
            return {"ok": False, "message": f"切片不能计算：{reason}"}
        if dimension not in ("分组", "人群", "归档恢复"):
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = f"不支持的切片维度 {dimension}"
            return {"ok": False, "message": f"切片维度 {dimension} 不受支持"}
        assign_rows = [
            r for r in store.rows("variant_assign")
            if r.get("实验编码") == exp_code and r.get("status") != "已剔除"
        ]
        if not assign_rows:
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["失败原因"] = "无分组数据可切片"
            return {"ok": False, "message": "实验无分组数据，切片计算失败"}
        ctrl = sum(1 for r in assign_rows if r.get("分组名称") == "对照组")
        treat = sum(1 for r in assign_rows if r.get("分组名称") == "实验组")
        diff = round((treat - ctrl) / max(len(assign_rows), 1), 4)
        interval = parse_interval(basis.get("置信区间"))
        direction = direction_of(basis.get("置信区间"))
        entry["分组差异"] = str(diff)
        entry["置信区间"] = basis.get("置信区间", "")
        entry["差异方向"] = direction
        entry["检验批次"] = basis.get("检验批次", "")
        entry["窗口范围"] = f"{basis.get('窗口起点', '')}~{basis.get('窗口终点', '')}"
        entry["取数依据"] = {
            "检验批次": basis.get("检验批次", ""),
            "检验窗口": basis.get("检验窗口", ""),
            "窗口起点": basis.get("窗口起点", ""),
            "窗口终点": basis.get("窗口终点", ""),
            "轮次": basis.get("轮次", ""),
            "修订状态": basis.get("修订状态", ""),
            "置信区间": basis.get("置信区间", ""),
            "差异方向": direction,
            "区间是否跨零": bool(interval and interval[0] <= 0 <= interval[1]),
        }
        entry["status"] = "已计算"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["计算时间"] = datetime.now(timezone.utc).isoformat()
        suffix = "，区间跨零，仅记无法判定差异方向" if direction == "无法判定" else ""
        return {
            "ok": True,
            "message": f"切片 {entry.get('切片值', dimension)} 按批次 {basis.get('检验批次')} 计算完成{suffix}",
        }

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败切片：仅在取样依据恢复有效后回到待计算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "仅计算失败切片可重试"}
        basis, reason = self._basis_of(entry)
        if reason:
            return {"ok": False, "message": f"仍缺少有效依据：{reason}"}
        reason_text = str(entry.get("失败原因", ""))
        if "维度" in reason_text:
            entry["切片维度"] = "分组"
        entry["status"] = "待计算"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = ""
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"切片已回到待计算（第 {entry['重试次数']} 次）"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行全部待计算切片。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待计算"]
        done = sum(1 for r in pending if self.run_slice(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"批量切片完成 {done}/{len(pending)}"}

    def consume_from_significance_test(self, exp_code: str) -> list[dict[str, Any]]:
        """读取有效确认的检验批次作为切片差异对照基线，排除已撤销与未确认。"""
        return [
            {
                "指标编码": row.get("指标编码", ""),
                "检验批次": row.get("检验批次", ""),
                "检验窗口": row.get("检验窗口", ""),
                "置信区间": row.get("置信区间", ""),
                "差异方向": direction_of(row.get("置信区间")),
            }
            for row in store.rows("significance_test")
            if str(row.get("实验编码", "")) == exp_code
            and row.get("status") == "已确认"
            and parse_interval(row.get("置信区间")) is not None
        ]

    def consume_from_cohort_builder(self, cohort_code: str) -> dict[str, Any]:
        """读取人群圈选器的人群包信息，限定切片范围。"""
        cohort = next((r for r in store.rows("cohort_builder") if r.get("人群编码") == cohort_code), None)
        if cohort is None:
            return {"ok": False, "message": f"人群包 {cohort_code} 不存在"}
        return {
            "ok": cohort.get("status") == "已生成",
            "人群规模": cohort.get("人群规模", ""),
            "状态": cohort.get("status", ""),
        }

    def produce_to_report_gen(self, exp_code: str) -> dict[str, Any]:
        """把已计算切片推送给报告生成器，登记下钻章节报告。"""
        computed = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已计算"
        ]
        if not computed:
            return {"ok": False, "message": "实验无已计算切片"}
        report_rows = store.rows("report_gen")
        report_rows.append({
            "id": max((int(r.get("id", 0)) for r in report_rows), default=0) + 1,
            "报告编码": f"RPT-SG-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "报告模板": "切片下钻模板",
            "数据窗口": "最近7天",
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"下钻报告已排队（覆盖 {len(computed)} 条切片）"}

    def export_slice_matrix(self, exp_code: str) -> list[dict[str, Any]]:
        """导出实验的切片矩阵，供指标查询服务提供下钻数据。"""
        return [
            {
                "切片维度": r.get("切片维度", ""),
                "切片值": r.get("切片值", ""),
                "分组差异": r.get("分组差异", ""),
                "置信区间": r.get("置信区间", ""),
                "检验批次": r.get("检验批次", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已计算"
        ]
