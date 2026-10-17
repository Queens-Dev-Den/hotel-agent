from datetime import datetime, timedelta

import token_report
from contracts import EmailReceipt, Run, Telemetry


def make_run(run_id, minute, total_s, parse, extract, agent):
    start = datetime(2026, 10, 17, 14, minute)
    return Run(
        run_id=run_id, prompt="p", started_at=start,
        email=EmailReceipt(message_id="m", sent_at=start + timedelta(seconds=total_s)),
        telemetry=Telemetry(
            stage_ms={"brain": 5, "search": 4000, "call": 150000, "email": 900},
            tokens={"backboard_in": parse + extract - 20, "backboard_out": 20,
                    "backboard_parse": parse, "backboard_extract": extract, "agent_llm": agent},
        ),
    )


def test_report_averages():
    out = token_report.report([make_run("run-aaaa1", 0, 200, 0, 0, 3000), make_run("run-bbbb2", 5, 180, 160, 200, 4000)])
    assert "| run-aaaa | 200.0 | 0.0 | 4.0 | 150.0 | 0.9 | 0 | 0 | 0 | 3,000 |" in out
    assert "| **avg of 2** | **190.0** |" in out
    assert "Backboard per session: avg 180 / 1,200 budget (max 360)." in out
    assert "Agent LLM per run (3 parallel calls): avg 3,500." in out


def test_main_archives_latest_once(tmp_path, monkeypatch, capsys):
    run_file = tmp_path / "run_telemetry.json"
    run_file.write_text(make_run("run-cccc3", 0, 150, 0, 0, 2500).model_dump_json())
    monkeypatch.setattr(token_report, "RUN_FILE", run_file)
    monkeypatch.setattr(token_report, "ARCHIVE", tmp_path / "telemetry_runs")
    token_report.main([])
    token_report.main([])
    assert [p.name for p in (tmp_path / "telemetry_runs").iterdir()] == ["run-cccc3.json"]
    assert capsys.readouterr().out.count("Archived") == 1


def test_main_no_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(token_report, "RUN_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(token_report, "ARCHIVE", tmp_path / "telemetry_runs")
    token_report.main([])
    assert "No runs found" in capsys.readouterr().out
