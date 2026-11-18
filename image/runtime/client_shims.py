"""Tokenless service clients. Provider policy and real OAuth remain in secret-proxy."""
import base64
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

REF='secret'+'ref:'

def reference(value):
    if not isinstance(value,str) or not re.fullmatch(re.escape(REF)+r'[A-Z][A-Z0-9_]{0,127}',value):
        raise ValueError('A canonical secret reference is required')
    return value

def gog(args,env):
    if any(arg=='auth' or arg.split('=',1)[0]=='--access-token' for arg in args):
        raise ValueError('Use proxy-managed Google access, not local login/keyring/token options')
    env=dict(env,GOG_ACCESS_TOKEN=reference(env.get('GOG_ACCESS_TOKEN',REF+'GOOGLE_ACCESS_TOKEN')))
    os.execvpe('/usr/local/bin/gog-real',['/usr/local/bin/gog-real',*args],env)

def sealed(value,expected='CHECKMARX_API_KEY'):
    if not isinstance(value,str) or len(value)>32768 or len(value.split('.'))!=3:
        raise ValueError('Invalid sealed response')
    signature=value.rsplit('.',1)[1]
    raw=base64.b64decode(signature+'='*(-len(signature)%4),altchars=b'-_',validate=True).decode('ascii')
    if raw!=REF+expected: raise ValueError('Unsealed response refused')
    return value

def checkmarx_url(env):
    base=env.get('CX_BASE_AUTH_URI') or env.get('CX_BASE_URI','')
    parsed=urllib.parse.urlsplit(base)
    realm=env.get('CX_TENANT','')
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or not re.fullmatch(r'[A-Za-z0-9_-]+',realm):
        raise ValueError('Explicit HTTPS Checkmarx base URI and tenant are required')
    return base.rstrip('/')+'/auth/realms/'+realm+'/protocol/openid-connect/token'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Token exchange redirects refused')

def cx(args,env):
    if any(arg.split('=',1)[0] in ('--apikey','--client-secret','--client-id') for arg in args):
        raise ValueError('Configure proxy-managed Checkmarx access in the environment')
    env=dict(env)
    if args in (['version'],['--version'],['--help']):
        env.pop('CX_APIKEY',None)
        os.execvpe('/usr/local/bin/cx-real',['/usr/local/bin/cx-real',*args],env)
        return
    value=env.get('CX_APIKEY','')
    if value.startswith(REF):
        reference(value)
        request=urllib.request.Request(checkmarx_url(env),data=urllib.parse.urlencode({'grant_type':'refresh_token','client_id':'ast-app','refresh_token':value}).encode(),method='POST')
        opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request,timeout=60) as response:
            data=response.read(65537)
        if len(data)>65536: raise ValueError('Oversized sealed response')
        reply=json.loads(data)
        sealed(reply['access_token'],'CHECKMARX_ACCESS_TOKEN')
        value=reply['refresh_token']
    env['CX_APIKEY']=sealed(value)
    os.execvpe('/usr/local/bin/cx-real',['/usr/local/bin/cx-real',*args],env)
