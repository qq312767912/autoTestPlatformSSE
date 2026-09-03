import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import vision_tools


class VisionToolsTests(unittest.TestCase):
    def test_vision_result_has_priority_over_ocr(self):
        with patch.object(vision_tools, "_vision", return_value={"content_summary": "AI内容", "business_rules": ["规则A"]}), patch.object(
            vision_tools, "_local_ocr", return_value=({"full_text": "模糊OCR"}, "")
        ):
            result = vision_tools.analyze_requirement_ui("page.png")
        self.assertEqual(result["content_summary"], "AI内容")
        self.assertEqual(result["primary_content_source"], "vision_model")
        self.assertEqual(result["ocr_role"], "fallback_only")

    def test_ocr_is_fallback_when_vision_fails(self):
        with patch.object(vision_tools, "_vision", side_effect=ValueError("bad vision")), patch.object(
            vision_tools, "_local_ocr", return_value=({"full_text": "OCR兜底"}, "")
        ):
            result = vision_tools.analyze_requirement_ui("page.png")
        self.assertTrue(result["vision_unavailable"])
        self.assertEqual(result["primary_content_source"], "local_ocr_fallback")

    def test_compare_page_models(self):
        expected = {"regions": [{"elements": [{"name": "查询", "type": "button"}, {"name": "状态", "type": "select"}]}]}
        actual = {"regions": [{"elements": [{"name": "查询", "type": "button"}, {"name": "重置", "type": "button"}]}]}
        result = vision_tools.compare_page_models(expected, actual)
        self.assertEqual(result["missing_on_actual"], ["状态"])
        self.assertEqual(result["unexpected_on_actual"], ["重置"])

    def test_extract_docx_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "req.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr("word/media/image1.png", b"png")
            result = vision_tools.extract_requirement_images(str(docx), str(Path(tmp) / "out"))
            self.assertEqual(result["total"], 1)
            self.assertTrue(Path(result["images"][0]["image_path"]).exists())


if __name__ == "__main__":
    unittest.main()
