import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import WifeAgent as agent


class PublicConfigurationTests(unittest.TestCase):
    def test_custom_owner_and_bot_are_used_in_prompt(self):
        env = dict(os.environ, WIFE_NG_OWNER='TestOwner', WIFE_NG_BOT_NAME='TestBot')
        result = subprocess.run(
            [sys.executable, '-c', 'import WifeAgent as a; import json; print(json.dumps([a.OWNER,a.BOT_NAME,a.SYSTEM_PROMPT]))'],
            cwd=Path(agent.__file__).parent, env=env, capture_output=True, text=True,
            encoding='utf-8', check=True)
        owner, bot, prompt = json.loads(result.stdout)
        self.assertEqual((owner, bot), ('TestOwner', 'TestBot'))
        self.assertIn('TestOwner', prompt)
        self.assertIn('TestBot', prompt)
        self.assertNotIn('Wu_Dabin', prompt)

    def test_control_token_only_sent_to_game_bridge(self):
        with patch.dict(os.environ, {'WIFE_NG_TOKEN': 'test-control-token'}):
            with patch.object(agent.urllib.request, 'urlopen') as opening:
                opening.return_value.__enter__.return_value.read.return_value = b'{}'
                agent.http_json(agent.API_BASE + '/v1/state')
                self.assertEqual(opening.call_args.args[0].get_header('Authorization'), 'Bearer test-control-token')
                agent.http_json('https://example.invalid/v1/state')
                self.assertIsNone(opening.call_args.args[0].get_header('Authorization'))
                agent.http_json(agent.API_BASE + '.invalid/v1/state')
                self.assertIsNone(opening.call_args.args[0].get_header('Authorization'))

    def test_repository_local_key_takes_precedence_over_legacy_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'project'
            root.mkdir()
            (root / 'DEEPSEEK_API_KEY.ini').write_text('DEEPSEEK_API_KEY=local-example-key', encoding='utf-8')
            (root.parent / 'DEEPSEEK_API_KEY.ini').write_text('legacy-example-key', encoding='utf-8')
            with patch.object(agent, 'ROOT', root), patch.object(agent, 'MCC_ROOT', root.parent):
                with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
                    self.assertEqual(agent.read_api_key(), 'local-example-key')
