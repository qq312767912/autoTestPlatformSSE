import pytest

from vision_mcp import server
from vision_mcp.server import _json_result


def test_json_result_accepts_fenced_json():
    assert _json_result('```json\n{"ok": true}\n```') == {"ok": True}


def test_json_result_rejects_non_json():
    with pytest.raises(ValueError, match="有效JSON"):
        _json_result("not-json")


def test_requirement_analysis_calls_vision_before_ocr(monkeypatch):
    calls = []

    def vision(prompt, system, images, max_tokens=4096):
        calls.append("vision")
        assert "本地OCR结果" not in prompt
        return {"content_summary": "AI识别结果", "overall_confidence": 0.95}

    def ocr(_path):
        calls.append("ocr")
        return {"full_text": "模糊OCR", "text_blocks": [], "average_confidence": 0.5}

    monkeypatch.setattr(server, "_vision", vision)
    monkeypatch.setattr(server, "local_ocr", ocr)
    result = server.analyze_requirement_ui("page.png")

    assert calls == ["vision", "ocr"]
    assert result["content_summary"] == "AI识别结果"
    assert result["ocr"]["full_text"] == "模糊OCR"
    assert result["primary_content_source"] == "vision_model"
    assert result["ocr_role"] == "fallback_only"


def test_requirement_analysis_uses_ocr_only_when_vision_fails(monkeypatch):
    monkeypatch.setattr(server, "_vision", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad vision")))
    monkeypatch.setattr(
        server,
        "local_ocr",
        lambda _path: {"full_text": "OCR兜底", "text_blocks": [], "average_confidence": 0.8},
    )

    result = server.analyze_requirement_ui("page.png")
    assert result["vision_unavailable"] is True
    assert result["primary_content_source"] == "local_ocr_fallback"
    assert result["ocr"]["full_text"] == "OCR兜底"


def test_ocr_failure_does_not_discard_vision_result(monkeypatch):
    monkeypatch.setattr(server, "_vision", lambda *args, **kwargs: {"table_markdown": "|A|\n|---|"})
    monkeypatch.setattr(server, "local_ocr", lambda _path: (_ for _ in ()).throw(RuntimeError("ocr unavailable")))

    result = server.analyze_requirement_ui("page.png")
    assert result["table_markdown"] == "|A|\n|---|"
    assert result["primary_content_source"] == "vision_model"
    assert result["ocr"]["provider"] == "unavailable"
