"""Inspect-only execution and portable review in a fresh project, without paid calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inspect_ai.model import ModelOutput, get_model
from tamesu.artifacts import write_yaml
from tamesu.backends import get_backend
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ConfigError, ExecutionError
from tamesu.identity import digest_file
from tamesu.packaging import pack_case, unpack_archive, verify_archive
from tamesu.planner import build_plan
from tamesu.presenting import present_case
from tamesu.review import export_pack, import_responses, review_status
from tamesu.runner import run_eval
from tamesu.scaffold import create
from test_inspect_backend import Mocked
from test_inspect_review import RUBRIC


class PortableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.eval_id = 'arithmetic/first/baseline'
        self.files = create(self.root, 'eval', self.eval_id)
        self.eval_dir = self.root/'cases/arithmetic/experiments/first/evals/baseline'
        self.case = self.root/'cases/arithmetic'
        self.route = Mocked(); self.route.__enter__()
        self.addCleanup(self.route.__exit__, None, None, None)

    def run(self, *args, **kwargs):
        # unittest's run method must remain untouched.
        return super().run(*args, **kwargs)

    def execute(self, answer='4'):
        import inspect_ai
        real = inspect_ai.eval
        def evaluate(*args, **kwargs):
            kwargs['model'] = get_model(kwargs['model'], custom_outputs=lambda *a: ModelOutput.from_content('mockllm', answer), memoize=False)
            return real(*args, **kwargs)
        with patch('inspect_ai.eval', evaluate):
            return run_eval(load_eval_context(self.root, self.eval_id))

    def review_config(self, multiple=False):
        document = load_yaml(self.eval_dir/'eval.yml')
        document['evaluation']['human_review'] = {'rubric':'rubric.yml'}
        document['evaluation']['acceptance'] = {'requires':['mechanical','human']}
        if multiple:
            document['execution']['artifacts']['required'].append('source')
            document['execution']['artifacts']['review'] = ['response','source']
            task = self.eval_dir/'task.py'
            task.write_text(task.read_text().replace('        stager(str(state.sample_id)).add(', '        stage = stager(str(state.sample_id))\n        stage.add(').replace('        return Score(', '        stage.add("source", "main.py", "print(4)", "text/x-python")\n        return Score('))
        write_yaml(self.eval_dir/'eval.yml', document)
        write_yaml(self.eval_dir/'rubric.yml', RUBRIC)
        (self.eval_dir/'looks-fine.md').write_text('Review the answer and any attached code.')

    def responses(self, pack):
        document = load_yaml(pack/'responses.yml'); document['reviewer']='andrew'
        for row in document['responses']:
            for dimension in row['dimensions'].values():
                dimension.update({'pass':True,'reason_code':None})
        write_yaml(pack/'responses.yml', document)
        return pack/'responses.yml'

    def test_starter_runs_and_roundtrips_without_publication_metadata(self):
        self.assertEqual(len(self.files),7)
        ids = self.execute(); self.assertEqual(len(ids),1)
        report = load_yaml(self.eval_dir/'runs'/ids[0]/'report.yml')
        self.assertEqual(report['metrics']['correctness/accuracy'],1.0)
        view = self.root/'view'; present_case(self.case,view)
        archive = pack_case(self.case,self.root/'packages',presentation_dir=view)
        verified=verify_archive(archive)
        self.assertTrue(verified.content_digest.startswith('sha256:'))
        dest=unpack_archive(archive,self.root/'other/cases')
        self.assertTrue((dest/'.untrusted-origin').exists())
        with patch('inspect_ai.eval',side_effect=AssertionError('Viewing cannot execute a task')):
            present_case(dest,self.root/'other-view')
        self.assertTrue((self.root/'other-view/index.html').is_file())
        self.assertFalse(list(dest.rglob('*.eval')))

    def test_wrong_answer_fails_correctness_even_when_text_is_valid(self):
        ids=self.execute('Please provide the question.')
        report=load_yaml(self.eval_dir/'runs'/ids[0]/'report.yml')
        self.assertEqual(report['metrics']['correctness/accuracy'],0.0)

    def test_legacy_draft_and_complete_labels_never_block_execution(self):
        for status in ('draft','complete'):
            document=load_yaml(self.eval_dir/'eval.yml');document['status']=status
            write_yaml(self.eval_dir/'eval.yml',document)
            if status=='complete':
                document['defaults']['parameters']['max_tokens']=512
                write_yaml(self.eval_dir/'eval.yml',document)
            self.assertEqual(len(self.execute()),1)

    def test_native_backend_is_unavailable(self):
        with self.assertRaises(ValueError):get_backend('native')

    def test_budget_blocks_before_execution(self):
        document=load_yaml(self.eval_dir/'eval.yml');document['defaults']['budget_usd']=0
        write_yaml(self.eval_dir/'eval.yml',document)
        with patch('inspect_ai.eval',side_effect=AssertionError('budget must stop the run')):
            with self.assertRaises(ExecutionError):self.execute()

    def test_review_in_an_unpacked_project_is_offline_and_updates_acceptance(self):
        self.review_config();self.execute()
        archive=pack_case(self.case,self.root/'packages')
        dest=unpack_archive(archive,self.root/'recipient/cases')
        context=load_eval_context(self.root/'recipient',self.eval_id)
        pack=self.root/'review'
        with patch('inspect_ai.eval',side_effect=AssertionError('Review cannot call a model')):
            exported=export_pack(context,pack,seed=42)
            self.assertEqual(exported.exported,1)
            item=load_yaml(pack/'pack.yml')['items'][0]
            self.assertEqual(item['artifacts'][0]['text'],'4')
            self.assertNotIn('model',json.dumps(item))
            imported=import_responses(context,self.responses(pack))
            self.assertEqual(len(imported.written),1)
            self.assertEqual(review_status(context)['awaiting_review'],0)

    def test_multi_artifact_review_binds_every_member(self):
        self.review_config(multiple=True);self.execute()
        context=load_eval_context(self.root,self.eval_id)
        pack=self.root/'review';export_pack(context,pack,seed=42)
        self.assertEqual(len(load_yaml(pack/'pack.yml')['items'][0]['artifacts']),2)
        response=self.responses(pack)
        source=next(self.eval_dir.glob('runs/*/items/*/artifacts/main.py'))
        source.write_text('print(5)')
        with self.assertRaisesRegex(ExecutionError,'changed since export'):
            import_responses(context,response)
        self.assertFalse(list(self.eval_dir.glob('runs/*/items/*/reviews/*.yml')))

    def test_inline_prompt_edits_invalidate_the_run(self):
        self.execute(); before=build_plan(load_eval_context(self.root,self.eval_id))
        document=load_yaml(self.eval_dir/'eval.yml')
        document['arms'][0]['prompts']['system']['text']='Explain the answer.'
        write_yaml(self.eval_dir/'eval.yml',document)
        after=build_plan(load_eval_context(self.root,self.eval_id))
        self.assertEqual(len(before.banked_run_ids),1)
        self.assertEqual(len(after.banked_run_ids),0)

    def test_page_keeps_old_evidence_without_banking_it_for_new_execution(self):
        self.execute();document=load_yaml(self.eval_dir/'eval.yml')
        document['defaults']['parameters']['max_tokens']=512
        write_yaml(self.eval_dir/'eval.yml',document)
        out=self.root/'view';present_case(self.case,out)
        data=json.loads((out/'data.json').read_text())
        evaluation=data['experiments'][0]['evals'][0]
        self.assertEqual(evaluation['coverage']['banked'],0)
        self.assertEqual(len(evaluation['evidence_runs']),1)
        self.assertFalse(evaluation['evidence_runs'][0]['compatible'])
