"""Local, deterministic DOCX component evaluation; never modifies source files.

Run in an isolated venv with docx-trackdiff, docx-revisions, python-docx, psutil.
Results contain synthetic text only. Real documents are not uploaded anywhere.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx_revisions import RevisionDocument


def fixture(path, count, updated=False):
    doc = Document()
    for i in range(count):
        if i % 40 == 0:
            doc.add_heading(f'第{i // 40 + 1}节 业务规则', 1)
        value = 60 if updated and i == count // 2 else 30
        doc.add_paragraph(f'规则{i:06d}：登录失败5次后锁定{value}分钟，金额上限100.50元。')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '字段'
    table.cell(0, 1).text = '规则'
    table.cell(1, 0).text = '超时'
    table.cell(1, 1).text = '60秒' if updated else '30秒'
    doc.save(path)


def run(command, timeout):
    start = time.monotonic()
    peak = 0
    import tempfile
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        process = psutil.Process(proc.pid)
        timed_out = False
        while proc.poll() is None:
            try:
                peak = max(peak, sum(p.memory_info().rss for p in [process] + process.children(recursive=True)))
            except psutil.Error:
                pass
            if time.monotonic() - start > timeout:
                timed_out = True
                for child in process.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                proc.kill()
                break
            time.sleep(0.05)
        proc.wait()
        log.seek(0)
        tail = log.read().decode('utf-8', errors='replace')[-2500:]
    return dict(exit_code=proc.returncode, timeout=timed_out,
                elapsed_seconds=round(time.monotonic()-start, 3),
                peak_rss_mb=round(peak/1024/1024, 1), log=tail)


def revision_probe(folder):
    doc = Document()
    p = doc.add_paragraph('当前：')
    r = p.add_run('手工删除内容')
    r.font.strike = True
    p.add_run('双删除内容').font.double_strike = True
    for tag, text in [('del', '修订旧值'), ('ins', '修订新值')]:
        change = OxmlElement('w:' + tag)
        change.set(qn('w:id'), '1' if tag == 'del' else '2')
        change.set(qn('w:author'), 'fixture')
        change.set(qn('w:date'), '2026-11-11T00:00:00Z')
        run_el = OxmlElement('w:r')
        text_el = OxmlElement('w:delText' if tag == 'del' else 'w:t')
        text_el.text = text
        run_el.append(text_el)
        change.append(run_el)
        p._p.append(change)
    path = folder / 'revision-probe.docx'
    doc.save(path)
    revised = RevisionDocument(str(path))
    para = revised.paragraphs[0]
    accepted = para.accepted_text
    return dict(accepted_text=accepted, original_text=para.original_text,
                tracked_delete_excluded='修订旧值' not in accepted,
                tracked_insert_included='修订新值' in accepted,
                manual_strike_excluded='手工删除内容' not in accepted,
                double_strike_excluded='双删除内容' not in accepted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--paragraphs', type=int, nargs='+', default=[100, 10000])
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = {'revision_probe': revision_probe(args.out), 'comparisons': []}
    cli = Path(sys.executable).parent / 'docx-trackdiff'
    verifier = Path(sys.executable).parent / 'docx-trackdiff-verify'
    for count in args.paragraphs:
        old, new, output = [args.out / f'{count}-{name}.docx' for name in ['old', 'new', 'diff']]
        fixture(old, count)
        fixture(new, count, updated=True)
        comparison = run([str(cli), str(old), str(new), str(output)], args.timeout)
        entry = {'paragraphs': count, 'compare': comparison}
        if comparison['exit_code'] == 0:
            entry['verify'] = run([str(verifier), str(output), str(old), str(new)], args.timeout)
        results['comparisons'].append(entry)
        (args.out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
