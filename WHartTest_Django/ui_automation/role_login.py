"""角色账号配置：密码以项目绑定的密文保存，只在登录步骤派发前解密。"""
import base64
import hashlib
import json
from urllib.parse import urlparse
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from rest_framework.exceptions import ValidationError

FIELDS = ('url','account_selector','password_selector','captcha_selector','submit_selector','success_selector')

def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+':ui-role-login').encode()).digest()))

def password_value(row, project_id):
    token = row.get('password_ref')
    if not token: return ''
    try:
        data=json.loads(cipher().decrypt(token.encode()))
        if data['project'] != project_id: raise ValueError()
        return data['password']
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise ValidationError('账号密码凭据无效，请重新输入密码')

def normalize_login(row, project_id):
    account=str(row.get('account') or '').strip()
    if not account: return {}
    if len(account)>256: raise ValidationError('账号不能超过256字')
    token=row.get('password_ref') or ''
    if row.get('password'):
        password=str(row['password'])
        if len(password)>1024: raise ValidationError('密码过长')
        token=cipher().encrypt(json.dumps({'project':project_id,'password':password}).encode()).decode()
    elif token:
        password_value(row,project_id)
    raw=row.get('login') or {}
    if not isinstance(raw,dict): raise ValidationError('登录方式格式错误')
    config={key:str(raw.get(key) or '').strip() for key in FIELDS}
    if any(len(value)>2000 for value in config.values()): raise ValidationError('登录方式配置过长')
    if config['url'] and urlparse(config['url']).scheme not in ('http','https'):
        raise ValidationError('登录页必须为http或https地址')
    return {'account':account,'password_ref':token,'captcha':str(row.get('captcha') or 'A1ad'),'login':config}

def validate_login(role, project_id):
    config=role['login']
    required=['url','account_selector','submit_selector','success_selector']
    if password_value(role,project_id): required.append('password_selector')
    if role.get('captcha'): required.append('captcha_selector')
    if any(not config.get(key) for key in required):
        raise ValidationError('角色「%s」请展开登录方式，补齐登录页、输入框、登录按钮及登录成功标记' % role['name'])

def login_steps(role, project_id):
    validate_login(role,project_id)
    config=role['login']
    def step(key,value,selector=None):
        return {'ope_key':key,'ope_value':{'text':value} if key=='fill' else {'url':value} if key=='goto' else {},
                'element':{'locator_type':'css','locator_value':selector} if selector else None,'sensitive':True,'description':'角色账号登录'}
    steps=[step('goto',config['url']),step('fill',role['account'],config['account_selector'])]
    password=password_value(role,project_id)
    if password: steps.append(step('fill',password,config['password_selector']))
    if role.get('captcha'): steps.append(step('fill',role['captcha'],config['captcha_selector']))
    steps.extend([step('click','',config['submit_selector']),step('assert','',config['success_selector'])])
    return steps
