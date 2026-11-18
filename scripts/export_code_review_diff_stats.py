#!/usr/bin/env python3
"""只读导出代码审查任务的逐文件 Diff 统计；不输出源码或凭据。"""

import argparse
import json
import re
import subprocess
from pathlib import Path

BASE = "435e4336f086baf75c0beb5dda7136e2ec5b326f"
HEAD = "a7972b8007b27192488247f496932e83ee84533a"

INNER = r'''
import json, sys
from code_analysis.models import AnalysisTask
base, head = __BASE__, __HEAD__
tasks = AnalysisTask.objects.filter(base_sha__startswith=base[:12], head_sha__startswith=head[:12]).order_by('-created_at')
out = []
for task in tasks[:10]:
    report = task.change_report or {}
    files = report.get('files') or []
    out.append({
        'task_id': str(task.pk), 'project_id': task.project_id,
        'repository_id': task.repository_id, 'source_type': task.source_type,
        'status': task.status, 'base_sha': task.base_sha, 'head_sha': task.head_sha,
        'created_at': task.created_at.isoformat(),
        'summary': {k: (report.get('summary') or {}).get(k) for k in
                    ('changed_files','analyzed_files','excluded_files','additions','deletions','changed_lines')},
        'raw_diff_bytes': len((task.raw_diff or '').encode('utf-8')),
        'files': [{k: f.get(k) for k in ('path','old_path','additions','deletions','changed_lines','excluded','exclusion_reason')}
                  for f in files if isinstance(f, dict)],
    })
print('DIFF_STATS_JSON=' + json.dumps(out, ensure_ascii=False, default=str))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='wharttest-new-backend', help='后端容器名')
    parser.add_argument('--base', default=BASE, help='报告中的基准 SHA')
    parser.add_argument('--head', default=HEAD, help='报告中的目标 SHA')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[0-9a-fA-F]{12,40}', value) for value in (args.base, args.head)):
        parser.error('SHA 必须为 12 至 40 位十六进制字符')
    output = Path(__file__).resolve().parent / 'code-review-platform-diff.log'
    try:
        code = INNER.replace('__BASE__', repr(args.base)).replace('__HEAD__', repr(args.head))
        cmd = ['docker', 'exec', args.container, 'python', 'manage.py', 'shell', '-c', code]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[-1200:])
        marker = 'DIFF_STATS_JSON='
        line = next((line for line in result.stdout.splitlines() if line.startswith(marker)), None)
        if line is None:
            raise RuntimeError('容器未返回逐文件统计；请确认容器名和 Django 运行环境')
        tasks = json.loads(line[len(marker):])
        lines = ['代码审查平台 Diff 统计（只读，不含代码正文或 Token）',
                 f'查询范围: {args.base} -> {args.head}', f'匹配任务数: {len(tasks)}', '']
        if not tasks:
            lines.append('没有匹配任务。请检查报告 SHA，或用 --container 指定正确的后端容器。')
        for task in tasks:
            files = task.pop('files')
            lines.append('任务信息: ' + json.dumps(task, ensure_ascii=False, sort_keys=True))
            lines.append('逐文件: 新增\t删除\t排除\t原因\t路径')
            for file in files:
                lines.append(f"{file.get('additions') or 0}\t{file.get('deletions') or 0}\t"
                             f"{bool(file.get('excluded'))}\t{file.get('exclusion_reason') or '-'}\t{file.get('path') or ''}")
            lines.append(f'逐文件合计: {len(files)} 个文件, +{sum(f.get("additions") or 0 for f in files)} '
                         f'-{sum(f.get("deletions") or 0 for f in files)}')
            lines.append('')
        output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'日志路径: {output}')
        print(f'匹配任务: {len(tasks)}')
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        output.write_text(f'导出失败: {exc}\n', encoding='utf-8')
        print(f'日志路径: {output}')
        print(f'导出失败: {exc}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
