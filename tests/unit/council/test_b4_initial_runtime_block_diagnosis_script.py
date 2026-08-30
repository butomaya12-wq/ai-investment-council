from __future__ import annotations

from pathlib import Path


def test_block_diagnosis_script_compiles_and_has_no_openai_key_surface():
    path = Path("scripts/b4_diagnose_initial_runtime_block.py")
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    assert "OPENAI_API_KEY" not in source
    assert "StdlibResponsesTransport" not in source
    assert "--execute-paid" not in source
    assert "model_calls_performed_by_diagnosis\": 0" in source
    assert "rerun_authorized\": False" in source
