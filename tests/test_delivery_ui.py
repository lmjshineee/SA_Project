"""Check the first-time demo flow without any real network credentials."""

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_archive_and_form_are_separate():
    at = AppTest.from_file(Path("app.py").resolve()).run()
    assert not at.exception
    assert "技术部" in at.selectbox[0].options[0]
    at.selectbox[0].set_value("admin-q1").run()
    assert any("85.00%" in x.value for x in at.markdown)
    for key in ["missing", "review-limit", "timeout"]:
        at.selectbox[0].set_value(key).run()
        assert not at.exception and at.warning
        assert not at.get("download_button")
    at.radio[0].set_value("发起真实运行").run()
    at.selectbox[0].set_value("admin-q1").run()
    assert "行政部" in at.text_area[0].value
    assert set(at.multiselect[0].value) == {"notes.txt", "service_feedback.csv"}
    with patch(
        "office_agents.workflow.run_report",
        return_value=Path("evidence/delivery/admin-q1"),
    ) as start:
        at.button[0].click().run()
        at.run()
        assert start.call_count == 1
        other = AppTest.from_file(Path("app.py").resolve()).run()
        other.radio[0].set_value("发起真实运行").run()
        assert "last_run" not in other.session_state and not other.exception
    assert not at.exception
