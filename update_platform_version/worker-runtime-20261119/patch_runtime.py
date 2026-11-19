"""在目标容器原文件上进行最小增量修改。"""
import configparser
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '/app')
conf = root / 'supervisord.conf'
settings = root / 'wharttest_django/settings.py'
original = conf.read_text()
expected = (pathlib.Path(__file__).parent / 'supervisor.sha256').read_text().strip()
if hashlib.sha256(conf.read_bytes()).hexdigest() != expected:
    raise SystemExit('Supervisor 配置与采集版本不同，停止；请重新采集配置。')
p = configparser.RawConfigParser()
p.read_string(original)
old = p['program:celery_worker']['command']
if '-Q celery,task_center' not in old or '--pool=solo' not in old:
    raise SystemExit('消费者命令与预期不符')
original = original.replace(old, old.replace('-Q celery,task_center', '-n default@%%h -Q celery --prefetch-multiplier=1').replace('--pool=solo --concurrency=1', '--pool=threads --concurrency=4'), 1)
for queue in ['code_analysis', 'testcase_review', 'knowledge', 'ui_execution', 'task_center']:
    pool, concurrency = ('solo', 1) if queue in {'code_analysis', 'testcase_review'} else ('threads', 2 if queue == 'knowledge' else 4)
    original += f'''
[program:celery_{queue}]
command=celery -A wharttest_django -b redis://redis:6379/0 worker -l info --pool={pool} --concurrency={concurrency} --prefetch-multiplier=1 -n {queue}@%%h -Q {queue}
directory=/app
autostart=true
autorestart=true
stopwaitsecs=180
stopasgroup=true
killasgroup=true
stderr_logfile=/app/data/logs/{queue}_worker_err.log
stdout_logfile=/app/data/logs/{queue}_worker_out.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=3
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
'''
p.read_string(original)
source = settings.read_text()
marker = '# WHARTTEST_WORKER_ISOLATION_20261119'
if marker in source:
    raise SystemExit('已安装该补丁')
source += '''\n# WHARTTEST_WORKER_ISOLATION_20261119
# review_tasks.py 不属于 Celery 默认自动发现的 tasks.py，显式加载。
CELERY_IMPORTS = tuple(globals().get('CELERY_IMPORTS', ())) + ('testcases.review_tasks',)
# 旧默认队列仍由 default 消费；新任务按业务隔离。
CELERY_TASK_ROUTES = {
    **CELERY_TASK_ROUTES,
    'code_analysis.tasks.*': {'queue': 'code_analysis'},
    'testcases.review_tasks.*': {'queue': 'testcase_review'},
    'knowledge.process_document': {'queue': 'knowledge'},
    'ui_automation.run_ui_script_batch': {'queue': 'ui_execution'},
    'ui_automation.run_ui_healing': {'queue': 'ui_execution'},
    'task_center.tasks.*': {'queue': 'task_center'},
}
'''
compile(source, str(settings), 'exec')
# 仅增量包裹目标版本的 ASGI 应用，保留其全部路由与认证。
asgi = root / 'wharttest_django/asgi.py'
asgi_source = asgi.read_text()
import ast
parsed = ast.parse(asgi_source)
if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'application' for t in n.targets) for n in parsed.body):
    raise SystemExit('ASGI application 结构不符')
if 'WatchedApplication' in asgi_source:
    raise SystemExit('ASGI 已有 watchdog，请重新采集配置')
asgi_source += '\nfrom .web_runtime import WatchedApplication\napplication = WatchedApplication(application)\n'
compile(asgi_source, str(asgi), 'exec')
old_web = p['program:django']['command']
assert '--port 8000 --workers=1' in old_web
original = original.replace(old_web, old_web + ' --lifespan=on --timeout-graceful-shutdown=30', 1)
original += '\n' + (pathlib.Path(__file__).parent / 'core-program.conf').read_text()
# 必须对候选配置整体解析，不使用本地整文件覆盖目标旧配置。
configparser.RawConfigParser().read_string(original)
nginx = root / 'frontend.conf'
nginx_source = nginx.read_text()
if 'http://backend:8001' in nginx_source:
    raise SystemExit('前端已配置新端口，请重新采集')
assert 'proxy_pass http://backend:8000;' in nginx_source
anchor = '    location /api/ {'
assert nginx_source.count(anchor) == 1
nginx_source = nginx_source.replace(anchor, (pathlib.Path(__file__).parent / 'core-location.conf').read_text() + '\n' + anchor, 1)
asgi.write_text(asgi_source)
nginx.write_text(nginx_source)
conf.write_text(original)
settings.write_text(source)
print('增量补丁写入完成')
