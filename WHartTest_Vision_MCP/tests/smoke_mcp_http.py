import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ.get("VISION_MCP_SMOKE_URL", "http://127.0.0.1:18010/mcp")
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            required = {
                "extract_requirement_images",
                "extract_text_from_screenshot",
                "analyze_requirement_ui",
                "ui_diff_check",
                "compare_page_models",
                "compare_requirement_with_page_model",
            }
            missing = required - set(names)
            if missing:
                raise SystemExit(f"missing tools: {sorted(missing)}")
            image_path = os.environ.get("VISION_MCP_SMOKE_IMAGE", "")
            if image_path:
                ocr = await session.call_tool("extract_text_from_screenshot", {"image_path": image_path, "provider": "rapidocr"})
                if ocr.isError:
                    raise SystemExit(f"OCR MCP call failed: {ocr.content}")
            diff = await session.call_tool("compare_page_models", {
                "expected_page_model_json": json.dumps({"elements": [{"type": "button", "name": "查询"}]}, ensure_ascii=False),
                "actual_page_model_json": json.dumps({"elements": [{"type": "button", "name": "查询"}]}, ensure_ascii=False),
            })
            if diff.isError:
                raise SystemExit(f"page model comparison MCP call failed: {diff.content}")
            print(f"MCP handshake OK; tools={len(names)}; names={','.join(names)}")


if __name__ == "__main__":
    asyncio.run(main())
