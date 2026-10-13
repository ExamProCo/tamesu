import json
import re
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tamesu.artifacts import write_yaml
from tamesu.config import load_yaml
from tamesu.live import live_state, serve_live
from tamesu.presenting import present_case
import test_portable_workflow as portable


class LiveReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = portable.PortableWorkflowTests('test_starter_runs_and_roundtrips_without_publication_metadata')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.review_config(multiple=True)
        self.fixture.execute()
        view = present_case(self.fixture.case, self.fixture.root/'view')
        self.server = serve_live(view, 0, self.fixture.case)
        self.addCleanup(self.close_server)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        html = self.get('/live').decode()
        self.token = json.loads(re.search(r'const TOKEN = ("[^"]+")', html)[1])

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.review_temporary.cleanup()

    def get(self, path):
        with urlopen(self.url+path) as response:
            return response.read()

    def post(self, path, value, token=None, origin=None):
        headers = {'Content-Type':'application/json', 'X-Tamesu-Token': token or self.token}
        if origin:
            headers['Origin'] = origin
        request = Request(self.url+path, data=json.dumps(value).encode(), headers=headers, method='POST')
        with urlopen(request) as response:
            return json.load(response)

    def export(self):
        response = self.post('/api/review/export', {'eval_id':self.fixture.eval_id})
        session = response['url'].split('/')[2]
        pack = Path(self.server.review_temporary.name)/session
        document = load_yaml(pack/'responses.yml')
        document['reviewer']='andrew'
        for row in document['responses']:
            for dimension in row['dimensions'].values():
                dimension.update({'pass':True,'reason_code':None})
        return session, document, response['url']

    def test_direct_review_saves_and_can_be_updated(self):
        session, document, url = self.export()
        html = self.get(url).decode()
        self.assertIn('Save review', html)
        self.assertIn('/api/review/save', html)
        self.assertNotIn('URL.createObjectURL', html)
        result = self.post('/api/review/save', {'session':session,'responses':document})
        self.assertEqual(result['written'],1)
        state = json.loads(self.get('/api/state'))
        self.assertEqual(state['evaluations'][0]['review']['awaiting_review'],0)
        session, document, _ = self.export()
        dimension = next(iter(document['responses'][0]['dimensions'].values()))
        dimension.update({'pass':False,'reason_code':'off-brief'})
        # Pick a valid non-other failure reason from the actual fixture rubric.
        rubric = load_yaml(self.fixture.eval_dir/'rubric.yml')
        dimension['reason_code'] = next(c for c in rubric['dimensions'][0]['reason_codes'] if c != 'other')
        self.post('/api/review/save', {'session':session,'responses':document})
        self.assertEqual(live_state(self.fixture.case)['evaluations'][0]['review']['items'][0]['state'],'fail')

    def test_rejects_foreign_writes_and_unknown_experiments(self):
        for options in ({'token':'wrong'}, {'origin':'https://example.com'}):
            with self.assertRaises(HTTPError) as raised:
                self.post('/api/review/export', {'eval_id':self.fixture.eval_id}, **options)
            self.assertEqual(raised.exception.code,403)
        with self.assertRaises(HTTPError) as raised:
            self.post('/api/review/export', {'eval_id':'../../elsewhere'})
        self.assertEqual(raised.exception.code,400)

    def test_rejects_stale_review_member(self):
        session, document, _ = self.export()
        subject = next(self.fixture.eval_dir.glob('runs/*/items/*/artifacts/main.py'))
        subject.write_text('changed')
        with self.assertRaises(HTTPError) as raised:
            self.post('/api/review/save', {'session':session,'responses':document})
        self.assertEqual(raised.exception.code,400)

    def test_running_progress_and_precise_cost_are_read_from_items(self):
        manifest_path = next(self.fixture.eval_dir.glob('runs/*/run.yml'))
        manifest = load_yaml(manifest_path)
        manifest['state']='running'
        manifest['selection']['item_ids'].append('not-finished')
        write_yaml(manifest_path,manifest)
        result_path = next(self.fixture.eval_dir.glob('runs/*/items/*/result.yml'))
        result = load_yaml(result_path)
        result['generation']['cost_usd']=0.000123
        write_yaml(result_path,result)
        run = json.loads(self.get('/api/state'))['evaluations'][0]['runs'][0]
        self.assertEqual((run['state'],run['completed'],run['selected']),('running',1,2))
        self.assertEqual(run['cost_usd'],0.000123)
