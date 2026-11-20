"""使用隔离容器只读验证执行器平台密码，不依赖当前后端已启动。"""
import getpass,json,os,pathlib,subprocess,tempfile

def select_password(config,candidates):
    actor=config['services']['actuator-01']['environment']
    username=actor.get('WHARTTEST_ACTUATOR_API_USERNAME','admin').replace('$$','$')
    backend=config['services']['backend']
    pg=json.loads(subprocess.check_output(['docker','inspect','wharttest-postgres']))[0]
    network=next((n for n in backend['networks'] if n in pg['NetworkSettings']['Networks']),None)
    if not network:raise RuntimeError('找不到后端与数据库的共同网络，停止凭据验证')
    env={k:str(v).replace('$$','$') for k,v in backend['environment'].items() if k.startswith('POSTGRES_') or k in {'DATABASE_TYPE','DJANGO_SECRET_KEY'}}
    code='''import os,json,sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE","wharttest_django.settings")
import django
django.setup()
from django.contrib.auth import get_user_model
p=json.load(sys.stdin)
u=get_user_model().objects.filter(username=p["username"],is_active=True).first()
raise SystemExit(0 if u and u.check_password(p["password"]) else 10)
'''
    with tempfile.TemporaryDirectory() as d:
        path=pathlib.Path(d)/'env';path.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n');path.chmod(0o600)
        def valid(password):
            r=subprocess.run(['docker','run','--rm','-i','--network',network,'--env-file',str(path),'--entrypoint','python',backend['image'],'-c',code],input=json.dumps({'username':username,'password':password}),text=True,capture_output=True,timeout=90)
            if r.returncode not in (0,10):raise RuntimeError('数据库凭据校验容器执行失败，停止；未重置密码')
            return r.returncode==0
        for password in {v.decode('utf-8').strip() for v in candidates}:
            if valid(password):return password.encode()
        for attempt in range(3):
            password=getpass.getpass('请输入平台账号 '+username+' 的当前登录密码（隐藏输入，不会重置）：')
            if password and valid(password):return password.encode()
            print('密码未通过验证，请确认当前平台账号密码；未进行重置。')
        raise RuntimeError('凭据验证未通过，停止发布')
