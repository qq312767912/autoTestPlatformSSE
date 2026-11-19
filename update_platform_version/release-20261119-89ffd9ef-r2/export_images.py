import gzip,hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent
REV='20261119-89ffd9ef-r2'
class Parts:
 def __init__(self,prefix):self.prefix=prefix;self.index=0;self.size=0;self.file=None;self.paths=[]
 def write(self,data):
  total=len(data)
  while data:
   if self.file is None:
    path=pathlib.Path(str(self.prefix)+f'.part{self.index:03d}')
    self.file=path.open('wb');self.paths.append(path);self.index+=1;self.size=0
   n=min(len(data),280*1024*1024-self.size);self.file.write(data[:n]);data=data[n:];self.size+=n
   if self.size==280*1024*1024:self.file.close();self.file=None
  return total
 def flush(self):
  if self.file:self.file.flush()
 def close(self):
  if self.file:self.file.close();self.file=None
for component in sys.argv[1:] or ['backend','frontend','semgrep','crg','actuator']:
 image=f'wharttest-250-{component}:update-{REV}-arm64'
 prefix=ROOT/'images'/f'{component}-{REV}-arm64.tar.gz'
 marker=pathlib.Path(str(prefix)+'.complete.json')
 image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
 if marker.exists() and json.loads(marker.read_text()).get('image_id')==image_id:
  print('已有完整导出：'+component,flush=True);continue
 for p in prefix.parent.glob(prefix.name+'.part*'):p.unlink()
 parts=Parts(prefix)
 process=subprocess.Popen(['docker','save','--platform','linux/arm64',image],stdout=subprocess.PIPE)
 try:
  with gzip.GzipFile(fileobj=parts,mode='wb',compresslevel=1,mtime=0) as compressed:
   while data:=process.stdout.read(1024*1024):compressed.write(data)
  if process.wait():raise RuntimeError('docker save failed')
 finally:parts.close()
 files={}
 for p in parts.paths:
  digest=hashlib.sha256()
  with p.open('rb') as f:
   while data:=f.read(1024*1024):digest.update(data)
  files[p.name]={'bytes':p.stat().st_size,'sha256':digest.hexdigest()}
 marker.write_text(json.dumps({'image':image,'image_id':image_id,'files':files},indent=2))
 print(component+' 导出完成，分卷数='+str(len(files)),flush=True)
