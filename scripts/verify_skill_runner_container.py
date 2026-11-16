#!/usr/bin/env python3
"""显式运行的Docker验收；只创建和清理本脚本命名的临时资源，不使用业务数据库。"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',default='wharttest-skill-runner:2.9.2-alpine');parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    prefix='codex-runner-'+uuid.uuid4().hex[:10];exec_net=prefix+'-exec';data_net=prefix+'-data'
    runner=prefix+'-runner';api=prefix+'-api';data=prefix+'-storage';created=[];networks=[]
    token=secrets.token_urlsafe(32);env={**os.environ,'SKILL_RUNNER_TOKEN':token}
    def docker(*argv,**kw):
        return subprocess.check_output(['docker',*argv],text=True,env=env,**kw).strip()
    def start(name,network,*extra):
        result=docker('run','-d','--name',name,'--network',network,'--label','codex.task=skill-runner-acceptance',*extra)
        created.append(name);return result
    def request(url,body=None,auth=True):
        req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if auth else {})})
        with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
    def job(script,timeout=10):
        files={'SKILL.md':'---\nname: acceptance\n---\n','main.py':script}
        package=[{'name':name,'data_b64':base64.b64encode(content.encode()).decode(),'sha256':hashlib.sha256(content.encode()).hexdigest()} for name,content in files.items()]
        return request(endpoint+'/execute',{'package':package,'command':'python main.py','timeout_seconds':timeout,'user_id':'acceptance-user','project_id':'acceptance-project','skill_version_id':'acceptance-version','source_sha256':'snapshot-metadata','runtime_env':{'WHARTTEST_API_KEY':'acceptance-caller-key'}})
    checks={}
    try:
        for network in [exec_net,data_net]:docker('network','create',network);networks.append(network)
        probe_code = """from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  authorized=not self.path.startswith('/api/') or self.headers.get('X-API-Key')=='acceptance-caller-key'
  self.send_response(200 if authorized else 403);self.send_header('Content-Type','application/json');self.end_headers()
  self.wfile.write(json.dumps({'status':'success','data':[{'id':8,'name':'probe-module'}]}).encode())
 def log_message(self,*args):pass
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
"""
        start(api,exec_net,'--entrypoint','python',args.image,'-c',probe_code)
        start(data,data_net,'--entrypoint','python',args.image,'-m','http.server','5432')
        start(runner,exec_net,'--read-only','--tmpfs','/tmp:size=256m,mode=1777','--cap-drop','ALL','--security-opt','no-new-privileges:true','--pids-limit','256','--memory','1g','--cpus','2','-p','127.0.0.1::8923','-e','SKILL_RUNNER_TOKEN',args.image)
        port=docker('port',runner,'8923/tcp').splitlines()[0].rsplit(':',1)[1];endpoint='http://127.0.0.1:'+port
        for attempt in range(50):
            try:
                if request(endpoint+'/health')['ok']:break
            except (OSError,ValueError):time.sleep(.2)
        else:raise AssertionError('runner health failed')
        try:request(endpoint+'/execute',{},auth=False);raise AssertionError('anonymous execute accepted')
        except urllib.error.HTTPError as error:assert error.code==401
        checks['rpc_authentication']=True
        result=job("import os,json;from pathlib import Path\ntry:\n open('/proc/1/environ','rb').read(); protected=False\nexcept PermissionError:\n protected=True\ntry:\n Path('/opt/runner/escape').write_text('x'); readonly=False\nexcept OSError:\n readonly=True\nprint(json.dumps({'uid':os.getuid(),'parent_protected':protected,'readonly':readonly,'backend_absent':not Path('/app/data').exists(),'control_token_absent':os.getenv('SKILL_RUNNER_TOKEN') is None,'page_size':os.sysconf('SC_PAGE_SIZE')}))")
        assert result['returncode']==0,result['stderr'];sandbox=json.loads(result['stdout'])
        assert sandbox['uid']!=0 and all(sandbox[k] for k in ['parent_protected','readonly','backend_absent','control_token_absent']),sandbox
        checks['sandbox']=sandbox
        docker('exec',data,'python','-c',"import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:5432',timeout=3).status==200")
        ip=json.loads(docker('inspect',data))[0]['NetworkSettings']['Networks'][data_net]['IPAddress']
        network=job("import socket,json,urllib.request\nreachable=urllib.request.urlopen('http://"+api+":8000',timeout=3).status==200\ntry:\n s=socket.create_connection(('"+ip+"',5432),timeout=1); s.close(); blocked=False\nexcept OSError:\n blocked=True\nprint(json.dumps({'backend_probe_reachable':reachable,'storage_network_blocked':blocked}))")
        values=json.loads(network['stdout']);assert all(values.values()),values;checks['network']=values
        artifact=job("from pathlib import Path;import os;Path(os.environ['ARTIFACT_DIR'],'proof.txt').write_text('round-trip')")
        assert artifact['returncode']==0 and base64.b64decode(artifact['artifacts'][0]['data_b64'])==b'round-trip';checks['artifact_round_trip']=True
        client_path=Path(__file__).resolve().parents[1]/'WHartTest_Django/orchestrator_integration/builtin_tools/skill_runner_client.py'
        spec=importlib.util.spec_from_file_location('acceptance_client',client_path);client=importlib.util.module_from_spec(spec);sys.modules[spec.name]=client;spec.loader.exec_module(client)
        source=Path(__file__).resolve().parents[1]/'WHartTest_Skills/ui-automation-skill'
        ui_result=request(endpoint+'/execute',{'package':client.build_package(str(source)),'command':'python ui_automation_tools.py --action get_ui_modules --project_id 8','timeout_seconds':10,'runtime_env':{'WHARTTEST_API_KEY':'acceptance-caller-key','WHARTTEST_BACKEND_URL':'http://'+api+':8000'}})
        assert ui_result['returncode']==0 and json.loads(ui_result['stdout'])==[{'id':8,'name':'probe-module'}],ui_result['stderr']
        checks['repository_ui_skill_cli_against_mock_api']=True
        imports=job("import requests,openpyxl,httpx,websockets,dotenv,pymysql;print('dependencies-ok')")
        assert imports['returncode']==0 and 'dependencies-ok' in imports['stdout'];checks['offline_runtime_imports']=True
        child=job("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid)")
        pid=int(child['stdout'].strip())
        docker('exec',runner,'python','-c',f"from pathlib import Path;p=Path('/proc/{pid}/stat');assert not p.exists() or p.read_text().split()[2]=='Z'")
        checks['detached_child_reaped']=True
        try:job('import time;time.sleep(40)',timeout=1);raise AssertionError('timeout accepted')
        except urllib.error.HTTPError as error:assert error.code==400
        checks['timeout_reaped']=True
        inspection=json.loads(docker('inspect',runner))[0];host=inspection['HostConfig']
        checks['container_configuration']={'read_only':host['ReadonlyRootfs'],'cap_drop':host['CapDrop'],'security_opt':host['SecurityOpt'],'pids_limit':host['PidsLimit'],'memory':host['Memory'],'mounts':inspection['Mounts'],'architecture':docker('image','inspect',args.image,'--format','{{.Architecture}}')}
        assert host['ReadonlyRootfs'] and 'ALL' in host['CapDrop'] and not host.get('Binds')
        assert all(m['Type']!='bind' for m in inspection['Mounts'])
        (output/'acceptance.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
        print('PASS: RPC auth; rootless/read-only; parent secret protection; separate data network; artifacts; detached children; timeout; container limits')
    finally:
        for name in reversed(created):subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for name in reversed(networks):subprocess.run(['docker','network','rm',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (output/'cleanup.json').write_text(json.dumps({'containers':created,'networks':networks,'cleanup_attempted':True},indent=2))

if __name__=='__main__':main()
