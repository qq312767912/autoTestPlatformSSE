"""从运行容器生成独立的部署/回滚配置，保留实际端口及持久化路径。"""
import json,pathlib,subprocess,sys
out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True)
revision='20261119-89ffd9ef-r2'
names={'backend':'wharttest-backend','frontend':'wharttest-frontend','semgrep-scanner':'wharttest-semgrep','crg-service':'wharttest-crg','actuator-01':'wharttest-actuator-01','actuator-02':'wharttest-actuator-02','actuator-03':'wharttest-actuator-03'}
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
objects={service:inspect(name) for service,name in names.items()}
project=objects['backend']['Config']['Labels']['com.docker.compose.project']
def compose_safe(value):
 if isinstance(value,str):return value.replace('$','$$')
 if isinstance(value,list):return [compose_safe(x) for x in value]
 if isinstance(value,dict):return {k:compose_safe(v) for k,v in value.items()}
 return value
configs={'name':project,'services':{},'networks':{},'volumes':{}}
for service,obj in objects.items():
 c=obj['Config'];h=obj['HostConfig'];item={'image':obj['Image'],'container_name':obj['Name'].lstrip('/'),'pull_policy':'never','restart':h['RestartPolicy']['Name'] or 'no','stop_grace_period':'240s','environment':{},'networks':{},'volumes':[]}
 for e in c.get('Env',[]):
  key,_,value=e.partition('=');item['environment'][key]=value
 for key,field in [('entrypoint','Entrypoint'),('command','Cmd'),('user','User'),('working_dir','WorkingDir'),('hostname','Hostname')]:
  if c.get(field):item[key]=c[field]
 if c.get('Healthcheck'):
  hc=c['Healthcheck'];item['healthcheck']={'test':hc['Test']}
  for f,k in [('Interval','interval'),('Timeout','timeout'),('StartPeriod','start_period')]:
   if hc.get(f):item['healthcheck'][k]=str(hc[f])+'ns'
  if hc.get('Retries'):item['healthcheck']['retries']=hc['Retries']
 for n,net in obj['NetworkSettings']['Networks'].items():
  configs['networks'][n]={'external':True,'name':n};item['networks'][n]={'aliases':net.get('Aliases') or [service]}
 for mount in obj['Mounts']:
  if mount['Type']=='volume':
   name=mount['Name'];configs['volumes'][name]={'external':True,'name':name};source=name
  elif mount['Type']=='bind':source=mount['Source']
  else:raise RuntimeError('不支持的挂载类型，停止：'+str(mount))
  if mount['Type']=='bind' and (mount['Destination'].startswith('/run/secrets/') or mount['Destination']=='/app/supervisord.conf'):
   if not pathlib.Path(source).is_file():
    raise SystemExit('关键文件挂载的宿主机源已缺失或变成目录，停止且不停止服务：'+source)
  item['volumes'].append({'type':mount['Type'],'source':source,'target':mount['Destination'],'read_only':not mount['RW']})
 item['ports']=[]
 for port,bindings in (h.get('PortBindings') or {}).items():
  target,protocol=port.split('/')
  for binding in bindings or []:
   item['ports'].append({'target':int(target),'published':str(binding['HostPort']),'host_ip':binding.get('HostIp') or '0.0.0.0','protocol':protocol})
 if not item['ports']:item.pop('ports')
 for key,field in [('extra_hosts','ExtraHosts'),('cap_drop','CapDrop'),('cap_add','CapAdd'),('security_opt','SecurityOpt'),('sysctls','Sysctls'),('tmpfs','Tmpfs')]:
  if h.get(field):item[key]=h[field]
 for key,field in [('read_only','ReadonlyRootfs'),('init','Init'),('privileged','Privileged')]:
  if h.get(field):item[key]=h[field]
 for key,field in [('mem_limit','Memory'),('memswap_limit','MemorySwap'),('pids_limit','PidsLimit'),('shm_size','ShmSize')]:
  if h.get(field):item[key]=h[field]
 if h.get('NanoCpus'):item['cpus']=h['NanoCpus']/1e9
 if h.get('Ulimits'):item['ulimits']={x['Name']:{'soft':x['Soft'],'hard':x['Hard']} for x in h['Ulimits']}
 configs['services'][service]=item
(out/'inspect.json').write_text(json.dumps(objects,indent=2))
(out/'project').write_text(project+'\n')
(out/'compose.rollback.json').write_text(json.dumps(compose_safe(configs),indent=2))
for service,item in configs['services'].items():
 component='actuator' if service.startswith('actuator-') else {'semgrep-scanner':'semgrep','crg-service':'crg'}.get(service,service)
 item['image']=f'wharttest-250-{component}:update-{revision}-arm64'
 if service=='backend':
  # 新镜像携带完整代码；移除会遮蔽源代码/启动配置的旧热修复挂载。
  allowed={'/app/data','/app/static','/app/media'}
  item['volumes']=[m for m in item['volumes'] if not (m['target'].startswith('/app/') and m['target'] not in allowed)]
 if service=='frontend':
  item['volumes']=[m for m in item['volumes'] if m['target'] not in {'/etc/nginx/conf.d/default.conf','/etc/nginx/nginx.conf','/usr/share/nginx/html'}]
(out/'compose.runtime.json').write_text(json.dumps(compose_safe(configs),indent=2))
for p in out.iterdir():
 if p.is_file():p.chmod(0o600)
print('已保存独立配置；project='+project+'，前端端口完全沿用现有映射')
