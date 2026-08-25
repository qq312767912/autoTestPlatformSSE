"""OpenAI 兼容视觉 API 客户端。"""

import base64
import mimetypes
import time
from pathlib import Path
from typing import Optional

import httpx

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


class VisionClient:
    """OpenAI 兼容视觉客户端"""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        model: str = "glm-4.6v-flash",
        chat_completions_path: str = "/chat/completions",
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.chat_completions_path = "/" + chat_completions_path.strip("/")
        self.max_retries = max(0, max_retries)
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_seconds))

    def _encode_image(self, image_path: str) -> str:
        """将本地图片编码为 base64 data URL"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")

        size = path.stat().st_size
        if size > MAX_IMAGE_SIZE:
            raise ValueError(f"图片过大 ({size} bytes)，最大 {MAX_IMAGE_SIZE} bytes")

        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{data}"

    def chat(
        self,
        prompt: str,
        image_paths: Optional[list[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """调用视觉模型进行图像理解

        Args:
            prompt: 用户提问文本
            image_paths: 图片路径或 URL 列表（本地路径自动编码为 base64）
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数

        Returns:
            模型生成的文本
        """
        # 构建 content 数组 (OpenAI 格式)
        content: list[dict] = []

        if image_paths:
            for img_path in image_paths:
                if img_path.startswith(("http://", "https://", "data:")):
                    url = img_path
                else:
                    url = self._encode_image(img_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })

        content.append({"type": "text", "text": prompt})

        # 构建 messages
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        headers: dict = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        endpoint = f"{self.base_url}{self.chat_completions_path}"
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
                if attempt >= self.max_retries:
                    raise
                time.sleep(0.5 * (2 ** attempt))
        assert response is not None
        data = response.json()

        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content_text = message.get("content", "")
            return content_text

        raise ValueError("视觉模型响应中缺少 choices[0].message.content")

    def close(self):
        self._client.close()
