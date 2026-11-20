"""恢复丢失的备份指针，仅接受本版本已经完成的独立备份。"""
import json,pathlib,sys,os,tempfile

def resolve(here):
 here=pathlib.Path(here).resolve();revision=(here/'VERSION').read_text().strip()
 def valid(p):
  try:
   return all((p/name).is_file() and (p/name).stat().st_size>0 for name in ['postgres.sql.gz','app-data.tar.gz','stopped-containers']) and (p/'BACKUP_COMPLETE').is_file() and bool((p/'project').read_text().strip()) and isinstance(json.loads((p/'compose.rollback.json').read_text())['services']['backend'],dict) and json.loads((p/'compose.runtime.json').read_text())['services']['backend']['image']=='wharttest-250-backend:update-'+revision+'-arm64'
  except (OSError,ValueError,KeyError,TypeError):return False
 marker=here/'.latest-backup'
 if marker.is_file():
  candidate=pathlib.Path(marker.read_text().strip())
  if valid(candidate):return candidate.resolve()
 candidates=[]
 root=here.parent/'backups'
 if root.is_dir():candidates=[p for p in root.iterdir() if p.is_dir() and valid(p)]
 if not candidates:raise RuntimeError('没有找到本版本完整备份；请提供原02-backup成功时的备份目录，不要在失败现场重新备份')
 # 02生成的目录名含时间戳；只从同版本且标记完成的备份中选择最新一次。
 selected=sorted(candidates,key=lambda p:p.name)[-1].resolve()
 fd,tmp=tempfile.mkstemp(prefix='.backup-pointer-',dir=here)
 with os.fdopen(fd,'w') as f:f.write(str(selected)+'\n')
 os.replace(tmp,marker)
 print('已找回本版本完整备份：'+str(selected),file=sys.stderr)
 return selected
if __name__=='__main__':
 try:print(resolve(sys.argv[1]))
 except RuntimeError as e:raise SystemExit(str(e))
