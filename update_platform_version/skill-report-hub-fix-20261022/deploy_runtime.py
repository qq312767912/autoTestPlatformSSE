"""Local exact-runtime patch installer. Refuses source drift; keeps rollback metadata."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime

root=Path(__file__).resolve().parent
files=json.loads((root/'manifest.json').read_text())
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

action=sys.argv[1]
if action in ('check','install'):
    for item in files:
        target=Path('/app')/item['relative']
        payload=root/'files'/item['relative']
        assert digest(target)==item['original_sha256'], '运行源码变化，停止：'+item['relative']
        assert digest(payload)==item['candidate_sha256'], '补丁内容校验失败：'+item['relative']
        compile(payload.read_text(),str(target),'exec')
    if action=='check':
        print('PASS: all runtime baselines and candidate syntax match')
        sys.exit(0)
    backup=Path('/app/data/hotfix-backups')/('skill-report-hub-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    for item in files:
        target=Path('/app')/item['relative']
        old=backup/item['relative']; old.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): shutil.copy2(target,old)
    (backup/'rollback.json').write_text(json.dumps(files,indent=2))
    (root/'last-backup.path').write_text(str(backup))
    try:
        for item in files:
            shutil.copy2(root/'files'/item['relative'],Path('/app')/item['relative'])
    except Exception:
        for item in files:
            target=Path('/app')/item['relative']
            if item['original_sha256'] is None: target.unlink(missing_ok=True)
            else: shutil.copy2(backup/item['relative'],target)
        raise
    print('INSTALLED; BACKUP='+str(backup))
elif action=='restore':
    backup=Path(sys.argv[2]) if len(sys.argv)>2 else Path((root/'last-backup.path').read_text().strip())
    records=json.loads((backup/'rollback.json').read_text())
    for item in records:
        target=Path('/app')/item['relative']
        assert digest(target) in (item['original_sha256'],item['candidate_sha256']), '源码已有后续修改，停止回滚：'+item['relative']
        if item['original_sha256'] is not None: assert digest(backup/item['relative'])==item['original_sha256']
    for item in records:
        target=Path('/app')/item['relative']
        if item['original_sha256'] is None: target.unlink(missing_ok=True)
        else: shutil.copy2(backup/item['relative'],target)
    print('RESTORED')
elif action=='verify':
    for item in files:
        assert digest(Path('/app')/item['relative'])==item['candidate_sha256'], item['relative']
    print('PASS: installed runtime content')
else: raise SystemExit('check | install | verify | restore [backup]')
