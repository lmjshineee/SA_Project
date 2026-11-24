"""HTTP test double exercises graph entry points, including refused exports."""

import json

import httpx
import pytest

from office_agents.roles import SAMPLES, SECTION_KEYS
from office_agents.workflow import digest, run_report, sample_files


class OfficeDouble:
    def __init__(self, fault):
        self.fault = fault
        self.count = 0

    def __call__(self, request):
        self.count += 1
        if self.fault == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        if self.fault == "401":
            return httpx.Response(401)
        msgs = json.loads(request.content)["messages"]
        prompt = msgs[0]["content"]
        payload = json.loads(msgs[1]["content"])
        calls = []
        if self.fault == "json":
            result = "wrong schema"
        elif "解析办公" in prompt:
            tech = "技术" in payload["request"]
            titles = (
                list(SECTION_KEYS)[:3] + ["下一步计划"]
                if tech
                else ["服务满意度", "存在问题", "改进建议"]
            )
            missing = self.fault == "department"
            result = {
                "title": "季度汇报",
                "department": None if missing else ("技术部" if tech else "行政部"),
                "period": "2026-Q2" if tech else "2026-Q1",
                "required_sections": titles,
                "output_format": "markdown",
                "input_file_ids": payload["file_ids"],
                "missing_fields": ["department"] if missing else [],
            }
        elif "大纲" in prompt and "section_id" in prompt and "规划" in prompt:
            result = {
                "sections": [
                    {
                        "title": t,
                        "section_id": f"s{i}",
                        "purpose": t,
                        "required": True,
                        "required_metrics": payload["section_metrics"][t],
                    }
                    for i, t in enumerate(payload["task"]["required_sections"])
                ]
            }
        elif "数据整理员" in prompt:
            if any(m["role"] == "tool" for m in msgs):
                result = {"summary": "统计完成"}
            else:
                calls = [
                    {
                        "id": "call1",
                        "type": "function",
                        "function": {
                            "name": "wrong"
                            if self.fault == "tool"
                            else "collect_evidence",
                            "arguments": "{}",
                        },
                    }
                ]
                result = None
        elif "撰写简明" in prompt:
            result = {
                "sections": [
                    {
                        "section_id": s["section_id"],
                        "content": "本节结合季度资料。",
                        "metric_ids": s["required_metrics"],
                    }
                    for s in payload["plan"]["sections"]
                ]
            }
        else:
            issues = []
            if self.fault in {"block", "data", "plan"} and (
                self.fault == "block" or self.count == 6
            ):
                category = {"block": "content", "data": "data", "plan": "plan"}[
                    self.fault
                ]
                issues = [
                    {
                        "issue_id": "test",
                        "category": category,
                        "severity": "blocker",
                        "claim_id": None,
                        "section_id": None,
                        "expected": "按范围核对",
                        "actual": "测试退回",
                        "suggestion": "再次核验",
                    }
                ]
            result = {"issues": issues}
        msg = {"role": "assistant", "content": json.dumps(result), "tool_calls": calls}
        return httpx.Response(
            200,
            json={
                "model": "test-double",
                "choices": [
                    {"message": msg, "finish_reason": "tool_calls" if calls else "stop"}
                ],
            },
        )


@pytest.mark.parametrize(
    "scenario,fault,status,revisions,calls",
    [
        ("tech-q2", "", "succeeded", 0, 6),
        ("admin-q1", "", "succeeded", 0, 6),
        ("tech-q2", "inject", "succeeded", 1, 8),
        ("tech-q2", "block", "needs_review", 2, 10),
        ("admin-q1", "data", "succeeded", 1, 10),
        ("admin-q1", "plan", "succeeded", 1, 11),
        ("admin-q1", "missing", "needs_input", 0, 4),
        ("tech-q2", "department", "needs_input", 0, 1),
        ("admin-q1", "401", "failed", 0, 1),
        ("tech-q2", "timeout", "failed", 0, 2),
        ("admin-q1", "json", "failed", 0, 2),
        ("tech-q2", "tool", "failed", 0, 3),
    ],
)
def test_run(tmp_path, scenario, fault, status, revisions, calls):
    files = sample_files(scenario)
    if fault == "missing":
        files.pop("service_feedback.csv")
    double = OfficeDouble(fault)
    path = run_report(
        SAMPLES[scenario],
        files,
        root=tmp_path,
        transport=httpx.MockTransport(double),
        injection=fault == "inject",
    )
    result = json.loads((path / "summary.json").read_text())
    assert (result["mode"], result["status"], result["revisions"], result["calls"]) == (
        "mock",
        status,
        revisions,
        calls,
    ), result
    assert double.count == calls
    assert (path / "report.md").exists() == (status == "succeeded")
    if status == "succeeded":
        review = json.loads((path / result["refs"]["review"]).read_text())
        assert review["draft_sha256"] == digest(path / result["refs"]["draft"])
        assert ("60.00%" if scenario == "tech-q2" else "85.00%") in (
            path / "report.md"
        ).read_text()
    if fault == "inject":
        assert (path / "artifacts/draft-before-injection-r0.json").exists()
        assert (
            json.loads((path / "artifacts/review-r0.json").read_text())["decision"]
            == "changes_required"
        )
