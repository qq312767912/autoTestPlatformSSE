import unittest

from .persistent_client import enhance_mcp_tools, normalize_browser_run_code


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = "执行浏览器代码"
        self.calls = []

        async def coroutine(**kwargs):
            self.calls.append(kwargs)
            return kwargs

        self.coroutine = coroutine


class BrowserRunCodeGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_wraps_top_level_javascript_statements(self):
        result = normalize_browser_run_code("var title = await page.title();\nreturn title;")
        self.assertTrue(result.startswith("async (page) => {"))
        self.assertIn("var title", result)

    def test_keeps_existing_async_function(self):
        code = "async (page) => { return await page.title(); }"
        self.assertEqual(normalize_browser_run_code(code), code)

    def test_removes_markdown_fence_before_wrapping(self):
        result = normalize_browser_run_code("```js\nconst title = await page.title();\nreturn title;\n```")
        self.assertNotIn("```", result)
        self.assertTrue(result.startswith("async (page) => {"))

    async def test_enhanced_tool_normalizes_code_before_call(self):
        tool = _FakeTool("browser_run_code_unsafe")
        enhance_mcp_tools([tool])

        result = await tool.coroutine(code="const title = await page.title();")

        self.assertTrue(result["code"].startswith("async (page) => {"))
        self.assertIn("调用约束", tool.description)

    async def test_unrelated_tool_is_not_changed(self):
        tool = _FakeTool("browser_click")
        original = tool.coroutine
        enhance_mcp_tools([tool])

        self.assertIs(tool.coroutine, original)
        self.assertNotIn("调用约束", tool.description)


if __name__ == "__main__":
    unittest.main()
