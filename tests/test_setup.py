"""First-run orchestration, without creating a VM or contacting a vault."""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli as c


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings/config.json'
        self.module = Mock()
        self.module.private_read.return_value = b'a' * 48
        self.proxy = {'sharedDir': '/unused', 'environment': {'GH_TOKEN': c.REF + 'GITHUB_TOKEN'}}
        self.runtime = Mock()

    @contextlib.contextmanager
    def prompts(self, github='', reference='', account=''):
        answers = ['Example Engineer', 'engineer@example.com', github, '', reference]
        if reference:
            answers.append(account)
        answers.append('')  # optional public build CA
        @contextlib.contextmanager
        def snapshot(*_):
            yield self.module, self.proxy, Mock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('cli.sys.platform', 'darwin'))
            stack.enter_context(patch('cli.sys.stdin.isatty', return_value=True))
            stack.enter_context(patch('cli.discover_proxy', return_value='/example/client.json'))
            proxy_call = stack.enter_context(patch('cli.proxy_snapshot', side_effect=snapshot))
            runtime_call = stack.enter_context(patch('cli.Runtime', return_value=self.runtime))
            stack.enter_context(patch('builtins.input', side_effect=answers))
            stack.enter_context(patch('builtins.print'))
            yield proxy_call, runtime_call

    def test_defaults_and_private_settings_then_build_and_init(self):
        with self.prompts() as (proxy_call, runtime_call):
            c.setup(self.path)
        cfg = json.loads(self.path.read_bytes())
        self.assertEqual(cfg['githubSecret'], 'GITHUB_TOKEN')
        self.assertEqual(cfg['unlock'], {'method': 'manual'})
        self.assertEqual(cfg['machine'], 'agent-container')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        runtime_call.assert_called_once_with(cfg, config_path=self.path)
        self.assertEqual([call[0] for call in self.runtime.mock_calls], ['build', 'prepare'])
        self.runtime.prepare.assert_called_once_with(self.proxy, self.module, initialise=True)
        self.assertEqual(proxy_call.call_count, 2)

    def test_can_explicitly_skip_the_suggested_github_helper(self):
        with self.prompts(github='-'):
            try:
                c.setup(self.path)
            except c.Error as error:
                self.fail('Explicit helper opt-out was rejected: ' + str(error))
        cfg = json.loads(self.path.read_bytes())
        self.assertEqual(cfg['githubSecret'], '')
        env = c.container_environment(cfg, self.proxy, self.module)
        self.assertNotIn('AGENT_CONTAINER_GITHUB_SECRET', env)
        self.assertEqual(env['GIT_CONFIG_COUNT'], '1')  # hooksPath only
        # This does not revoke access supplied independently by proxy metadata.
        self.assertEqual(env['GH_TOKEN'], c.REF + 'GITHUB_TOKEN')

    def test_empty_suggestion_can_be_skipped_with_enter(self):
        self.proxy['environment'] = {}
        with self.prompts():
            c.setup(self.path)
        self.assertEqual(json.loads(self.path.read_bytes())['githubSecret'], '')

    def test_1password_requires_explicit_account(self):
        with self.prompts(reference='op://Example/Workspace/password', account=''):
            with self.assertRaises(c.Error):
                c.setup(self.path)
        self.assertFalse(self.path.exists())
        self.runtime.build.assert_not_called()

    def test_reference_only_unlock_config_is_saved_without_vault_read(self):
        with self.prompts(reference='op://Example/Workspace/password', account='example.1password.com'), patch('cli.read_passphrase') as unlock:
            c.setup(self.path)
        unlock.assert_not_called()  # real Runtime owns unlock, after the build
        cfg = json.loads(self.path.read_bytes())
        self.assertEqual(cfg['unlock'], {'method': '1password', 'reference': 'op://Example/Workspace/password', 'account': 'example.1password.com'})

    def test_repeated_setup_refuses_before_proxy_or_runtime(self):
        self.path.parent.mkdir()
        c.save_new(self.path, b'original operator settings')
        with self.prompts() as (proxy_call, runtime_call):
            with self.assertRaisesRegex(c.Error, 'Already configured'):
                c.setup(self.path)
            proxy_call.assert_not_called()
            runtime_call.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b'original operator settings')

    def test_dangling_config_symlink_is_not_replaced(self):
        self.path.parent.mkdir()
        self.path.symlink_to(self.path.parent / 'missing')
        with self.prompts() as (proxy_call, _):
            with self.assertRaises(c.Error):
                c.setup(self.path)
            proxy_call.assert_not_called()
        self.assertTrue(self.path.is_symlink())

    def test_no_config_or_build_on_untrusted_proxy_environment(self):
        self.proxy['environment']['EXAMPLE_API_KEY'] = 'unsealed fixture value'
        with self.prompts() as (_, runtime_call):
            with self.assertRaises(c.Error):
                c.setup(self.path)
            runtime_call.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_missing_proxy_does_not_create_config(self):
        with self.prompts() as (proxy_call, runtime_call):
            proxy_call.side_effect = c.Error('Proxy unavailable')
            with self.assertRaisesRegex(c.Error, 'Proxy unavailable'):
                c.setup(self.path)
            runtime_call.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_noninteractive_setup_refuses_before_discovery(self):
        with self.prompts() as (proxy_call, _), patch('cli.sys.stdin.isatty', return_value=False):
            with self.assertRaisesRegex(c.Error, 'interactive terminal'):
                c.setup(self.path)
            proxy_call.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_failure_after_save_keeps_settings_for_explicit_build_init_retry(self):
        self.runtime.build.side_effect = c.Error('Fixture build failed')
        with self.prompts():
            with self.assertRaisesRegex(c.Error, 'Fixture build failed'):
                c.setup(self.path)
        c.validate_config(json.loads(c.private_read(self.path)))
        self.runtime.prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
