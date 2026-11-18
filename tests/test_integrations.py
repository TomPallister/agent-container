import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'image/runtime'))
import cli
from test_cli import config, module

class IntegrationTests(unittest.TestCase):
    def test_private_environment_cannot_override_trust_or_hold_credentials(self):
        cfg=config(); cfg['environment']={'GOG_ACCOUNT':'engineer@example.com','CX_APIKEY':cli.REF+'SCAN_API_KEY'}
        env=cli.container_environment(cli.validate_config(cfg),{'sharedDir':'/unused'},module())
        self.assertEqual(env['GOG_ACCOUNT'],'engineer@example.com')
        for key,value in [('PATH','/bad'),('HTTPS_PROXY','http://elsewhere'),('CX_APIKEY','raw-credential'),('LD_PRELOAD','/bad')]:
            with self.subTest(key=key):
                cfg['environment']={key:value}
                with self.assertRaises(cli.Error):
                    cli.container_environment(cli.validate_config(cfg),{'sharedDir':'/unused'},module())

    def test_owner_routes_disable_fixed_default_without_raw_values(self):
        cfg=config(); cfg.update(githubOwners={'example-org':'ORG_GITHUB_TOKEN'},githubCopilotSecret='COPILOT_TOKEN')
        env=cli.container_environment(cli.validate_config(cfg),{'sharedDir':'/unused'},module())
        self.assertNotIn('GH_TOKEN',env)
        self.assertEqual(json.loads(env['AGENT_CONTAINER_GITHUB_OWNERS']),cfg['githubOwners'])
        cfg['githubOwners']['https://example.com']='BAD'
        with self.assertRaises(cli.Error): cli.validate_config(cfg)

    def test_owner_selection_and_explicit_override(self):
        import github_auth as g
        env={'AGENT_CONTAINER_GITHUB_SECRET':'GITHUB_TOKEN','AGENT_CONTAINER_GITHUB_OWNERS':json.dumps({'example-org':'ORG_TOKEN'}),'AGENT_CONTAINER_GITHUB_COPILOT':'COPILOT_TOKEN'}
        self.assertEqual(g.select('EXAMPLE-ORG',env),cli.REF+'ORG_TOKEN')
        self.assertEqual(g.select('someone',env),cli.REF+'GITHUB_TOKEN')
        self.assertEqual(g.select('example-org',dict(env,GH_SECRET_NAME='OVERRIDE')),cli.REF+'OVERRIDE')
        for args in [['pr','list','-R','example-org/repo'],['api','/repos/example-org/repo'],['api','orgs/example-org/copilot/billing'],['repo','clone','example-org/repo']]:
            self.assertEqual(g.owner(args),'example-org')
        self.assertTrue(g.copilot(['api','orgs/example-org/copilot/billing']))
        self.assertFalse(g.copilot(['pr','create','--body','not about copilot']))

    def test_sealed_checkmarx_response_only(self):
        import client_shims as s
        import base64
        sealed='e30.e30.'+base64.urlsafe_b64encode((cli.REF+'CHECKMARX_API_KEY').encode()).decode().rstrip('=')
        self.assertEqual(s.sealed(sealed),sealed)
        for value in ['real-token','e30.e30.c2VjcmV0cmVmBAD',cli.REF+'CHECKMARX_API_KEY','e30.e30.'+base64.urlsafe_b64encode(b'unsealed-signature').decode().rstrip('=')]:
            with self.assertRaises(ValueError): s.sealed(value)
        self.assertEqual(s.checkmarx_url({'CX_BASE_AUTH_URI':'https://scanner.example.com','CX_TENANT':'example'}),'https://scanner.example.com/auth/realms/example/protocol/openid-connect/token')
        for env in [{},{'CX_BASE_URI':'http://scanner.example.com','CX_TENANT':'example'},{'CX_BASE_URI':'https://scanner.example.com','CX_TENANT':'../bad'}]:
            with self.assertRaises(ValueError): s.checkmarx_url(env)

    def test_gog_never_uses_real_tokens_or_keyring_login(self):
        import client_shims as s
        with patch.object(s.os,'execvpe') as execute:
            s.gog(['drive','ls'],{})
            self.assertEqual(execute.call_args.args[2]['GOG_ACCESS_TOKEN'],cli.REF+'GOOGLE_ACCESS_TOKEN')
        for args,env in [(['auth','add','engineer@example.com'],{}),(['drive','ls'],{'GOG_ACCESS_TOKEN':'raw-value'}),(['--access-token=raw-value','drive','ls'],{})]:
            with patch.object(s.os,'execvpe') as execute:
                with self.assertRaises(ValueError): s.gog(args,env)
                execute.assert_not_called()

if __name__=='__main__': unittest.main()
