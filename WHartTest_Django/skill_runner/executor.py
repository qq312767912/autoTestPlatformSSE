"""独立执行面：每次传入精确版本快照，无业务目录共享挂载。"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass,field
from pathlib import Path,PurePosixPath

_GUARD_DIR=Path(__file__).resolve().parent.parent/'orchestrator_integration'/'builtin_tools'
sys.path.insert(0,str(_GUARD_DIR))
from skill_exec_guard import SkillCommandError,parse_skill_command,validate_skill_command,build_skill_env,build_preexec_fn

MAX_PACKAGE_BYTES=24*1024*1024
MAX_FILES=2000
MAX_OUTPUT=256*1024
MAX_ARTIFACT_BYTES=8*1024*1024
RUNTIME_KEYS={'WHARTTEST_API_KEY','WHARTTEST_BACKEND_URL','PYTHONUTF8','PYTHONIOENCODING'}

class SkillRunnerError(ValueError):pass

@dataclass
class JobRequest:
    package: list
    command: str
    timeout_seconds: int=120
    runtime_env: dict=field(default_factory=dict)
    user_id: object=None
    project_id: object=None
    chat_session_id: object=None
    test_case_id: object=None
    skill_id: object=None
    skill_name: object=None
    skill_version_id: object=None
    source_sha256: object=None

@dataclass
class JobResult:
    returncode:int
    stdout:str
    stderr:str
    duration_ms:int
    workspace_files:list=field(default_factory=list)
    artifacts:list=field(default_factory=list)


def safe_relative(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
        raise SkillRunnerError('非法相对文件路径')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in {'.','..'} for p in value.split('/')) or ':' in value:
        raise SkillRunnerError('文件路径不能越界')
    return path


def unpack_package(package,root):
    if not isinstance(package,list) or not package or len(package)>MAX_FILES:
        raise SkillRunnerError('Skill 文件清单无效或超限')
    total=0;seen=set()
    for item in package:
        if not isinstance(item,dict):raise SkillRunnerError('Skill 文件清单无效')
        path=safe_relative(item.get('name'))
        if str(path) in seen:raise SkillRunnerError('重复文件路径')
        seen.add(str(path))
        try:data=base64.b64decode(item['data_b64'],validate=True)
        except (KeyError,ValueError,TypeError) as exc:raise SkillRunnerError('Skill 文件编码无效') from exc
        total+=len(data)
        if total>MAX_PACKAGE_BYTES:raise SkillRunnerError('Skill 文件大小超限')
        if hashlib.sha256(data).hexdigest()!=item.get('sha256'):raise SkillRunnerError('Skill 文件指纹失配')
        dest=root.joinpath(*path.parts);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    if 'SKILL.md' not in seen:raise SkillRunnerError('缺少 SKILL.md')


def prepare_argv(command,skill_dir):
    invocation=parse_skill_command(command)
    if invocation.env_overrides or invocation.stdin_text is not None:
        raise SkillRunnerError('请直接执行版本包内脚本，不支持临时环境覆盖或 heredoc')
    validate_skill_command(invocation.argv,skill_dir=str(skill_dir))
    argv=list(invocation.argv);launcher=Path(argv[0]).name.lower()
    # 忽略调用方机器上的绝对解释器位置，仅解析 runner 自身预装的解释器。
    if launcher not in {'python','python3','node','playwright-cli','browser-use','npx'}:
        raise SkillRunnerError('沙箱仅支持预装解释器及浏览器CLI')
    if launcher=='npx':
        argv=[x for x in argv if x!='--no-install'][1:]
        if not argv or argv[0]!='playwright-cli':raise SkillRunnerError('npx仅允许预装playwright-cli')
        launcher='playwright-cli'
    if launcher in {'python','python3','node'}:
        args=argv[1:];index=0
        while index<len(args) and args[index] in {'-u','-B','-I','--'}:index+=1
        if index==len(args) or args[index].startswith('-'):
            raise SkillRunnerError('必须执行版本包内的脚本文件')
        script=Path(args[index])
        if script.is_absolute():raise SkillRunnerError('脚本必须使用版本包内相对路径')
        script_path=(skill_dir/script).resolve()
        if not script_path.is_relative_to(skill_dir.resolve()) or not script_path.is_file():
            raise SkillRunnerError('脚本不存在或超出版本包')
        args[index]=str(script_path);argv=[launcher,*args]
    else:argv[0]=launcher
    executable=shutil.which(launcher)
    if not executable:raise SkillRunnerError(f'镜像未预装 {launcher}，请构建依赖后重试')
    argv[0]=executable
    return argv


def _kill_group(proc):
    try:os.killpg(proc.pid,signal.SIGKILL)
    except (ProcessLookupError,PermissionError):pass


def _reap_container_children():
    # 容器 PID1 独占执行面且并发固定为1，结束时回收脱离原进程组的后台子进程。
    if os.environ.get('SKILL_RUNNER_REAP_ALL')!='1':return
    if os.getpid()!=1:raise SkillRunnerError('全量子进程回收只允许在容器 PID1 启用')
    for _ in range(4):
        for p in Path('/proc').iterdir():
            if p.name.isdigit() and int(p.name)>1:
                try:os.kill(int(p.name),signal.SIGKILL)
                except (ProcessLookupError,PermissionError):pass
        while True:
            try:
                pid,_=os.waitpid(-1,os.WNOHANG)
                if not pid:break
            except ChildProcessError:break


def _artifacts(workspace):
    result=[];total=0
    for path in sorted(workspace.rglob('*')):
        if path.is_symlink():continue
        if not path.is_file() or not path.resolve().is_relative_to(workspace.resolve()):continue
        if len(result)>=20:break
        size=path.stat().st_size
        if total+size>MAX_ARTIFACT_BYTES:raise SkillRunnerError('产物超过8MiB回传限制，请拆分输出')
        data=path.read_bytes();total+=len(data)
        result.append({'name':path.relative_to(workspace).as_posix(),'data_b64':base64.b64encode(data).decode(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return result


def execute_job(request):
    if not isinstance(request.runtime_env,dict) or set(request.runtime_env)-RUNTIME_KEYS:
        raise SkillRunnerError('不允许的运行时环境变量')
    timeout=max(1,min(int(request.timeout_seconds),120))
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='skill-job-',dir=os.environ.get('SKILL_RUNNER_WORKSPACE_ROOT')) as temp:
        root=Path(temp);skill=root/'package';skill.mkdir();workspace=root/'output';workspace.mkdir()
        unpack_package(request.package,skill)
        try:argv=prepare_argv(request.command,skill)
        except SkillCommandError as exc:raise SkillRunnerError(str(exc)) from exc
        env=build_skill_env(source_env={'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')},runtime_env=request.runtime_env)
        env.update({'HOME':str(root),'TMPDIR':str(root),'PYTHONDONTWRITEBYTECODE':'1','NODE_PATH':'/usr/local/lib/node_modules','SCREENSHOT_DIR':str(workspace),'ARTIFACT_DIR':str(workspace),'SKILL_OUTPUT_DIR':str(workspace)})
        # Expand only public output-directory placeholders, without a shell or
        # exposing runtime credentials through generic environment expansion.
        output_variable = re.compile(r'\$\{(SKILL_OUTPUT_DIR|ARTIFACT_DIR|SCREENSHOT_DIR)\}|\$(SKILL_OUTPUT_DIR|ARTIFACT_DIR|SCREENSHOT_DIR)\b')
        argv = [output_variable.sub(lambda m: env[m.group(1) or m.group(2)], arg) for arg in argv]
        proc=None;timed_out=False
        try:
            with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
                proc=subprocess.Popen(argv,cwd=skill,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True,preexec_fn=build_preexec_fn(timeout))
                try:proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:timed_out=True
                finally:
                    _kill_group(proc);proc.wait(timeout=10);_reap_container_children()
                out.seek(0);stdout=out.read(MAX_OUTPUT+1);err.seek(0);stderr=err.read(MAX_OUTPUT+1)
            if timed_out:raise SkillRunnerError('命令超时，已回收整个任务进程')
            artifacts=_artifacts(workspace)
            secrets=[str(v) for k,v in request.runtime_env.items() if k=='WHARTTEST_API_KEY' and v]
            def clean(data):
                text=data[:MAX_OUTPUT].decode('utf-8','replace')
                for value in secrets:text=text.replace(value,'[REDACTED]')
                return text+('\n[output truncated]' if len(data)>MAX_OUTPUT else '')
            return JobResult(proc.returncode,clean(stdout),clean(stderr),int((time.monotonic()-started)*1000),[x['name'] for x in artifacts],artifacts)
        finally:
            if proc and proc.poll() is None:_kill_group(proc);proc.wait(timeout=10)
