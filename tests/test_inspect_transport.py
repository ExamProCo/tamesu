import unittest
from unittest.mock import patch

from inspect_ai.model import ModelOutput, get_model
from tamesu.inspect_provider import InspectProvider
from tamesu.models import ImagePart, TextPart
from test_inspect_backend import Mocked


class InspectTransportTests(unittest.TestCase):
    def setUp(self):
        self.route=Mocked();self.route.__enter__();self.addCleanup(self.route.__exit__,None,None,None)

    def test_text_uses_inspect_and_preserves_usage(self):
        model=get_model('mockllm/transport', custom_outputs=[ModelOutput.from_content('mockllm','{"answer":"4"}')],memoize=False)
        with patch('inspect_ai.model.get_model',return_value=model) as resolve:
            response=InspectProvider('meta').generate_text(model='muse-spark-1.2',system_prompt='Answer as JSON.',user_prompt='2+2?',output_schema={'type':'object'},parameters={'max_tokens':64},timeout_seconds=30)
        self.assertEqual(response.text,'{"answer":"4"}')
        self.assertGreater(response.usage['total_tokens'],0)
        self.assertEqual(resolve.call_args.args[0],'mockllm/muse-spark-1.2')

    def test_image_judging_uses_inspect_content_parts(self):
        seen=[]
        def generate(messages,*args):
            seen.extend(messages)
            return ModelOutput.from_content('mockllm','{"pass":true}')
        model=get_model('mockllm/transport',custom_outputs=generate,memoize=False)
        with patch('inspect_ai.model.get_model',return_value=model):
            response=InspectProvider('meta').generate_structured(model='muse-spark-1.2',system_prompt='Review the image.',parts=(TextPart('Check it.'),ImagePart(b'fake-image','image/png')),output_schema={'type':'object'},parameters={},timeout_seconds=30)
        self.assertEqual(response.text,'{"pass":true}')
        self.assertTrue(seen[-1].content[1].image.startswith('data:image/png;base64,'))
