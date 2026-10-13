from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tamesu.artifacts import write_yaml
from tamesu.cli import main
from tamesu.config import load_eval_context
from tamesu.documentation import read_document, save_document
from tamesu.errors import ConfigError, ExecutionError
from tamesu.planner import build_plan
from tamesu.reporting import comparison_rows
from tamesu.scaffold import create
from tamesu.selection import RunOptions, selected_plan


class WalkthroughTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create(self.root, 'eval', 'base-calc/add/int')
        self.case = self.root / 'cases/base-calc'
        self.eval = self.case / 'experiments/add/evals/int/eval.yml'

    def invoke(self, args):
        with self.assertRaises(SystemExit) as exit:
            main(args)
        self.assertEqual(exit.exception.code, 0)

    def context(self):
        return load_eval_context(self.root, 'base-calc/add/int')

    def test_authored_case_and_external_dataset(self):
        write_yaml(self.case / 'case.yml', dict(name='Orderwisp - Basic Calculation Accuracy', description='Does it add?', business_use='Support agents', current_problem='Missing evidence'))
        data = yaml.safe_load(self.eval.read_text())
        dataset = data['dataset']
        dataset['items'] = [dict(id=f'calculation-{i:03}', input=dict(question=f'{i} + 1'), expected=str(i+1)) for i in range(100)]
        dataset_path = self.case / 'datasets/integer-addition-v1/dataset.yml'
        write_yaml(dataset_path, dataset)
        data['dataset'] = '{{ root.datasets }}/integer-addition-v1/dataset.yml'
        data['defaults']['repetitions'] = 30
        data['arms'] = [dict(data['arms'][0], id=arm) for arm in ('minimal', 'low', 'medium', 'high')]
        data['runs'][0]['arms'] = ['minimal', 'low', 'medium', 'high']
        write_yaml(self.eval, data)
        context = self.context()
        self.assertEqual(context.dataset_path, dataset_path)
        self.assertEqual(sum(len(spec.item_ids) for spec in build_plan(context).specs), 12000)
        options = RunOptions(limit_items=10, sample='random', seed=42, repetitions=2)
        first = selected_plan(context, options)
        self.assertEqual(sum(len(spec.item_ids) for spec in first.specs), 80)
        self.assertTrue(all(spec.probe for spec in first.specs))
        self.assertEqual(first.specs, selected_plan(context, options).specs)
        capped = selected_plan(context, RunOptions(max_executions=13))
        self.assertEqual(sum(len(spec.item_ids) for spec in capped.specs), 13)

    def test_commands_and_dry_run(self):
        with patch('pathlib.Path.cwd', return_value=self.root), contextlib.redirect_stdout(io.StringIO()) as output:
            self.invoke(['case', 'new', 'other'])
            self.invoke(['exp', 'new', 'other/sub'])
            self.invoke(['eval', 'new', 'other/sub/multi_digit'])
            self.invoke(['run', 'base-calc/add/int', '--arm', 'baseline', '--limit-items', '1', '--repetitions', '1', '--dry-run'])
        self.assertIn('Probe: True', output.getvalue())
        self.assertFalse((self.eval.parent / 'runs').exists())
        self.assertTrue((self.root / 'cases/other/experiments/sub/experiment.yml').is_file())

    def test_unknown_and_conflicting_selection(self):
        for options in (RunOptions(items=('absent',)), RunOptions(arms=('absent',)), RunOptions(limit_items=1, items=('example-1',)), RunOptions(sample='random', limit_items=1), RunOptions(budget_usd=float('nan'))):
            with self.assertRaises(ExecutionError):
                selected_plan(self.context(), options)

    def test_document_save_preserves_other_fields_and_detects_conflict(self):
        self.eval.write_text('# preserve me\n' + self.eval.read_text())
        ref = self.eval.relative_to(self.root).as_posix()
        document = read_document(self.root, ref)
        saved = save_document(self.root, ref, document['revision'], {'question': 'Does exact addition work?'})
        self.assertEqual(saved['fields']['question'], 'Does exact addition work?')
        self.assertTrue(self.eval.read_text().startswith('# preserve me'))
        self.assertEqual(self.context().evaluation['defaults']['repetitions'], 1)
        with self.assertRaisesRegex(ConfigError, 'changed on disk'):
            save_document(self.root, ref, document['revision'], {'question': 'Stale edit'})
        with self.assertRaisesRegex(ConfigError, 'Only authored'):
            save_document(self.root, ref, saved['revision'], {'runs': []})


