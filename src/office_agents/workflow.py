"""Execute a single office report and retain the artifacts behind every handoff."""

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from .config import Settings, load_settings
from .models.chat import ChatModel, ModelError
from .roles import SAMPLES, Roles, plain
from .schemas import (
    ContractError,
    EvidenceBundle,
    ReportDraft,
    ReportPlan,
    ReviewResult,
    TaskSpec,
    validate_draft,
    validate_evidence,
    validate_plan,
    validate_review,
)
from .statistics import FILES, LABELS, snapshot
from .verify import environment, utc_now


class State(TypedDict, total=False):
    task: TaskSpec
    plan: ReportPlan
    evidence: EvidenceBundle
    draft: ReportDraft
    review: ReviewResult
    refs: dict[str, str]
    status: str
    detail: str


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def markdown(task, plan, evidence, draft):
    lines = [f"# {task.title}", "", f"{task.department} · {task.period}"]
    sections = {section.section_id: section for section in draft.sections}
    claims = {claim.claim_id: claim for claim in draft.claims}
    metrics = {metric.metric_id: metric for metric in evidence.metrics}
    for section in plan.sections:
        if section.section_id not in sections:
            continue
        paragraph = sections[section.section_id]
        lines.extend(["", f"## {section.title}", "", paragraph.content])
        if paragraph.claim_ids:
            lines.extend(["", "| 指标 | 结果 | 来源 |", "| --- | --- | --- |"])
        for cid in paragraph.claim_ids:
            metric = metrics[claims[cid].metric_id]
            source = metric.source_refs[0]
            lines.append(
                f"| {LABELS[metric.metric_key]} | {metric.display_value}{metric.unit} | "
                f"{source.snapshot_path}（{', '.join(source.record_ids)}） |"
            )
    if evidence.excerpts:
        lines += ["", "## 资料摘录"]
        for ex in evidence.excerpts:
            ref = ex.source_ref
            lines += [
                "",
                f"> {ex.text}",
                "",
                f"{ref.snapshot_path} · 第 {ref.line_range[0]} 行",
            ]
    return "\n".join(lines) + "\n"


class Run:
    def __init__(self, root, mode, request, injection, progress):
        self.id = "office-" + uuid4().hex[:12]
        self.path = root / self.id
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "inputs").mkdir()
        (self.path / "artifacts").mkdir()
        self.mode, self.request, self.injection = mode, request, injection
        self.progress = progress
        self.started = time.monotonic()
        self.role = None
        self.calls = self.revision = self.seq = 0
        self.hashes = {}
        self.refs = {}

    def check_budget(self):
        if time.monotonic() - self.started >= 600:
            raise ModelError("RUN_TIMEOUT", "运行超过十分钟期限")

    def write(self, name, obj):
        text = (
            obj.model_dump_json(indent=2)
            if isinstance(obj, BaseModel)
            else json.dumps(obj, ensure_ascii=False, indent=2)
        )
        (self.path / name).write_text(text + "\n", encoding="utf-8")

    def save(self, name, obj):
        ref = f"artifacts/{name}-r{self.revision}.json"
        self.write(ref, obj)
        return ref

    def event(self, kind, **fields):
        self.seq += 1
        event = dict(
            schema_version="1.0",
            event_id=uuid4().hex,
            sequence=self.seq,
            run_id=self.id,
            mode=self.mode,
            created_at=utc_now(),
            event_type=kind,
            agent=self.role,
            revision_round=self.revision,
            **fields,
        )
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.progress:
            self.progress(event)


NODES = {
    "manager": "task",
    "planner": "plan",
    "data": "evidence",
    "writer": "draft",
    "checker": "review",
}
NEXT = {"manager": "planner", "planner": "data", "data": "writer", "writer": "checker"}


def build_graph(run, roles):
    def execute(role, state):
        run.role = role
        run.check_budget()
        refs = dict(state["refs"])
        review = state.get("review")
        feedback = plain(review) if review else None
        run.event("agent_started", upstream=refs.copy())
        status, detail = "running", ""
        if role == "manager":
            value = roles.manager(run.request, [Path(n).stem for n in run.hashes])
            if not value.ready_for_planner:
                status, detail = (
                    "needs_input",
                    "请补充：" + "、".join(value.missing_fields),
                )
        elif role == "planner":
            value = roles.planner(state["task"], feedback)
            validate_plan(state["task"], value)
        elif role == "data":
            value = roles.data(state["task"], state["plan"], feedback)
            validate_evidence(state["plan"], value)
            if not value.ready_for_writer:
                status, detail = "needs_input", "；".join(value.limitations)
        elif role == "writer":
            value = roles.writer(state["plan"], state["evidence"], refs, feedback)
            if run.injection and run.revision == 0 and value.claims:
                before = run.save("draft-before-injection", value)
                claim = value.claims[0]
                changed = claim.model_copy(update={"value": claim.value + 1})
                value = value.model_copy(
                    update={"claims": [changed, *value.claims[1:]]}
                )
                run.event(
                    "controlled_injection",
                    before=before,
                    claim_id=claim.claim_id,
                    original=str(claim.value),
                    changed=str(changed.value),
                )
        else:
            draft = state["draft"]
            try:
                validate_draft(
                    draft,
                    state["plan"],
                    state["evidence"],
                    plan_ref=refs["plan"],
                    evidence_ref=refs["evidence"],
                )
                rule_issue = None
            except ContractError as exc:
                rule_issue = str(exc)
            sha = digest(run.path / refs["draft"])
            value = roles.checker(
                state["plan"], state["evidence"], draft, refs, sha, rule_issue
            )
            validate_review(
                value,
                draft,
                state["plan"],
                draft_ref=refs["draft"],
                draft_sha256=sha,
                evidence_ref=refs["evidence"],
            )
            if value.decision == "approved" and not rule_issue:
                status = "succeeded"
            elif run.revision >= 2:
                status, detail = "needs_review", "两轮返工后仍有阻断意见，请人工复核"
        key = NODES[role]
        refs[key] = run.save(key, value)
        run.refs = refs
        run.write(
            "state.json",
            {"status": status, "refs": refs, "revision_round": run.revision},
        )
        run.event("agent_completed", ref=refs[key])
        if role == "checker":
            run.event("handoff", sender=role, receiver="manager", ref=refs[key])
            if status != "succeeded":
                run.event("review_rejected", ref=refs[key])
            if status == "running":
                run.revision += 1
                run.event(
                    "revision_requested", receiver=value.repair_target, ref=refs[key]
                )
        elif status == "running":
            run.event("handoff", sender=role, receiver=NEXT[role], ref=refs[key])
        return {key: value, "refs": refs, "status": status, "detail": detail}

    graph = StateGraph(State)
    for role in NODES:

        def step(state, name=role):
            return execute(name, state)

        graph.add_node(role, step)
    graph.add_edge(START, "manager")
    for role, successor in NEXT.items():

        def route(state, target=successor):
            return target if state["status"] == "running" else END

        graph.add_conditional_edges(role, route, [successor, END])
    graph.add_conditional_edges(
        "checker",
        lambda s: s["review"].repair_target if s["status"] == "running" else END,
        ["planner", "data", "writer", END],
    )
    return graph.compile()


