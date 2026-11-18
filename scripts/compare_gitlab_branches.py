#!/usr/bin/env python3
"""只读核对 GitLab 分支差异；用标准库运行，不上传代码或 Token。"""
import argparse
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="核对平台代码审查使用的分支比较数据")
    parser.add_argument("--url", required=True, help="GitLab 根地址，如 https://gitlab.example.com")
    parser.add_argument("--project", required=True, help="GitLab 项目 ID 或 namespace/repo")
    parser.add_argument("--base", required=True, help="基准分支")
    parser.add_argument("--head", required=True, help="目标分支")
    parser.add_argument("--token", help="可选；省略时安全输入，不出现在命令历史")
    parser.add_argument("--output", default=str(Path(__file__).with_name("branch-compare.json")))
    args = parser.parse_args()
    token = args.token or os.environ.get("GITLAB_TOKEN") or getpass.getpass("GitLab Token: ")
    root = args.url.rstrip("/")
    project = urllib.parse.quote(args.project, safe="")
    context = ssl._create_unverified_context()

    def get(path, params=None):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{root}/api/v4/projects/{project}/{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60, context=context) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitLab HTTP {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from exc

    base = get("repository/branches/" + urllib.parse.quote(args.base, safe=""))
    head = get("repository/branches/" + urllib.parse.quote(args.head, safe=""))
    compare = get("repository/compare", {"from": base["commit"]["id"], "to": head["commit"]["id"], "straight": "false"})
    rows = []
    for item in compare.get("diffs", []):
        diff = item.get("diff") or ""
        added = sum(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines())
        deleted = sum(line.startswith("-") and not line.startswith("---") for line in diff.splitlines())
        rows.append({"path": item.get("new_path"), "old_path": item.get("old_path"), "added": added, "deleted": deleted,
                     "new_file": item.get("new_file"), "deleted_file": item.get("deleted_file"),
                     "renamed_file": item.get("renamed_file"), "too_large": item.get("too_large"), "collapsed": item.get("collapsed"),
                     "diff": diff})
    result = {"project": args.project, "base_branch": args.base, "head_branch": args.head,
              "base_sha": base["commit"]["id"], "head_sha": head["commit"]["id"],
              "compare_timeout": compare.get("compare_timeout"), "compare_same_ref": compare.get("compare_same_ref"),
              "commits_returned": len(compare.get("commits", [])), "files_returned": len(rows),
              "added_lines_in_returned_diffs": sum(row["added"] for row in rows),
              "deleted_lines_in_returned_diffs": sum(row["deleted"] for row in rows), "files": rows}
    output = Path(args.output).expanduser().resolve()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"基准 {args.base}: {result['base_sha']}\n目标 {args.head}: {result['head_sha']}")
    print(f"GitLab 返回：{len(rows)} 个文件，+{result['added_lines_in_returned_diffs']} / -{result['deleted_lines_in_returned_diffs']} 行")
    print(f"compare_timeout={result['compare_timeout']}；明细已保存：{output}")
    print("注意：行数只统计 API 返回的 diff 文本；若 GitLab 截断大文件，它不是仓库完整增删行数。")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError, KeyError, ValueError) as exc:
        print(f"比较失败：{exc}", file=sys.stderr)
        sys.exit(1)