class InspectStatisticsTests(unittest.TestCase):
    def test_custom_metric_uses_pooled_samples(self):
        from test_inspect_backend import Fixture, Mocked
        from tamesu.runner import run_eval
        fixture = Fixture(self)
        config = yaml.safe_load((fixture.eval_dir / 'eval.yml').read_text())
        config['defaults']['repetitions'] = 2
        config['evaluation'] = {'scorers': ['check']}
        config['metrics'] = {'primary': 'check/all_exact', 'secondary': ['check/count_samples']}
        write_yaml(fixture.eval_dir / 'eval.yml', config)
        task = fixture.exp / 'inspect/task.py'
        source = task.read_text().replace('Score, accuracy, scorer', 'Score, accuracy, scorer, metric, SampleScore')
        source = source.replace('@scorer(metrics=[accuracy()])', '''@metric
def count_samples():
    def metric(scores: list[SampleScore]):
        return len(scores)
    return metric


@metric
def all_exact():
    def metric(scores: list[SampleScore]):
        return float(all(sample.score.value == 1.0 for sample in scores))
    return metric


@scorer(metrics=[count_samples(), all_exact()])''')
        source = source.replace("return Score(value=1.0,", "return Score(value=0.0 if '--rep2--' in os.environ['TAMESU_STAGING_DIR'] else 1.0,")
        task.write_text(source)
        with Mocked():
            run_ids = run_eval(fixture.context())
        self.assertEqual(len(run_ids), 2)
        with Mocked():
            rows = comparison_rows(build_plan(fixture.context()))
        # Inspect reduces repeated IDs first: three unique samples, not six executions.
        self.assertEqual(rows[0]['metrics']['check/count_samples'], 3)
        # Averaging per-run all_exact (1 and 0) would incorrectly yield 0.5.
        self.assertEqual(rows[0]['primary_mean'], 0)
        self.assertEqual(rows[0]['completed_items'], 6)
        with Mocked():
            probes = run_eval(fixture.context(), options=RunOptions(limit_items=1, sample='random', seed=42, repetitions=1))
        self.assertEqual(len(probes), 1)
        with Mocked():
            self.assertEqual(comparison_rows(build_plan(fixture.context()))[0]['completed_items'], 6)


class BrowserAndPortabilityTests(unittest.TestCase):
    setUp = WalkthroughTests.setUp
    def test_external_dataset_and_human_case_roundtrip(self):
        from tamesu.packaging import pack_case, unpack_archive, verify_archive
        config = yaml.safe_load(self.eval.read_text())
        write_yaml(self.case / 'datasets/numbers/dataset.yml', config['dataset'])
        config['dataset'] = '{{ root.datasets }}/numbers/dataset.yml'
        write_yaml(self.eval, config)
        write_yaml(self.case / 'case.yml', dict(name='Orderwisp basic calculation accuracy', description='Addition', business_use='Support', current_problem='Missing evidence'))
        archive = pack_case(self.case, self.root / 'archives')
        verify_archive(archive)
        destination = self.root / 'copy/cases'
        unpack_archive(archive, destination)
        self.assertEqual(load_eval_context(destination.parent, 'base-calc/add/int').dataset['name'], 'base-calc-v1')

    def test_project_browser_and_document_edit(self):
        import json
        import re
        from urllib.parse import quote
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from tamesu.live import serve_project
        create(self.root, 'case', 'other')
        server = serve_project(self.root, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}'
        with urlopen(url) as response:
            index = response.read().decode()
        self.assertIn('/cases/base-calc', index)
        self.assertIn('/cases/other', index)
        with urlopen(url + '/cases/base-calc') as response:
            case_url = response.url.rstrip('/')
            self.assertIn('Experiment overview', response.read().decode())
        child = server.case_servers['base-calc']
        def close_child():
            for stop, watcher in server.case_watchers:
                stop.set()
                watcher.join(timeout=2)
            child.shutdown()
            child.server_close()
            child.review_temporary.cleanup()
        self.addCleanup(close_child)
        with urlopen(case_url + '/edit') as response:
            page = response.read().decode()
        token = json.loads(re.search(r'const TOKEN=("[^"]+")', page)[1])
        ref = 'cases/base-calc/experiments/add/experiment.yml'
        with urlopen(case_url + '/api/document?path=' + quote(ref)) as response:
            document = json.load(response)
        body = dict(path=ref, revision=document['revision'], fields={'hypothesis': 'Every addition is exact.'})
        def save(token=token):
            request = Request(case_url + '/api/document/save', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'X-Tamesu-Token': token}, method='POST')
            return urlopen(request)
        with save() as response:
            self.assertEqual(json.load(response)['fields']['hypothesis'], body['fields']['hypothesis'])
        with self.assertRaises(HTTPError):
            save()
        with self.assertRaises(HTTPError) as forbidden:
            save('wrong')
        self.assertEqual(forbidden.exception.code, 403)

    def test_completion_finds_project_references(self):
        from tamesu.completion import candidates
        self.assertEqual(candidates(self.root, ['run', 'base']), ['base-calc/add/int'])
        self.assertEqual(candidates(self.root, ['eval', 'new', 'base']), ['base-calc/add/'])
        self.assertEqual(candidates(self.root, ['run', 'base-calc/add/int', '--arm', 'bas']), ['baseline'])


class ProbeResumeTests(unittest.TestCase):
    def test_random_probe_with_extra_repetitions_resumes_saved_selection(self):
        from test_inspect_backend import Fixture, Mocked
        from tamesu.runner import run_eval, resume_run
        fixture = Fixture(self)
        options = RunOptions(limit_items=2, sample='random', seed=42, repetitions=2, concurrency=1)
        with Mocked():
            run_ids = run_eval(fixture.context(), options=options)
            run = fixture.eval_dir / 'runs' / run_ids[-1]
            saved = yaml.safe_load((run / 'run.yml').read_text())
            selected = saved['selection']['item_ids']
            self.assertEqual(saved['selection']['options']['seed'], 42)
            self.assertEqual(resume_run(fixture.context(), run_ids[-1]), run_ids[-1])
        after = yaml.safe_load((run / 'run.yml').read_text())
        self.assertEqual(after['selection']['item_ids'], selected)
        self.assertEqual(after['resolved']['concurrency'], 1)
