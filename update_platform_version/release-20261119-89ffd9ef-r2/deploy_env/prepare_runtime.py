"""同步WebSocket鉴权，保护复用Playwright MCP入口文件。"""
import json,os,pathlib,secrets,subprocess,sys,tempfile,datetime

def atomic(path,body,mode=0o600):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(dir=path.parent)
 with os.fdopen(fd,'wb') as f:f.write(body)
 os.chmod(tmp,mode);os.replace(tmp,path)

def sync_tokens(backup):
 backup=pathlib.Path(backup);p=backup/'compose.runtime.json';c=json.loads(p.read_text())
 env=c['services']['backend'].setdefault('environment',{})
 token=env.get('UI_WS_ACTUATOR_TOKEN','')
 if not token.strip():
  file=backup/'deployment-assets/ws-token'
  token=file.read_text().strip() if file.is_file() else secrets.token_urlsafe(48)
  atomic(file,token.encode())
 env['UI_WS_ACTUATOR_TOKEN']=token
 for name in ['actuator-01','actuator-02','actuator-03']:
  c['services'][name].setdefault('environment',{})['WHARTTEST_ACTUATOR_WS_TOKEN']=token
 atomic(p,(json.dumps(c,indent=2)+'\n').encode())
 print('后端与三个执行器WebSocket鉴权配置已同步（不输出token）')

def protect_mcp(here):
 here=pathlib.Path(here)
 objects=json.loads(subprocess.check_output(['docker','inspect','wharttest-playwright-mcp']))
 expected={'/opt/wharttest/playwright-mcp-entrypoint.sh':'playwright-mcp-entrypoint.sh','/opt/wharttest/playwright-mcp-config.template.json':'playwright-mcp-config.template.json'}
 for m in objects[0]['Mounts']:
  if m['Type']!='bind' or m['Destination'] not in expected:continue
  source=pathlib.Path(m['Source'])
  if source.is_file() and source.stat().st_size:continue
  replacement=here/expected[m['Destination']]
  if not replacement.is_file():raise RuntimeError('部署包缺少复用服务入口：'+str(replacement))
  if source.is_symlink():raise RuntimeError('挂载源是符号链接，停止：'+str(source))
  if source.exists():
   saved=source.with_name(source.name+'.invalid-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
   assert not saved.exists();source.rename(saved)
  atomic(source,replacement.read_bytes(),0o755 if source.suffix=='.sh' else 0o644)
  print('已补齐复用Playwright MCP挂载文件：',source)

if __name__=='__main__':
 protect_mcp(sys.argv[2]);sync_tokens(sys.argv[1])
