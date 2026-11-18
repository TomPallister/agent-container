"""Select reference names, never credentials. All account routing is private config."""
import json
import os
import re
import subprocess
from urllib.parse import urlsplit

REF='secret'+'ref:'

def reference(value):
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}',value):
        raise ValueError('Invalid GitHub secret name')
    return REF+value

def owner(args):
    if len(args)>2 and args[0]=='repo':
        for arg in args[2:]:
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+',arg):
                return repo_owner(arg)
    previous=''
    for arg in args:
        if previous in ('-R','--repo'):
            return repo_owner(arg)
        if arg.startswith('--repo='): return repo_owner(arg.split('=',1)[1])
        if arg.startswith('-R') and len(arg)>2: return repo_owner(arg[2:])
        path=arg.lstrip('/').split('/')
        if len(path)>1 and path[0] in ('repos','orgs','users'):
            return path[1].lower()
        if arg.startswith('https://github.com/'): return repo_owner(arg)
        previous=arg
    result=subprocess.run(['git','config','--get','remote.origin.url'],capture_output=True,check=False)
    return repo_owner(result.stdout.decode().strip())

def repo_owner(value):
    if value.startswith('git@github.com:'): value=value.split(':',1)[1]
    elif '://' in value:
        parsed=urlsplit(value)
        if parsed.hostname!='github.com': return ''
        value=parsed.path.lstrip('/')
    first=value.split('/',1)[0]
    return first.lower() if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*',first) else ''

def copilot(args):
    return bool(args and args[0]=='api' and any('copilot' in a.strip('/').split('/') for a in args[1:] if not a.startswith('--')))

def select(selected_owner,env=None,is_copilot=False):
    env=os.environ if env is None else env
    forced=env.get('GH_SECRET_NAME')
    routes=json.loads(env.get('AGENT_CONTAINER_GITHUB_OWNERS','{}'))
    selected=forced or (env.get('AGENT_CONTAINER_GITHUB_COPILOT') if is_copilot else None) or routes.get(selected_owner.lower()) or env.get('AGENT_CONTAINER_GITHUB_SECRET','')
    return reference(selected) if selected else ''

def gh(args):
    env=dict(os.environ)
    value=env.get('GH_TOKEN') or env.get('GITHUB_TOKEN') or select(owner(args),env,copilot(args))
    if value:
        if not value.startswith(REF) or reference(value[len(REF):])!=value: raise ValueError('gh accepts secret references only')
        env['GH_TOKEN']=value
        env.pop('GITHUB_TOKEN',None)
    os.execvpe('/usr/bin/gh',['/usr/bin/gh',*args],env)
