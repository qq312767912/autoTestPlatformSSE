import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class SourceFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=2_000_000)


class ScanRequest(BaseModel):
    files: list[SourceFile] = Field(min_length=1, max_length=300)


app = FastAPI(title="WHartTest Semgrep Scanner", docs_url=None, redoc_url=None)
RULES = Path(os.environ.get("SEMGREP_RULES", "/app/rules/semgrep.yml"))


@app.get("/health")
def health():
    return {"status": "ok", "semgrep": shutil.which("semgrep") is not None}


def safe_relative_path(raw: str) -> Path:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail=f"非法文件路径：{raw}")
    return Path(*path.parts)


@app.post("/scan")
def scan(request: ScanRequest):
    with tempfile.TemporaryDirectory(prefix="wharttest-semgrep-") as directory:
        root = Path(directory)
        targets = []
        for item in request.files:
            relative = safe_relative_path(item.path)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8", errors="replace")
            targets.append(str(relative))
        try:
            result = subprocess.run(
                ["semgrep", "scan", "--config", str(RULES), "--json", "--metrics", "off", "--disable-version-check", *targets],
                cwd=root, capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Semgrep扫描超过180秒") from exc
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail=(result.stderr or "Semgrep未返回JSON")[-1000:]) from exc
        payload["scanner_exit_code"] = result.returncode
        return payload
