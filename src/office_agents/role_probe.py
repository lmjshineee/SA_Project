"""T02: validate each real role independently against a fixed synthetic case."""

import json
from pathlib import Path

from .config import load_settings
from .models.chat import ChatModel, ModelError
from .roles import Roles
from .schemas import (
    EvidenceBundle,
    ReportDraft,
    ReportPlan,
    TaskSpec,
    validate_draft,
    validate_evidence,
    validate_plan,
    validate_review,
)
from .statistics import snapshot
from .verify import environment, utc_now
from .workflow import Run, digest


def main():
    base = Path("evidence/delivery/admin-q1")
    manifest = json.loads((base / "manifest.json").read_text())
    source = json.loads((base / "summary.json").read_text())
    refs = source["refs"]

    # Every role receives the same archived upstream fixture, regardless of other probe outputs.
    def fixture(kind, schema):
        return schema.model_validate_json((base / refs[kind]).read_bytes())

    task = fixture("task", TaskSpec)
    plan = fixture("plan", ReportPlan)
    evidence = fixture("evidence", EvidenceBundle)
    draft = fixture("draft", ReportDraft)
    settings = load_settings()
    run = Run(Path("runs"), "live", manifest["request"], True, None)
    env = environment()
    env["code"].pop("status", None)
    run.write(
        "manifest.json",
        {
            "run_id": run.id,
            "mode": "live",
            "scope": "T02 independent roles",
            "fixture_run": source["run_id"],
            "request": run.request,
            "controlled_injection": True,
            "config": settings.public_config(),
            "environment": env,
            "started_at": utc_now(),
        },
    )
    run.hashes = snapshot(
        {p.name: p.read_bytes() for p in (base / "inputs").iterdir()},
        run.path / "inputs",
    )
    if run.hashes != manifest["input_sha256"]:
        raise ValueError("来源快照不一致")
    model = ChatModel(settings, run.id)
    roles = Roles(model, run)
    outcomes = {}
    try:
        for role in ("manager", "planner", "data", "writer", "checker"):
            run.role = role
            run.event("agent_started", fixture=source["run_id"])
            if role == "manager":
                obj = roles.manager(run.request, list(task.input_file_ids))
                ok = (obj.department, obj.period, obj.required_sections) == (
                    task.department,
                    task.period,
                    task.required_sections,
                )
            elif role == "planner":
                obj = roles.planner(task, None)
                validate_plan(task, obj)
                ok = True
            elif role == "data":
                obj = roles.data(task, plan, None)
                validate_evidence(plan, obj)
                ok = obj == evidence
            elif role == "writer":
                obj = roles.writer(plan, evidence, refs, None)
                validate_draft(
                    obj,
                    plan,
                    evidence,
                    plan_ref=refs["plan"],
                    evidence_ref=refs["evidence"],
                )
                ok = True
            else:
                changed = draft.model_copy(deep=True)
                changed.claims[0].value += 1
                changed_ref = run.save("checker-input", changed)
                sha = digest(run.path / changed_ref)
                # No rule issue supplied: this probe checks the model's own semantic finding.
                obj = roles.checker(
                    plan, evidence, changed, {**refs, "draft": changed_ref}, sha, None
                )
                validate_review(
                    obj,
                    changed,
                    plan,
                    draft_ref=changed_ref,
                    draft_sha256=sha,
                    evidence_ref=refs["evidence"],
                )
                ok = obj.decision == "changes_required" and any(
                    i.category == "metric" for i in obj.issues
                )
            ref = run.save(role, obj)
            outcomes[role] = ok
            run.event("agent_completed", ref=ref, matched=ok)
    except ModelError as exc:
        run.event("probe_failed", code=exc.code, detail=str(exc))
    finally:
        model.close()
        run.write(
            "summary.json",
            {
                "run_id": run.id,
                "mode": "live",
                "calls": run.calls,
                "results": outcomes,
                "ended_at": utc_now(),
            },
        )
        print(run.path, outcomes)
    return 0 if len(outcomes) == 5 and all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
