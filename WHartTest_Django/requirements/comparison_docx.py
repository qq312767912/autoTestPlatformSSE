"""Build stable DOCX bytes for text comparison adapters."""
from io import BytesIO
from zipfile import ZipFile


def comparison_docx_bytes(document):
    raw, output = BytesIO(), BytesIO()
    document.save(raw)
    # Fixed ZIP timestamps keep the source hash stable across comparison and export.
    with ZipFile(raw) as source, ZipFile(output, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            entry.date_time = (1980, 1, 1, 0, 0, 0)
            target.writestr(entry, content)
    return output.getvalue()
