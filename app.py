"""Local Streamlit entry point. Run from the repository root."""

import json
from pathlib import Path
from threading import Lock

import streamlit as st

from office_agents.roles import SAMPLES
from office_agents.statistics import LABELS
from office_agents.workflow import run_report, sample_files

st.set_page_config(page_title="季度汇报工作台", page_icon="📑", layout="centered")
SCENARIOS = {
    "tech-q2": "技术部 · 第二季度",
    "admin-q1": "行政部 · 第一季度",
    "revision": "审核返工 · 真实模型",
    "revision-mock": "审核返工 · 替身测试",
    "missing": "缺少反馈资料 · 替身测试",
    "review-limit": "返工上限 · 替身测试",
    "timeout": "模型超时 · 真实运行",
}
NAMES = {
    "manager": "需求协调",
    "planner": "大纲规划",
    "data": "数据整理",
    "writer": "报告撰写",
    "checker": "审核",
}
STATES = {
    "succeeded": "审核通过",
    "needs_input": "待补充资料",
    "needs_review": "待人工复核",
    "failed": "运行失败",
}


@st.cache_resource
def workflow_lock():
    return Lock()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def show(path):
    summary = read(path / "summary.json")
    manifest = read(path / "manifest.json")
    refs = summary["refs"]
    st.subheader(STATES[summary["status"]])
    st.caption(
        f"{summary['run_id']}　·　{summary['mode']}　·　{summary['calls']} 次模型调用　·　{summary['revisions']} 轮返工"
    )
    st.write(manifest["request"])
    if summary["detail"]:
        st.warning(summary["detail"])
    report, trail, sources = st.tabs(["业务汇报", "协作与审核", "来源核对"])
    with report:
        if summary["report"]:
            body = (path / summary["report"]).read_text(encoding="utf-8")
            st.download_button(
                "下载 Markdown",
                body,
                file_name=f"{summary['run_id']}.md",
                mime="text/markdown",
                on_click="ignore",
            )
            st.markdown(body)
        else:
            st.write("当前运行未生成最终汇报。")
    with trail:
        events = [
            json.loads(line)
            for line in (path / "events.jsonl").read_text().splitlines()
        ]
        if manifest["controlled_injection"]:
            for e in events:
                if e["event_type"] == "controlled_injection":
                    st.info(
                        f"受控注入 · {e['claim_id']} 的初稿值由 {e['original']} 改为 {e['changed']}。"
                    )
        for e in events:
            if e["event_type"] == "agent_completed":
                label = f"第 {e['revision_round']} 轮 · {NAMES[e['agent']]}"
                obj = read(path / e["ref"])
                with st.expander(label, expanded=e["agent"] == "checker"):
                    if e["agent"] == "checker":
                        st.write(
                            "通过" if obj["decision"] == "approved" else "退回修订"
                        )
                        for issue in obj["issues"]:
                            st.write(issue["actual"] + " → " + issue["suggestion"])
                    st.json(obj, expanded=False)
            elif e["event_type"] == "revision_requested":
                st.write("↳ 返回" + NAMES[e["receiver"]])
    with sources:
        if "evidence" in refs:
            evidence = read(path / refs["evidence"])
            for m in evidence["metrics"]:
                with st.expander(
                    f"{LABELS[m['metric_key']]}　{m['display_value']}{m['unit']}"
                ):
                    st.write(m["formula"])
                    st.json(m["source_refs"])
        for name, sha in manifest.get("input_sha256", {}).items():
            with st.expander(name):
                st.code(sha)
                st.code(
                    (path / "inputs" / name).read_text(encoding="utf-8-sig"),
                    language="text",
                )


st.title("季度汇报工作台")
view = st.radio("工作方式", ["查看实验记录", "发起真实运行"], horizontal=True)
if view == "查看实验记录":
    available = [
        key
        for key in SCENARIOS
        if (Path("evidence/delivery") / key / "summary.json").is_file()
    ]
    chosen = (
        st.selectbox("已记录场景", available, format_func=SCENARIOS.get)
        if available
        else None
    )
    path = Path("evidence/delivery") / (chosen or "")
    if (path / "summary.json").exists():
        show(path)
    else:
        st.info("该场景尚无公开运行记录。")
else:
    scenario = st.selectbox("合成资料", list(SAMPLES), format_func=SCENARIOS.get)
    files = sample_files(scenario)
    with st.form("report"):
        request = st.text_area("汇报需求", SAMPLES[scenario], key="request-" + scenario)
        selected = st.multiselect(
            "允许使用的资料", list(files), default=list(files), key="files-" + scenario
        )
        st.caption("提交后会将需求和所选合成资料的统计结果发送到本机配置的模型服务。")
        submitted = st.form_submit_button("生成季度汇报", type="primary")
    if submitted:
        lock = workflow_lock()
        if not request.strip():
            st.warning("请填写汇报需求。")
        elif not lock.acquire(blocking=False):
            st.warning("另一份汇报正在生成，请稍后提交。")
        else:
            try:
                indicator = st.empty()

                def progress(e):
                    if e["event_type"] == "agent_started":
                        indicator.info(
                            f"第 {e['revision_round']} 轮 · {NAMES[e['agent']]}正在处理…"
                        )

                st.session_state.last_run = run_report(
                    request.strip(), {n: files[n] for n in selected}, progress=progress
                )
                indicator.empty()
            finally:
                lock.release()
    if "last_run" in st.session_state:
        show(st.session_state.last_run)
