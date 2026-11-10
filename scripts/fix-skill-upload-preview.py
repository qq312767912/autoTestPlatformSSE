#!/usr/bin/env python3
"""Restore upload-preview on the inspected local backend without replacing its views.

Usage: python3 scripts/fix-skill-upload-preview.py --apply
Backups and rollback metadata are retained under /private/tmp.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

EXPECTED = 'c4c88fd08989b21777b061bfb81b6ce07822cf2fb69eeff0f509ed98877c84fc'
ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def restart_web(container):
    # Let supervisor respawn only the API worker; avoid the slow container entrypoint.
    run('docker', 'exec', container, 'python', '-c',
        'from pathlib import Path; import os,signal; '
        'pids=[int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit() '
        'and (p/"cmdline").exists() and any(Path(a.decode()).name=="uvicorn" '
        'for a in (p/"cmdline").read_bytes().split(b"\\0")[:3] if a)]; '
        'assert len(pids)==1, pids; os.kill(pids[0],signal.SIGKILL)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', required=True)
    parser.add_argument('--container', default='wharttest-new-backend')
    args = parser.parse_args()
    container = args.container
    backup = Path(tempfile.mkdtemp(prefix='whart-upload-preview-', dir='/private/tmp'))
    original = backup / 'views.py.orig'
    run('docker', 'cp', f'{container}:/app/skills/views.py', str(original))
    source = original.read_text()
    if hashlib.sha256(original.read_bytes()).hexdigest() != EXPECTED:
        raise SystemExit(f'Runtime source changed; no changes made. Backup: {backup}')
    current = ast.parse(source)
    target = next(n for n in current.body if isinstance(n, ast.ClassDef) and n.name == 'SkillViewSet')
    assert not any(isinstance(n, ast.FunctionDef) and n.name == 'upload_preview' for n in target.body)
    reference = (ROOT / 'WHartTest_Django/skills/views.py').read_text()
    method = next(n for n in ast.walk(ast.parse(reference)) if isinstance(n, ast.FunctionDef) and n.name == 'upload_preview')
    start = min([method.lineno] + [d.lineno for d in method.decorator_list])
    snippet = ''.join(reference.splitlines(keepends=True)[start-1:method.end_lineno]) + '\n\n'
    # The old runtime lacks the newer module-level zipfile import.
    snippet = snippet.replace('        import tempfile\n', '        import tempfile\n        import zipfile\n', 1)
    upload = next(n for n in target.body if isinstance(n, ast.FunctionDef) and n.name == 'upload')
    index = min([upload.lineno] + [d.lineno for d in upload.decorator_list])-1
    lines = source.splitlines(keepends=True)
    patched = ''.join(lines[:index]) + snippet + ''.join(lines[index:])
    ast.parse(patched)
    payload = backup / 'views.py'
    payload.write_text(patched)
    helper = ROOT / 'WHartTest_Django/skills/import_preview.py'
    metadata = {'container': container, 'original_sha256': EXPECTED,
                'image': run('docker', 'inspect', '--format', '{{.Image}}', container).strip(),
                'backup': str(original), 'added_file': '/app/skills/import_preview.py'}
    (backup / 'rollback.json').write_text(json.dumps(metadata, indent=2))
    # Preflight dependencies using the target interpreter before changing live files.
    run('docker', 'exec', container, 'python', 'manage.py', 'shell', '-c',
        'from skills.canonical import pick_canonical; from skills.validation import parse_manifest, scan_package_dir; '
        'from skills.versions import SkillVersionService; assert callable(SkillVersionService.next_version)')
    run('docker', 'cp', str(helper), f'{container}:/tmp/skill-upload-preview-helper.py')
    run('docker', 'exec', container, 'python', '-m', 'py_compile', '/tmp/skill-upload-preview-helper.py')
    helper_sha = hashlib.sha256(helper.read_bytes()).hexdigest()
    run('docker', 'exec', container, 'python', '-c',
        'from pathlib import Path; import hashlib; p=Path("/app/skills/import_preview.py"); '
        f'assert not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()=="{helper_sha}", "Existing helper differs"')
    package = ROOT / 'WHartTest_Skills/excel-to-ui-case.zip'
    if not package.is_file():
        skill_dir = ROOT / 'WHartTest_Skills/excel-to-ui-case'
        assert (skill_dir / 'SKILL.md').is_file(), 'Missing test skill'
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(skill_dir.rglob('*')):
                if file.is_file():
                    archive.write(file, file.relative_to(skill_dir.parent))
    run('docker', 'cp', str(package), f'{container}:/tmp/excel-to-ui-case-preview-test.zip')
    # Recheck source immediately before mutation.
    run('docker', 'exec', container, 'python', '-c',
        f'import hashlib; assert hashlib.sha256(open("/app/skills/views.py","rb").read()).hexdigest()=="{EXPECTED}"')
    try:
        run('docker', 'cp', str(helper), f'{container}:/app/skills/import_preview.py')
        run('docker', 'cp', str(payload), f'{container}:/app/skills/views.py')
        run('docker', 'exec', container, 'python', 'manage.py', 'shell', '-c', SMOKE)
        restart_web(container)
        run('docker', 'exec', container, 'python', 'manage.py', 'shell', '-c', LIVE)
    except BaseException:
        run('docker', 'cp', str(original), f'{container}:/app/skills/views.py')
        # The new helper is unused after rollback; retain it for inspection.
        restart_web(container)
        run('docker', 'exec', container, 'python', 'manage.py', 'check')
        print(f'Rolled back runtime views. Backup: {backup}')
        raise
    print(f'PASS: upload preview route and ZIP validated. Backup: {backup}')


SMOKE = '''
from django.urls import resolve
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate
from skills.models import Skill, SkillVersion
from projects.models import Project
user = User.objects.filter(is_superuser=True, is_active=True).first()
project = Project.objects.first()
assert user and project, 'Need existing admin and project for read-only smoke test'
url = f'/api/projects/{project.pk}/skills/upload-preview/'
match = resolve(url)
assert match.func.actions.get('post') == 'upload_preview'
before = (Skill.objects.count(), SkillVersion.objects.count())
factory = APIRequestFactory()
def invoke(data, authenticated=True):
    request = factory.post(url, data, format='multipart')
    if authenticated: force_authenticate(request, user=user)
    return match.func(request, **match.kwargs)
with open('/tmp/excel-to-ui-case-preview-test.zip', 'rb') as file:
    data = file.read()
response = invoke({'file': SimpleUploadedFile('excel-to-ui-case.zip', data, content_type='application/zip')})
assert response.status_code == 200, str(response.data)
assert response.data['data'][0]['name'] == 'excel-to-ui-case'
assert invoke({}).status_code == 400
assert invoke({'file': SimpleUploadedFile('invalid.zip', b'not a zip')}).status_code == 400
assert invoke({}, authenticated=False).status_code in (401, 403)
assert before == (Skill.objects.count(), SkillVersion.objects.count()), 'Preview must not write assets'
print('PASS: valid ZIP, missing file, invalid ZIP, anonymous rejection, no asset writes')
'''

LIVE = '''
import time, requests
from django.contrib.auth.models import User
from projects.models import Project
from rest_framework_simplejwt.tokens import RefreshToken
user=User.objects.filter(is_superuser=True,is_active=True).first()
project=Project.objects.first()
headers={'Authorization':'Bearer '+str(RefreshToken.for_user(user).access_token)}
url=f'http://127.0.0.1:8000/api/projects/{project.pk}/skills/upload-preview/'
for attempt in range(45):
    try:
        with open('/tmp/excel-to-ui-case-preview-test.zip','rb') as file:
            response=requests.post(url,headers=headers,files={'file':('excel-to-ui-case.zip',file,'application/zip')},timeout=10)
        assert response.status_code==200, str(response.status_code)
        payload=response.json()
        while isinstance(payload,dict) and 'data' in payload:
            payload=payload['data']
        assert isinstance(payload,list) and payload[0]['name']=='excel-to-ui-case', str(payload)
        print('PASS: live HTTP upload-preview 200')
        break
    except requests.ConnectionError:
        time.sleep(1)
else: raise AssertionError('Backend did not recover')
'''

if __name__ == '__main__':
    main()
