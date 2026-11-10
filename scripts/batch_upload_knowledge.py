#!/usr/bin/env python3
"""交互登录并递归上传文件夹文档。依赖：pip install requests。"""
import getpass
import json
from datetime import datetime
from pathlib import Path
import sys

import requests

BASE_URL = 'http://10.114.70.116:8913'
ALLOWED = {'pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'txt', 'md', 'html', 'htm'}


def unpack(response):
    if not response.ok:
        raise RuntimeError(f'HTTP {response.status_code}: {response.text[:600]}')
    body = response.json()
    if isinstance(body, dict) and body.get('success') is False:
        raise RuntimeError(str(body.get('error') or body.get('message') or body))
    return body.get('data', body) if isinstance(body, dict) else body


class Client:
    def __init__(self):
        self.session = requests.Session()
        self.refresh = None

    def login(self):
        username = input('账号：').strip()
        password = getpass.getpass('密码（输入不显示）：')
        data = unpack(self.session.post(
            BASE_URL + '/api/token/',
            json={'username': username, 'password': password}, timeout=(15, 60)))
        self.session.headers['Authorization'] = 'Bearer ' + data['access']
        self.refresh = data.get('refresh')

    def request(self, method, endpoint, **kwargs):
        files = kwargs.get('files', {})
        response = self.session.request(method, BASE_URL + endpoint,
                                        timeout=(15, 300), **kwargs)
        if response.status_code == 401 and self.refresh:
            data = unpack(self.session.post(BASE_URL + '/api/token/refresh/',
                json={'refresh': self.refresh}, timeout=(15, 60)))
            self.session.headers['Authorization'] = 'Bearer ' + data['access']
            self.refresh = data.get('refresh', self.refresh)
            for _, file in files.values():
                file.seek(0)
            response = self.session.request(method, BASE_URL + endpoint,
                                            timeout=(15, 300), **kwargs)
        return unpack(response)

    def knowledge_bases(self):
        items, page = [], 1
        while True:
            data = self.request('GET', '/api/knowledge/knowledge-bases/',
                                params={'page': page, 'page_size': 200})
            if isinstance(data, list):
                return items + data
            items.extend(data['results'])
            if not data.get('next'):
                return items
            page += 1


def main():
    client = Client()
    print('平台：' + BASE_URL)
    client.login()
    bases = client.knowledge_bases()
    if not bases:
        raise RuntimeError('当前账号没有可用知识库，请先在平台创建或检查权限。')
    for index, kb in enumerate(bases, 1):
        print(f"{index}. {kb['name']} | 项目：{kb.get('project_name', '')} | ID：{kb['id']}")
    while True:
        choice = input('输入目标知识库序号：').strip()
        if choice.isdigit() and 1 <= int(choice) <= len(bases):
            kb = bases[int(choice) - 1]
            break
        print('序号无效，请重新输入。')
    while True:
        folder = Path(input('待上传文件夹绝对路径：').strip().strip('\"\'')).expanduser()
        if folder.is_absolute() and folder.is_dir():
            break
        print('请输入存在的文件夹绝对路径。')
    paths = sorted(p for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    documents = [p for p in paths if p.suffix.lower().lstrip('.') in ALLOWED
                 and not p.name.startswith(('~$', '.'))]
    print(f"目标知识库：{kb['name']}；递归找到 {len(documents)} 个支持的文档，跳过 {len(paths) - len(documents)} 个其他文件。")
    if not documents:
        return
    records = []
    report = Path.cwd() / ('knowledge_upload_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
    # 提前验证报告位置可写；逐份保存，便于中途停止后核对。
    report.write_text('[]', encoding='utf-8')
    for index, path in enumerate(documents, 1):
        record = {'file': str(path), 'knowledge_base': str(kb['id'])}
        print(f'[{index}/{len(documents)}] {path.relative_to(folder)}', flush=True)
        try:
            ext = path.suffix.lower().lstrip('.')
            with path.open('rb') as stream:
                data = client.request('POST', '/api/knowledge/documents/',
                    data={'knowledge_base': kb['id'], 'title': path.stem,
                          'document_type': 'html' if ext == 'htm' else ext},
                    files={'file': (path.name, stream)})
            record.update(result='uploaded', response=data)
            print('  上传成功（后台异步解析）')
        except (requests.RequestException, RuntimeError, ValueError, OSError) as exc:
            record.update(result='failed_or_unknown', error=str(exc))
            print(f'  失败或结果待确认：{exc}')
        records.append(record)
        report.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    success = sum(r['result'] == 'uploaded' for r in records)
    print(f'完成：上传成功 {success}，失败或待确认 {len(records) - success}。报告：{report}')
    print('超时可能已在服务端创建文档，请核对后补传；重新运行会再次上传所有文档。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已停止，已处理文件的结果保存在运行目录的报告中。')
        sys.exit(130)
    except (requests.RequestException, RuntimeError, ValueError, KeyError, OSError) as exc:
        print(f'执行失败：{exc}', file=sys.stderr)
        sys.exit(1)
