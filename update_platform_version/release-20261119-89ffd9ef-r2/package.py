import hashlib,json,pathlib,subprocess,zipfile
from verify_release import verify
ROOT=pathlib.Path(__file__).resolve().parent
ENV=ROOT/'deploy_env'
REV=(ENV/'VERSION').read_text().strip()
verify()  # Fail before generating manifests or replacing the deliverable ZIP.
components={'backend':'WHartTest_Django/Dockerfile.alpine','frontend':'WHartTest_Vue/Dockerfile','semgrep':'WHartTest_Semgrep/Dockerfile','crg':'WHartTest_CRG/Dockerfile','actuator':'WHartTest_Actuator/Dockerfile.alpine-arm64'}
images=[]; checks=[]
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while chunk:=f.read(1024*1024):h.update(chunk)
 return h.hexdigest()
for component,dockerfile in components.items():
 tag=f'wharttest-250-{component}:update-{REV}-arm64'
 image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
 assert image['Architecture']=='arm64' and image['Os']=='linux',tag
 assert image['Config']['Labels']['org.opencontainers.image.revision']==REV,tag
 marker=ROOT/'images'/f'{component}-{REV}-arm64.tar.gz.complete.json'
 record=json.loads(marker.read_text());assert record['image_id']==image['Id']
 for name,meta in record['files'].items():
  path=ROOT/'images'/name
  assert path.stat().st_size==meta['bytes'] and digest(path)==meta['sha256'],name
  checks.append(meta['sha256']+'  ../images/'+name)
 images.append({'component':component,'tag':tag,'image_id':image['Id'],'platform':'linux/arm64','revision':REV,'dockerfile':dockerfile,'exported_parts':record['files']})
for component in ['backend','actuator']:
 validation=json.loads((ROOT/'build-logs'/f'{component}-validation.complete').read_text())
 actual=next(x['image_id'] for x in images if x['component']==component)
 assert validation.get('passed') and validation['image_id']==actual,component+' validation mismatch'
(ENV/'images.json').write_text(json.dumps(images,ensure_ascii=False,indent=2)+'\n')
lines=['源码：dev / 89ffd9ef','镜像版本：'+REV,'目标：麒麟 ARM64 64KB，目标验收由03-import-images.sh执行。','', '本次完整源码构建：']
for image in images:lines+=['- '+image['tag']+' ['+image['platform']+']','  Dockerfile: '+image['dockerfile']]
lines+=['','复用：内网现有 PostgreSQL、Redis、麒麟64KB专用Qdrant、MCP、Playwright MCP、Vision MCP、Host Sync。','端口与挂载：从实际运行容器快照生成，保持现有80端口入口；不修改宿主机hosts。']
(ENV/'IMAGE_MANIFEST.txt').write_text('\n'.join(lines)+'\n')
files=[p for p in sorted(ENV.rglob('*')) if p.is_file() and not p.name.startswith('.') and p.name!='SHA256SUMS' and '__pycache__' not in p.parts]
checks += [digest(p)+'  '+p.relative_to(ENV).as_posix() for p in files]
(ENV/'SHA256SUMS').write_text('\n'.join(checks)+'\n')
archive=ROOT/'deploy_env.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for p in files+[ENV/'SHA256SUMS']:z.write(p,'deploy_env/'+p.relative_to(ENV).as_posix())
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
(ROOT/'deploy_env.zip.sha256').write_text(digest(archive)+'  deploy_env.zip\n')
print('部署包：'+str(archive))
print('镜像分卷：'+str(ROOT/'images'))
print('校验文件：'+str(ROOT/'deploy_env.zip.sha256'))
