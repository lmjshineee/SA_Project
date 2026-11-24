from pathlib import Path
import pytest

from office_agents.schemas import ReportPlan, TaskSpec
from office_agents.statistics import GROUPS, collect, snapshot

SAMPLES = Path(__file__).resolve().parents[1] / "data/sample"


def fixture(root, name, change=None):
    files = {p.name: p.read_bytes() for p in (SAMPLES / name).iterdir()}
    if change:
        change(files)
    hashes = snapshot(files, root)
    tech = name == "tech-q2"
    task = TaskSpec(title="季度汇报", period="2026-Q2" if tech else "2026-Q1", department="技术部" if tech else "行政部",
                    required_sections=["统计"], output_format="markdown", input_file_ids=list(files), missing_fields=[])
    keys = [k for g, keys in GROUPS.items() if (g != "service_feedback") == tech for k in keys]
    plan = ReportPlan(sections=[dict(section_id="s1", title="统计", purpose="复核", required=True, required_metrics=keys)])
    return hashes, task, plan


@pytest.mark.parametrize("name,expected", [
    ("tech-q2", {"project_count":"5","completed_project_count":"3","project_completion_rate":"60.00",
                 "attendance_rate":"96.00","achievement_count":"4"}),
    ("admin-q1", {"feedback_count":"20","satisfied_feedback_count":"17","satisfaction_rate":"85.00"})])
def test_metrics_and_scope(tmp_path, name, expected):
    def extra(files):
        if name == "admin-q1":
            files["service_feedback.csv"] += "other,技术部,2026-Q1,1\nprior,行政部,2025-Q4,1\n".encode()
    args = fixture(tmp_path, name, extra)
    result = collect(tmp_path, *args)
    assert {m.metric_key:m.display_value for m in result.metrics} == expected
    assert len(result.excerpts) == 2 and not result.missing_required
    for m in result.metrics:
        ref = m.source_refs[0]
        assert ref.sha256 == args[0][Path(ref.snapshot_path).name]
        assert "other" not in ref.record_ids and "prior" not in ref.record_ids


@pytest.mark.parametrize("mutation", ["duplicate", "encoding", "negative", "zero", "missing", "tamper", "symlink"])
def test_invalid_data(tmp_path, mutation):
    def change(files):
        name = "attendance.csv"
        rows = files[name].splitlines(keepends=True)
        if mutation == "duplicate":
            files[name] += rows[-1]
        if mutation == "encoding":
            files[name] += b"\xff"
        if mutation == "negative":
            files[name] = files[name].replace(b",38", b",-1")
        if mutation == "zero":
            files[name] = rows[0] + rows[1].replace(b"40,38",b"0,0")
        if mutation == "missing":
            files.pop(name)
    args = fixture(tmp_path, "tech-q2", change)
    if mutation == "tamper":
        (tmp_path / "attendance.csv").write_text("modified")
    if mutation == "symlink":
        path = tmp_path / "attendance.csv"
        path.unlink()
        path.symlink_to(SAMPLES / "tech-q2/attendance.csv")
    if mutation in {"zero", "missing"}:
        assert collect(tmp_path, *args).missing_required == ["attendance_rate"]
    else:
        with pytest.raises(ValueError):
            collect(tmp_path, *args)


def test_snapshot_refuses_external_path(tmp_path):
    with pytest.raises(ValueError):
        snapshot({"../attendance.csv": b"private"}, tmp_path)
