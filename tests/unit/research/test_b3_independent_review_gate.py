from pathlib import Path


GATE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "b3_independent_review_gate.py"


def test_independent_review_gate_runs_review_once_and_has_no_retry_or_broker_path() -> None:
    source = GATE_PATH.read_text(encoding="utf-8")
    assert "subprocess.run(" in source
    assert source.count("subprocess.run(") == 1
    assert 'REVIEW_SCRIPT = Path("scripts/b3_independent_review.py")' in source
    assert "validate_review_evidence_refs" in source
    assert "submit_order(" not in source
    assert "execute_retrieval_plan" not in source
    assert "execute_synthesis_runtime" not in source
    assert "while " not in source
    assert "for attempt" not in source