def run_report(
    request, files, *, root=Path("runs"), injection=False, progress=None, transport=None
):
    mode = "mock" if transport is not None else "live"
    run = Run(root, mode, request, injection, progress)
    started = utc_now()
    manifest = {
        "run_id": run.id,
        "mode": mode,
        "request": request,
        "controlled_injection": injection,
        "started_at": started,
        "environment": environment(),
    }
    # Keep private filenames from unrelated untracked changes out of public business logs.
    manifest["environment"]["code"].pop("status", None)
    run.write("manifest.json", manifest)
    model = None
    refs = {}
    status, code, detail = "failed", "", ""
    try:
        run.event("run_started")
        run.hashes = snapshot(files, run.path / "inputs")
        if transport is None:
            settings = load_settings()
        else:
            settings = Settings(api_key="offline-test", model="test-double")
        manifest.update(input_sha256=run.hashes, config=settings.public_config())
        run.write("manifest.json", manifest)
        model = ChatModel(settings, run.id, transport=transport)
        roles = Roles(model, run)
        state = build_graph(run, roles).invoke({"refs": {}, "status": "running"})
        status, detail, refs = state["status"], state["detail"], state["refs"]
        run.check_budget()
        if status == "succeeded":
            reviewed = ReportDraft.model_validate_json(
                (run.path / refs["draft"]).read_bytes()
            )
            if digest(run.path / refs["draft"]) != state["review"].draft_sha256:
                raise ValueError("最终草稿与审核指纹不一致")
            body = markdown(state["task"], state["plan"], state["evidence"], reviewed)
            temp = run.path / "report.tmp"
            temp.write_text(body, encoding="utf-8")
            temp.replace(run.path / "report.md")
            run.event("export_completed", sha256=digest(run.path / "report.md"))
        run.event("run_completed", status=status)
    except (ValueError, ModelError, OSError, csv.Error) as exc:
        status = "failed"
        code = exc.code if isinstance(exc, ModelError) else "INVALID_INPUT_OR_OUTPUT"
        if isinstance(exc, ValidationError):
            detail = "配置或结构字段无效：" + "、".join(
                str(e["loc"]) for e in exc.errors(include_input=False)
            )
        elif isinstance(exc, OSError):
            detail = "运行资料或产物读写失败"
        else:
            detail = str(exc)
        refs = run.refs
        run.event("run_failed", code=code, detail=detail)
    finally:
        if model:
            model.close()
    run.write(
        "state.json", {"status": status, "refs": refs, "revision_round": run.revision}
    )
    run.write(
        "summary.json",
        {
            "run_id": run.id,
            "mode": mode,
            "status": status,
            "detail": detail,
            "error_code": code,
            "calls": run.calls,
            "revisions": run.revision,
            "ended_at": utc_now(),
            "duration_ms": round((time.monotonic() - run.started) * 1000),
            "refs": refs,
            "report": "report.md" if status == "succeeded" else None,
        },
    )
    return run.path


def sample_files(scenario):
    if scenario not in SAMPLES:
        raise ValueError("未知样例")
    folder = Path("data/sample") / scenario
    return {
        p.name: p.read_bytes()
        for p in sorted(folder.iterdir())
        if p.name in FILES and not p.is_symlink()
    }


def main():
    parser = argparse.ArgumentParser(description="真实模型季度办公汇报（仅合成样例）")
    parser.add_argument("scenario", choices=SAMPLES)
    parser.add_argument(
        "--inject", action="store_true", help="在第一轮草稿中注入指标错误，验证审核返工"
    )
    args = parser.parse_args()
    path = run_report(
        SAMPLES[args.scenario], sample_files(args.scenario), injection=args.inject
    )
    print(path)
    print((path / "summary.json").read_text())
    return (
        0
        if json.loads((path / "summary.json").read_text())["status"] == "succeeded"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
