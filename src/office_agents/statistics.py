"""Read only known snapshot files and calculate reproducible office statistics."""
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
from pathlib import Path
import re

from .schemas import EvidenceBundle, Excerpt, Metric, ReportPlan, SourceRef, TaskSpec

COLUMNS = {
    "projects": "project_id department period name status progress_percent",
    "attendance": "member_id department period scheduled_days attended_days",
    "achievements": "achievement_id department period category title",
    "service_feedback": "feedback_id department period score",
}
GROUPS = {
    "projects": ("project_count", "completed_project_count", "project_completion_rate"),
    "attendance": ("attendance_rate",),
    "achievements": ("achievement_count",),
    "service_feedback": ("satisfied_feedback_count", "feedback_count", "satisfaction_rate"),
}
LABELS = dict(zip((k for keys in GROUPS.values() for k in keys),
                  ("项目总数", "完成项目数", "项目完成率", "出勤率", "成果数", "满意反馈数", "反馈总数", "满意度"), strict=True))
FILES = {k + ".csv" for k in COLUMNS} | {"notes.txt"}
LIMIT = 5 * 1024 * 1024


def snapshot(files: dict[str, bytes], root: Path) -> dict[str, str]:
    if len(files) > 10:
        raise ValueError("一次最多选择十份文件")
    hashes = {}
    for name, data in files.items():
        if name not in FILES or len(data) > LIMIT:
            raise ValueError("资料名称不在允许清单或超过 5 MiB")
        (root / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def number(row, key, maximum=None):
    s = row[key]
    if not re.fullmatch(r'[0-9]{1,12}', s) or (maximum is not None and int(s) > maximum):
        raise ValueError(f"{key} 必须是范围内的非负整数")
    return int(s)


def rows_for(kind, text, task):
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    columns = COLUMNS[kind].split()
    if reader.fieldnames != columns:
        raise ValueError(f"{kind}.csv 表头须为 {','.join(columns)}")
    seen, selected = set(), []
    for row in reader:
        rid = row[columns[0]]
        if (None in row or any(not v or not v.strip() for v in row.values())
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', rid)
                or not re.fullmatch(r'[0-9]{4}-Q[1-4]', row["period"])):
            raise ValueError(f"{kind}.csv 第 {reader.line_num} 行无效")
        identity = (row["department"], row["period"], rid) if kind == "attendance" else rid
        if identity in seen:
            raise ValueError(f"{kind}.csv 记录 ID 重复")
        seen.add(identity)
        if kind == "projects":
            progress = number(row, "progress_percent", 100)
            if (row["status"] not in {"planned", "in_progress", "completed", "blocked"}
                    or row["status"] == "completed" and progress != 100):
                raise ValueError("项目状态与进度无效")
        elif kind == "attendance":
            number(row, "attended_days", number(row, "scheduled_days"))
        elif kind == "service_feedback" and not number(row, "score", 5):
            raise ValueError("反馈评分必须为 1 至 5")
        if (row["department"], row["period"]) == (task.department, task.period):
            selected.append(row)
    return selected


def measures(kind, rows):
    total = len(rows)
    if kind == "projects":
        done = sum(r["status"] == "completed" for r in rows)
        return [(total, "项目记录数"), (done, "状态为 completed 的记录数"),
                (Decimal(done) * 100 / total, "完成项目数 ÷ 项目数 × 100")]
    if kind == "attendance":
        days = sum(int(r["scheduled_days"]) for r in rows)
        attended = sum(int(r["attended_days"]) for r in rows)
        return [(Decimal(attended) * 100 / days if days else None, "总实到天数 ÷ 总应到天数 × 100")]
    if kind == "achievements":
        return [(total, "唯一成果记录数")]
    satisfied = sum(int(r["score"]) >= 4 for r in rows)
    return [(satisfied, "得分至少为 4 的反馈数"), (total, "有效反馈样本数"),
            (Decimal(satisfied) * 100 / total, "满意反馈数 ÷ 有效反馈样本数 × 100")]


def collect(root: Path, hashes: dict, task: TaskSpec, plan: ReportPlan) -> EvidenceBundle:
    wanted = {key for section in plan.sections for key in section.required_metrics}
    required = {key for section in plan.sections if section.required for key in section.required_metrics}
    metrics, excerpts, limitations = [], [], []
    for name, digest in hashes.items():
        if name not in FILES:
            raise ValueError("未知资料")
        path = root / name
        if path.is_symlink() or path.resolve().parent != root.resolve():
            raise ValueError("快照路径越界")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("快照哈希不匹配")
        text = data.decode("utf-8-sig")
        kind = path.stem
        ref = dict(file_id=kind, snapshot_path=f"inputs/{name}", sha256=digest)
        if kind == "notes":
            lines = text.splitlines()
            if (len(lines) < 2 or not lines[0].startswith("department: ")
                    or not re.fullmatch(r"period: [0-9]{4}-Q[1-4]", lines[1])):
                raise ValueError("notes.txt 缺少合法范围头")
            if lines[:2] == [f"department: {task.department}", f"period: {task.period}"]:
                for i, line in enumerate(lines[2:], 3):
                    if line.strip():
                        source = SourceRef(**ref, record_ids=[], line_range=[i, i])
                        excerpts.append(Excerpt(excerpt_id=f"N{i}", text=line, source_ref=source))
            continue
        rows = rows_for(kind, text, task)
        if not rows:
            continue
        ids = [row[COLUMNS[kind].split()[0]] for row in rows]
        source = SourceRef(**ref, record_ids=ids, line_range=None)
        for key, (value, formula) in zip(GROUPS[kind], measures(kind, rows), strict=True):
            if key not in wanted:
                continue
            if value is None:
                limitations.append(f"{LABELS[key]}的分母为零")
                continue
            percent = key.endswith("_rate")
            value = Decimal(value)
            display = str(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)) if percent else str(value)
            unit = "%" if percent else ("条" if kind == "service_feedback" else "个")
            metrics.append(Metric(metric_id=key, metric_key=key, raw_value=value, display_value=display,
                                  unit=unit, formula=formula, source_refs=[source]))
    missing = sorted(required - {m.metric_key for m in metrics})
    if not excerpts and any(s.required and any(k in s.title for k in ("问题", "计划", "建议", "风险")) for s in plan.sections):
        missing.append("notes")
    if missing:
        limitations.append("请补充本部门、本季度的对应资料：" + "、".join(LABELS.get(k, k) for k in missing))
    return EvidenceBundle(metrics=metrics, excerpts=excerpts, missing_required=missing, limitations=limitations)
