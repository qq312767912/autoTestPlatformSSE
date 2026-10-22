#!/usr/bin/env python3
"""Apply the reviewed code-review changes to the known intranet v3 baseline."""
import ast
import hashlib
import sys
from pathlib import Path

BASE_SHA256 = "8f208701e0e4d557b106b8a242a210654b7e2324e4025f6de792cc544be3b945"
NEW_MARKER = "# code-review-coverage-hotfix-v2"


def fail(message):
    raise SystemExit(message)


def function_block(source, name):
    lines = source.splitlines(keepends=True)
    module = ast.parse(source)
    matches = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        fail(f"Expected exactly one function {name}")
    node = matches[0]
    # end_lineno is unavailable on the older Python shipped with the intranet
    # host. The next top-level definition is a stable boundary for these
    # undecorated functions on the approved baseline and reference.
    later = [item.lineno for item in module.body if item.lineno > node.lineno]
    next_line = min(later) if later else len(lines) + 1
    return "".join(lines[node.lineno - 1:next_line - 1])


def replace_once(source, before, after, label):
    if source.count(before) != 1:
        fail(f"Incompatible baseline at {label}: expected one match, got {source.count(before)}")
    return source.replace(before, after, 1)


def between(source, start, end):
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def main():
    if len(sys.argv) != 4:
        fail("usage: patch_services.py BASELINE REFERENCE OUTPUT")
    baseline, reference, output = map(Path, sys.argv[1:])
    original_bytes = baseline.read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()
    if digest != BASE_SHA256:
        fail(f"Unsupported services.py SHA256={digest}; no file changed")
    old = original_bytes.decode("utf-8")
    new = reference.read_text(encoding="utf-8")
    result = old

    # Each replacement is anchored against the approved v3 baseline. Do not
    # transplant the entire module: the target image has older dependencies.
    for name in ("_ocr_diagnostics", "_run_ai_batches", "_prioritize_findings_for_report"):
        result = replace_once(result, function_block(old, name), function_block(new, name), name)

    result = replace_once(
        result,
        between(old, "LOW_VALUE_FILE_PATTERNS = (", "\n\n\ndef _get_code_analysis_llm_config"),
        between(new, "LOW_VALUE_FILE_PATTERNS = (", "\n\n\ndef _get_code_analysis_llm_config"),
        "low value file patterns",
    )
    result = replace_once(
        result,
        between(old, "        for item in diffs:\n", "        # 确定性工具优先："),
        between(new, "        for item in diffs:\n", "        # 确定性工具优先："),
        "diff file classification",
    )
    result = replace_once(
        result,
        between(old, "        verification_candidates, _ = _prioritize_findings_for_report", "        findings = _attach_finding_diff_evidence"),
        between(new, "        verification_candidates, _ = _prioritize_findings_for_report", "        findings = _attach_finding_diff_evidence"),
        "finding preservation",
    )
    result = replace_once(
        result,
        between(old, "        if task.mode != \"deep\":\n            ocr_status =", "        task.change_report = _sanitize_json_value"),
        between(new, "        if task.mode != \"deep\":\n            ocr_status =", "        task.change_report = _sanitize_json_value"),
        "per-file review status",
    )
    old_coverage = next(line for line in old.splitlines(keepends=True) if '"coverage": {"scope_files": len(files)' in line)
    new_coverage = next(line for line in new.splitlines(keepends=True) if '"coverage": {"scope_files": len(files)' in line)
    result = replace_once(result, old_coverage, new_coverage, "coverage report")
    result = NEW_MARKER + "\n" + result
    compile(result, str(output), "exec")
    output.write_text(result, encoding="utf-8")
    print(f"Prepared {output}; SHA256={hashlib.sha256(result.encode()).hexdigest()}")


if __name__ == "__main__":
    main()
