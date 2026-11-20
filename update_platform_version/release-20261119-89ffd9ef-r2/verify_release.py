"""Offline packaging gate; explicit suite avoids importing live staging scripts."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
SUITE = [
    'test_check_actuators', 'test_preflight_queues', 'test_prepare_assets',
    'test_prepare_runtime', 'test_resolve_backup', 'test_credential_check',
]


def verify():
    env = ROOT / 'deploy_env'
    required = [
        '00-preflight.sh', '02-backup.sh', '04-deploy.sh', '05-verify.sh',
        '06-rollback-app.sh', '07-start-actuators.sh', 'check_actuators.py',
        'prepare_assets.py', 'prepare_runtime.py', 'resolve_backup.py',
        'credential_check.py', 'check_queues.py', 'playwright-mcp-entrypoint.sh',
        'playwright-mcp-config.template.json', 'ones-url-hotfix/apply.sh',
        'ones-url-hotfix/rollback.sh', 'ones-url-hotfix/expected.sha256',
    ]
    for name in required:
        path = env / name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError('部署包缺少必要文件：' + name)
    for path in env.rglob('*.sh'):
        subprocess.run(['bash', '-n', str(path)], check=True)
    revision = (env / 'VERSION').read_text().strip()
    # Use the exact release runtime for amqp/kombu and Python compatibility.
    # No DB, credentials or network access is supplied to these fixture tests.
    subprocess.run([
        'docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python',
        '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-v', str(ROOT) + ':/checks:ro', '-w', '/checks',
        f'wharttest-250-backend:update-{revision}-arm64',
        '-m', 'unittest', *SUITE,
    ], check=True)
    print('发布门禁通过：必要文件、Shell语法、目标镜像内回归测试。')


if __name__ == '__main__':
    verify()
