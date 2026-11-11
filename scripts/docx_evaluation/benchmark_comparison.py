"""Benchmark the shipped comparison engine using deterministic synthetic documents."""
import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'WHartTest_Django'))
from docx import Document
from evaluate import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--out', type=Path, default=Path('/tmp/whart-shipped-comparison-benchmark.json'))
    args = parser.parse_args()
    if args.worker:
        from requirements.document_comparison import compare_documents, export_selection
        folder = args.worker
        old, new = (folder / 'old.docx').read_bytes(), (folder / 'new.docx').read_bytes()
        start = time.monotonic()
        result = compare_documents(old, new)
        compared = time.monotonic()
        assert result['added'] == 2 and result['deleted'] == 2
        bundle = export_selection(new, result, [h['id'] for h in result['hunks']], '性能验证')
        print(json.dumps({'file_mb': round(len(new)/1024**2, 2), 'new_blocks': result['new_blocks'],
                          'compare_seconds': round(compared-start, 3),
                          'export_seconds': round(time.monotonic()-compared, 3),
                          'bundle_bytes': len(bundle)}))
        return
    results = []
    with tempfile.TemporaryDirectory(prefix='whart-comparison-benchmark-') as directory:
        folder = Path(directory)
        for count in (5000, 20000):
            rng = random.Random(20261111)
            doc = Document()
            for i in range(count):
                if i % 100 == 0:
                    doc.add_heading(f'第{i//100+1}节 业务规则', 1)
                doc.add_paragraph(f'规则{i} 超时30秒 ' + rng.randbytes(320).hex())
            table = doc.add_table(rows=2, cols=2)
            for row in table.rows:
                for cell in row.cells:
                    cell.text = '字段规则'
            doc.save(folder/'old.docx')
            doc.paragraphs[len(doc.paragraphs)//2].text += ' 本次修改超时为60秒'
            table.cell(1, 1).text = '金额上限改为200元'
            doc.save(folder/'new.docx')
            result = run([sys.executable, __file__, '--worker', str(folder)], 120)
            result['paragraphs'] = count
            results.append(result)
            args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
