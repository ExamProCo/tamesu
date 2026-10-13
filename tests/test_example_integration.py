"""Structured-text example executed by Inspect, without a second runner."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inspect_ai.model import ModelOutput, get_model
from tamesu.artifacts import write_yaml
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ExecutionError
from tamesu.planner import build_plan
from tamesu.reporting import comparison_rows
from tamesu.run_report import rescore_run
from tamesu.runner import resume_run, run_eval
from test_inspect_backend import Mocked

EVAL_ID='support-ticket-triage/decision-rules/prompt-ablation'

class ExampleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'
        shutil.copytree(Path(__file__).parents[1]/'examples/support-ticket-classification', self.root, ignore=shutil.ignore_patterns('runs','logs','analysis.md','leaderboard.md','evaluation-report.md'))
        self.route=Mocked();self.route.__enter__();self.addCleanup(self.route.__exit__,None,None,None)
        self.context=load_eval_context(self.root,EVAL_ID)
        self.calls=0

    def execute(self, answer=None, **filters):
        import inspect_ai
        real=inspect_ai.eval
        def evaluate(*args, **kwargs):
            def output(messages,*rest):
                self.calls+=1
                expected=next((item['expected'] for item in self.context.dataset['items'] if item['input']['message'] in messages[-1].text), {})
                return ModelOutput.from_content('mockllm',answer if answer is not None else json.dumps(expected))
            kwargs['model']=get_model(kwargs['model'],custom_outputs=output,memoize=False)
            return real(*args,**kwargs)
        with patch('inspect_ai.eval',evaluate):
            return run_eval(self.context,**filters)

    def test_full_example_workflow(self):
        ids=self.execute();self.assertEqual(len(ids),4);self.assertEqual(self.calls,32)
        plan=build_plan(self.context);self.assertEqual(len(plan.banked_run_ids),4)
        self.assertTrue(all(row['primary_mean']==1 for row in comparison_rows(plan)))
        self.assertEqual(self.execute(),[])
        for run_id in ids:
            manifest=load_yaml(self.context.eval_dir/'runs'/run_id/'run.yml')
            self.assertEqual(manifest['execution']['backend'],'inspect')
            self.assertTrue(manifest['execution']['logs'])

    def test_valid_json_is_not_a_correct_answer(self):
        ids=self.execute(answer='{"answer":"Please provide the question"}',limit_items=1)
        result=load_yaml(self.context.eval_dir/'runs'/ids[0]/'items'/self.context.dataset['items'][0]['id']/'result.yml')
        self.assertTrue(result['mechanical_scores']['valid_json'])
        self.assertFalse(result['mechanical_scores']['exact_match'])

    def test_malformed_json_is_scored_as_invalid(self):
        ids=self.execute(answer='not JSON',limit_items=1)
        report=load_yaml(self.context.eval_dir/'runs'/ids[0]/'report.yml')
        self.assertEqual(report['metrics']['invalid_output_rate'],1.0)

    def test_probe_does_not_bank_full_dataset(self):
        self.execute(limit_items=1)
        self.assertEqual(len(build_plan(self.context).banked_run_ids),0)

    def test_changed_prompt_invalidates_current_runs(self):
        self.execute();path=self.context.experiment_dir/'prompts/basic-system.md'
        path.write_text(path.read_text()+'\nAnswer carefully.\n')
        plan=build_plan(load_eval_context(self.root,EVAL_ID))
        self.assertEqual(len(plan.banked_run_ids),2)

    def test_rescore_uses_stored_text_without_model_calls(self):
        ids=self.execute(limit_items=1)
        with patch('inspect_ai.eval',side_effect=AssertionError('rescore must be offline')):
            rescore_run(self.context,self.context.eval_dir/'runs'/ids[0])

    def test_completed_run_cannot_resume(self):
        ids=self.execute()
        with self.assertRaisesRegex(ExecutionError,'already complete'):
            resume_run(self.context,ids[0])

    def test_missing_credentials_stop_execution(self):
        with patch.dict('os.environ',{'META_API_KEY':''}):
            with self.assertRaisesRegex(ExecutionError,'META_API_KEY'):
                run_eval(self.context)

    def test_budget_blocks_before_model_calls(self):
        path=self.context.eval_dir/'eval.yml';doc=load_yaml(path)
        doc['defaults']['budget_usd']=0;write_yaml(path,doc)
        self.context=load_eval_context(self.root,EVAL_ID)
        with self.assertRaises(ExecutionError):self.execute()
        self.assertEqual(self.calls,0)
