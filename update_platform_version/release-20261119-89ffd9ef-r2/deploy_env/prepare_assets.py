"""在停止/重建容器前固化挂载文件；兼容继续已有备份的发布。"""
import hashlib,json,pathlib,subprocess,sys,tempfile,datetime,os

def prepare(backup,here):
    backup=pathlib.Path(backup);here=pathlib.Path(here);assets=backup/'deployment-assets';assets.mkdir(mode=0o700,exist_ok=True)
    configs={name:json.loads((backup/name).read_text()) for name in ['compose.runtime.json','compose.rollback.json']}
    replacements={}
    for config in configs.values():
        for service,item in config['services'].items():
            for mount in item.get('volumes',[]):
                target=mount['target']
                if mount['type']!='bind' or not(target.startswith('/run/secrets/') or target=='/app/supervisord.conf'):continue
                source=pathlib.Path(mount['source']);key=str(source)
                if key in replacements:continue
                if source.parent==assets and source.is_file() and source.stat().st_size:
                    replacements[key]=key;continue
                output=assets/(hashlib.sha256(key.encode()).hexdigest()[:12]+'-'+source.name)
                if output.is_file() and output.stat().st_size:replacements[key]=str(output);continue
                candidates=[]
                if source.is_file() and source.stat().st_size:candidates=[source.read_bytes()]
                else:
                    for base in [here.parent/'secrets',here.parent/'deploy_env.bak',here.parent/'backups',backup]:
                        if base.is_dir():
                            candidates += [p.read_bytes() for p in base.rglob(source.name) if p.is_file() and not p.is_symlink() and p.stat().st_size]
                    # 原容器仍持有已删除文件时，优先在重建前救出。
                    for container in ['wharttest-actuator-01','wharttest-actuator-02','wharttest-actuator-03','wharttest-backend']:
                        r=subprocess.run(['docker','exec',container,'sh','-c','test -f "$1" && cat "$1"','sh',target],capture_output=True)
                        if r.returncode==0 and r.stdout:candidates.append(r.stdout)
                    if not candidates and target=='/app/supervisord.conf':
                        image=configs['compose.rollback.json']['services']['backend']['image']
                        container=subprocess.check_output(['docker','create','--entrypoint','sh',image],text=True).strip()
                        try:
                            with tempfile.TemporaryDirectory() as d:
                                p=pathlib.Path(d)/'conf';subprocess.run(['docker','cp',container+':/app/supervisord.conf',str(p)],check=True,stdout=subprocess.DEVNULL);candidates=[p.read_bytes()]
                        finally:subprocess.run(['docker','rm',container],check=True,stdout=subprocess.DEVNULL)
                unique={hashlib.sha256(v.strip()).hexdigest():v for v in candidates if v.strip()}
                if len(unique)!=1:raise RuntimeError('无法唯一确认原挂载文件，未生成或猜测密码：'+str(source))
                fd,tmp=tempfile.mkstemp(dir=assets)
                with os.fdopen(fd,'wb') as f:f.write(next(iter(unique.values())))
                os.replace(tmp,output);os.chmod(output,0o600);replacements[key]=str(output)
    for name,config in configs.items():
        for item in config['services'].values():
            for m in item.get('volumes',[]):
                if m['source'] in replacements:m['source']=replacements[m['source']]
        original=backup/(name+'.before-assets')
        if not original.exists():original.write_bytes((backup/name).read_bytes());original.chmod(0o600)
        fd,tmp=tempfile.mkstemp(dir=backup)
        with os.fdopen(fd,'w') as f:json.dump(config,f,indent=2)
        os.replace(tmp,backup/name)
    print('秘密文件和回滚配置已固化到独立备份目录，内容不输出')

if __name__=='__main__':prepare(sys.argv[1],sys.argv[2])
