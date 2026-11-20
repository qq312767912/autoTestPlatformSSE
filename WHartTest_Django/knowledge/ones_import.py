"""ONES 网页接口适配：凭据只用于当前请求，不写入配置。"""
import requests
import os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from rest_framework import serializers
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError


DEFAULT_ONES_URL = 'https://10.122.212.49:60000'


def normalize_ones_url(value):
    if not isinstance(value, str):
        raise serializers.ValidationError('ONES 地址必须是字符串')
    value = value.strip().rstrip('/')
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise serializers.ValidationError('ONES 地址端口无效')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise serializers.ValidationError('请输入完整 HTTP/HTTPS 服务地址，不包含路径或账号密码')
    try:
        URLValidator(schemes=['http', 'https'])(value)
    except DjangoValidationError:
        raise serializers.ValidationError('ONES 地址格式无效')
    if port is not None and port < 1:
        raise serializers.ValidationError('ONES 地址端口无效')
    return value


def probe_ones(value):
    base = normalize_ones_url(value)
    try:
        with requests.Session() as session:
            with session.get(base + '/project/api/project/auth/login', timeout=(5, 8), allow_redirects=False, stream=True) as response:
                code = response.status_code
                connected = code in (200, 400, 401, 403, 405)
                return {'connected': connected, 'status_code': code, 'message': ('服务接口可达，请使用邮箱密码登录验证' if connected else '接口返回异常状态 HTTP '+str(code))}
    except requests.exceptions.SSLError:
        return {'connected': False, 'message': 'HTTPS 证书校验失败，请核对证书域名及服务器信任链'}
    except requests.exceptions.Timeout:
        return {'connected': False, 'message': '连接或响应超时，请检查地址、端口和内网网络'}
    except requests.RequestException:
        return {'connected': False, 'message': '连接失败，请检查地址、端口和网络'}


class OnesRequestSerializer(serializers.Serializer):
    preview = serializers.BooleanField(default=False)
    attachment_uuids = serializers.ListField(child=serializers.CharField(), required=False)
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    space_uuid = serializers.RegexField(r'^[A-Za-z0-9_-]+$', required=False)
    page_uuids = serializers.ListField(
        child=serializers.RegexField(r'^[A-Za-z0-9_-]+$'), max_length=200,
        required=False, allow_empty=False,
    )


class OnesClient:
    base = DEFAULT_ONES_URL
    team = 'BfkaEsWn'

    def __init__(self, email, password, base_url=None):
        self.base = normalize_ones_url(base_url or self.base)
        self.session = requests.Session()
        try:
            data = self.request('POST', '/project/api/project/auth/login',
                                json={'email': email, 'password': password})
            user = data.get('user') or {}
            if not user.get('uuid') or not user.get('token'):
                raise ValueError('ONES 登录失败，请检查账号密码')
            self.session.headers.update({'Ones-User-Id': user['uuid'],
                                         'Ones-Auth-Token': user['token']})
        except Exception:
            self.session.close()
            raise

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base + path,
                                            timeout=30, allow_redirects=False, **kwargs)
            if response.status_code != 200:
                raise ValueError(f'ONES 请求失败（HTTP {response.status_code}）')
            return response.json()
        except requests.exceptions.JSONDecodeError:
            raise ValueError('ONES 返回了无效数据') from None
        except requests.RequestException:
            raise ValueError('无法连接 ONES，请检查服务器网络') from None

    def spaces(self):
        return self.request('GET', f'/wiki/api/wiki/team/{self.team}/spaces',
                            params={'page_count': 'false', 'ignore_wiki_admin': 'true'}).get('spaces', [])

    def pages(self, space):
        return self.request('GET', f'/wiki/api/wiki/team/{self.team}/space/{space}/pages',
                            params={'status': 1}).get('pages', [])

    def page(self, page):
        return self.request('GET', f'/wiki/api/wiki/team/{self.team}/page/{page}',
                            params={'version': ''})

    def attachments(self, space, html):
        """枚举页面中的附件卡片，返回可导入的附件。"""
        soup = BeautifulSoup(html or '', 'html.parser')
        for card in soup.select('.ones-file-card-block[data-card-uuid]'):
            data = self.request('GET', f'/wiki/api/wiki/team/{self.team}/space/{space}/card/{card["data-card-uuid"]}', params={'ref_type': 1})
            for item in (data.get('config') or {}).get('attachments', []):
                yield item

    def download(self, resource):
        data = self.request('GET', f'/wiki/api/project/team/{self.team}/res/attachment/{resource}', params={'action': 'download'})
        url = data.get('url', '')
        if urlparse(url).scheme not in ('http', 'https'):
            raise ValueError('附件下载地址无效')
        # 签名下载地址使用独立请求，不向文件服务器发送 ONES 认证头。
        with requests.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise ValueError('附件超过 50MB')
                chunks.append(chunk)
        return os.path.basename(data.get('name') or resource), b''.join(chunks)


def page_text(html):
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup(['script', 'style', 'nav']):
        tag.decompose()
    return soup.get_text('\n', strip=True)
