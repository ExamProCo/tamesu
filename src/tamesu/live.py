"""Local live overview and direct review, backed by the existing evidence contracts."""
from __future__ import annotations

import json
import secrets
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .artifacts import write_json
from .config import load_eval_context, load_yaml
from .errors import TamesuError
from .review import export_pack, import_responses, review_status


def _answer(item_dir: Path, result: dict) -> str:
    subjects = result.get('review_subjects') or []
    output = next((s for s in subjects if s.get('role') == 'response'), None) or (subjects[0] if subjects else result.get('output') or {})
    path = (item_dir / output.get('path', '')).resolve()
    if not path.is_relative_to(item_dir.resolve()) or not path.is_file():
        return 'No answer artifact'
    media = output.get('media_type', '')
    if media.startswith('text/') or path.suffix in ('.txt', '.json', '.md', '.py'):
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            return stream.read(1000)
    return 'Artifact: ' + path.name


def live_state(case_dir: Path) -> dict:
    """Read evidence without loading task code or making provider calls."""
    evaluations = []
    errors = []
    for path in sorted(case_dir.glob('experiments/*/evals/*/eval.yml')):
        eval_id = f'{case_dir.name}/{path.parents[2].name}/{path.parent.name}'
        runs = []
        for manifest_path in sorted(path.parent.glob('runs/*/run.yml')):
            try:
                manifest = load_yaml(manifest_path)
                items = []
                for result_path in sorted(manifest_path.parent.glob('items/*/result.yml')):
                    result = load_yaml(result_path)
                    items.append({
                        'id': result_path.parent.name,
                        'state': result.get('state'),
                        'scores': result.get('scores') or {name: value.get('value') for name, value in result.get('mechanical_scores', {}).items()},
                        'answer': _answer(result_path.parent, result),
                        'cost_usd': result.get('generation', {}).get('cost_usd'),
                        'error': result.get('error', {}).get('message'),
                    })
                known = [item['cost_usd'] for item in items if isinstance(item['cost_usd'], (int, float))]
                runs.append({
                    'id': manifest.get('run_id', manifest_path.parent.name),
                    'state': manifest.get('state'),
                    'probe': bool(manifest.get('probe')),
                    'model': manifest.get('resolved', {}).get('model'),
                    'arm': manifest.get('resolved', {}).get('arm'),
                    'selected': len(manifest.get('selection', {}).get('item_ids', [])),
                    'completed': sum(item['state'] == 'complete' for item in items),
                    'failed': sum(item['state'] == 'failed' for item in items),
                    'cost_usd': sum(known),
                    'unknown_cost_items': len(items) - len(known),
                    'items': items,
                })
            except (TamesuError, OSError, ValueError, TypeError) as exc:
                errors.append(f'{eval_id}: {exc}')
        try:
            context = load_eval_context(case_dir.parents[1], eval_id)
            review = review_status(context) if context.evaluation.get('evaluation', {}).get('human_review') else None
            question = context.evaluation.get('question', eval_id)
        except (TamesuError, OSError, ValueError, TypeError) as exc:
            errors.append(f'{eval_id}: {exc}')
            review, question = None, eval_id
        evaluations.append({'id': eval_id, 'question': question, 'runs': runs, 'review': review})
    documentation = {}
    experiments = []
    try:
        documentation = load_yaml(case_dir / 'case.yml')
    except TamesuError as exc:
        errors.append(str(exc))
    for path in sorted(case_dir.glob('experiments/*/experiment.yml')):
        try:
            experiments.append(dict(load_yaml(path), id=path.parent.name))
        except TamesuError as exc:
            errors.append(str(exc))
    return {'schema_version': 1, 'case': case_dir.name, 'documentation': documentation, 'experiments': experiments, 'evaluations': evaluations, 'errors': errors}


PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tamesu live overview</title>
<style>body{font:16px system-ui;max-width:1150px;margin:2rem auto;padding:0 1rem;color:#222}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{padding:.5rem;font:inherit}article{border:1px solid #ddd;padding:1rem;margin:1rem 0}a{color:#246}#status{color:#555}.error{color:#a21}</style>
<h1>Experiment overview</h1><p><a href="/index.html">Full results and narrative</a> · <a href="/api/state">Live JSON</a> · <a href="/data.json">Presentation JSON</a></p>
<p id="status" role="status">Connecting…</p>
<p>Completed items have finished generating; acceptance depends on their scores and any required review. Costs are recorded as items finish, not streamed from the billing provider.</p>
<p><a href="/edit">Edit documentation</a></p><main id="main"></main><pre id="errors" class="error"></pre>
<script>
const TOKEN = __TOKEN__;
const main = document.getElementById('main');
function el(tag, text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
async function review(id){
  try {
    const response=await fetch('/api/review/export',{method:'POST',headers:{'Content-Type':'application/json','X-Tamesu-Token':TOKEN},body:JSON.stringify({eval_id:id})});
    const data=await response.json();if(!response.ok)throw Error(data.error);
    location.href=data.url;
  } catch(error){document.getElementById('errors').textContent=error.message;}
}
let last='';
async function refresh(){
  try {
    const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('Could not read live state');
    const data=await response.json(), serialized=JSON.stringify(data);
    if(serialized!==last){
      last=serialized; main.replaceChildren();
      const documentation=data.documentation||{};
      main.append(el('h2',documentation.title||documentation.name||data.case));
      for(const key of ['description','business_use','current_problem','technical_uncertainty'])if(documentation[key])main.append(el('h3',key.replaceAll('_',' ')),el('p',documentation[key]));
      for(const experiment of data.experiments||[]){
        const section=el('section');section.append(el('h2',experiment.name||experiment.id),el('p',experiment.description),el('h3','Hypothesis'),el('p',experiment.hypothesis),el('h3','Success criteria'));
        const criteria=el('ul');for(const value of experiment.success_criteria||[])criteria.append(el('li',value));section.append(criteria);main.append(section);
      }
      for(const evaluation of data.evaluations){
        const article=el('article');article.append(el('h2',evaluation.question),el('p',evaluation.id));
        if(evaluation.review){
          article.append(el('p',`${evaluation.review.reviewed} reviewed · ${evaluation.review.awaiting_review} awaiting review`));
          const button=el('button','Review / update decisions');button.onclick=()=>review(evaluation.id);article.append(button);
        }
        if(!evaluation.runs.length)article.append(el('p','Ready to run from your terminal. No stored results yet.'));
        const table=el('table'),head=el('tr');for(const h of ['Model / arm','Run status','Items completed','Recorded cost','Evidence'])head.append(el('th',h));table.append(head);
        for(const run of evaluation.runs){
          const row=el('tr');row.append(el('td',`${run.model} / ${run.arm}${run.probe?' · probe (excluded from comparisons)':''}`),el('td',run.state),el('td',`${run.completed} / ${run.selected} · ${run.failed} failed`),el('td',`$${run.cost_usd.toFixed(6)}${run.unknown_cost_items?' + unknown':''}`));
          const cell=el('td'),details=el('details');details.append(el('summary',`${run.id} — answers and checks`));
          for(const item of run.items)details.append(el('h4',`${item.id} · ${item.state}`),el('pre',JSON.stringify(item.scores,null,2)),el('p',item.error||''));
          cell.append(details);row.append(cell);table.append(row);
        }
        article.append(table);
        const answers=el('table'), ah=el('tr');for(const h of ['Item / model / arm','Answer','Automated checks','Human review'])ah.append(el('th',h));answers.append(ah);
        for(const run of evaluation.runs)for(const item of run.items){
          const human=evaluation.review?.items.find(r=>r.run_id===run.id&&r.item_id===item.id);
          const row=el('tr');row.append(el('td',`${item.id} / ${run.model} / ${run.arm}`),el('td',item.answer),el('td',JSON.stringify(item.scores)),el('td',human?.state||'Not required'));answers.append(row);
        }
        if(evaluation.runs.length)article.append(answers);main.append(article);
      }
      document.getElementById('errors').textContent=data.errors.join('\n');
    }
    document.getElementById('status').textContent='Live · updated '+new Date().toLocaleTimeString()+' · refreshes every second';
  } catch(error){document.getElementById('status').textContent='Updates paused: '+error.message;}
  setTimeout(refresh,1000);
}
refresh();
</script>'''


def serve_live(directory: Path, port: int, case_dir: Path) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)
    packs: dict[str, tuple[object, Path]] = {}
    temporary = tempfile.TemporaryDirectory(prefix='tamesu-web-review-')
    lock = threading.Lock()
    root = case_dir.parents[1]

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, value, media='application/json'):
            body = (json.dumps(value) if media == 'application/json' else value).encode()
            self.send_response(status)
            self.send_header('Content-Type', media + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') in {
                f'127.0.0.1:{self.server.server_address[1]}',
                f'localhost:{self.server.server_address[1]}',
            }

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, {'error': 'Use the localhost address printed by Tamesu.'})
                return
            route = urlsplit(self.path).path
            if route == '/api/document':
                from .documentation import read_document
                try:
                    ref = parse_qs(urlsplit(self.path).query).get('path', [''])[0]
                    self.reply(200, read_document(root, ref))
                except TamesuError as exc:
                    self.reply(400, {'error': str(exc)})
                return
            if route == '/edit':
                paths = [path.relative_to(root).as_posix() for pattern in ('case.yml', 'experiments/*/experiment.yml', 'experiments/*/evals/*/eval.yml') for path in case_dir.glob(pattern)]
                self.reply(200, editor_page(paths, token), 'text/html')
                return
            if route == '/api/state':
                self.reply(200, live_state(case_dir))
                return
            if route in ('/', '/live'):
                self.reply(200, PAGE.replace('__TOKEN__', json.dumps(token)), 'text/html')
                return
            if route.startswith('/review/'):
                parts = unquote(route).split('/')
                if len(parts) < 4 or parts[2] not in packs:
                    self.reply(404, {'error': 'Unknown review session'})
                    return
                _, pack = packs[parts[2]]
                path = (pack / '/'.join(parts[3:])).resolve()
                if not path.is_relative_to(pack.resolve()) or not path.is_file():
                    self.reply(404, {'error': 'Unknown artifact'})
                    return
                if path.name == 'review.html':
                    html = path.read_text().replace('Download responses.yml', 'Save review')
                    html = html.replace('Save the file next to the pack and run <code>tamesu review import</code>.', 'Save your decisions directly to this experiment. <a href="/live">Back to overview</a>.')
                    html = html.replace('addEventListener("click", () => {', 'addEventListener("click", async () => {')
                    start = html.index('  // JSON is valid YAML')
                    end = html.index('\n});', start)
                    html = html[:start] + '''  try {
    const response = await fetch('/api/review/save', {method:'POST',headers:{'Content-Type':'application/json','X-Tamesu-Token':__TOKEN__},body:JSON.stringify({session:__SESSION__,responses:doc})});
    const result = await response.json(); if(!response.ok)throw Error(result.error);
    document.getElementById('status').textContent = 'Saved '+result.written+' review(s); '+result.skipped_blank+' untouched item(s) still pending. Return to overview to see updated acceptance.';
  } catch(error){document.getElementById('status').textContent=error.message;}
''' .replace('__TOKEN__', json.dumps(token)).replace('__SESSION__', json.dumps(parts[2])) + html[end:]
                    self.reply(200, html, 'text/html')
                else:
                    self.path = '/' + path.relative_to(pack).as_posix()
                    original = self.directory
                    self.directory = str(pack)
                    try:
                        super().do_GET()
                    finally:
                        self.directory = original
                return
            super().do_GET()

        def do_POST(self):
            # A session token prevents unrelated web pages from writing local evidence.
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if not self.valid_host() or self.headers.get('X-Tamesu-Token') != token or (origin and origin != 'http://' + host):
                self.reply(403, {'error': 'Open this review from the local Tamesu overview.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                with lock:
                    if self.path == '/api/document/save':
                        from .documentation import save_document
                        self.reply(200, save_document(root, body.get('path'), body.get('revision'), body.get('fields')))
                    elif self.path == '/api/review/export':
                        eval_id = body.get('eval_id')
                        allowed = {e['id'] for e in live_state(case_dir)['evaluations']}
                        if not isinstance(eval_id, str) or eval_id not in allowed:
                            raise ValueError('Unknown experiment')
                        context = load_eval_context(root, eval_id)
                        session = secrets.token_urlsafe(16)
                        pack = Path(temporary.name) / session
                        export_pack(context, pack, include_reviewed=True)
                        packs[session] = (context, pack)
                        self.reply(200, {'url': f'/review/{session}/review.html'})
                    elif self.path == '/api/review/save':
                        session = body.get('session')
                        if not isinstance(session, str) or session not in packs:
                            raise ValueError('Unknown review session')
                        context, pack = packs[session]
                        # Reload current config so changed acceptance/rubrics are checked.
                        context = load_eval_context(root, context.eval_id)
                        write_json(pack / 'submitted.json', body.get('responses'))
                        result = import_responses(context, pack / 'submitted.json')
                        self.reply(200, {'written': len(result.written), 'reviewer': result.reviewer, 'skipped_blank': result.skipped_blank})
                    else:
                        self.reply(404, {'error': 'Unknown endpoint'})
            except (TamesuError, OSError, ValueError, TypeError, KeyError) as exc:
                self.reply(400, {'error': str(exc)})

    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), partial(Handler, directory=str(directory)))
    except OSError as exc:
        temporary.cleanup()
        raise TamesuError(f'Cannot serve on port {port}; choose another --port.') from exc
    server.review_temporary = temporary
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def editor_page(paths: list[str], token: str) -> str:
    import html
    options = ''.join(f'<option>{html.escape(path)}</option>' for path in paths)
    return '''<!doctype html><meta charset="utf-8"><title>Edit documentation</title>
<style>body{font:16px system-ui;max-width:900px;margin:2rem auto}label{display:block;margin:1rem 0}textarea{display:block;width:100%;min-height:5rem;font:inherit}button,select{font:inherit;padding:.5rem}</style>
<a href="/">← Cases and results</a><h1>Edit documentation</h1>
<select id="documents">''' + options + '''</select><button id="reload">Reload</button>
<form id="editor"></form><button id="save">Save</button><p id="status" role="status"></p>
<script>
const TOKEN=''' + json.dumps(token) + ''';
let current;const editor=document.getElementById('editor'),status=document.getElementById('status');
async function load(){try{const r=await fetch('/api/document?path='+encodeURIComponent(document.getElementById('documents').value));current=await r.json();if(!r.ok)throw Error(current.error);editor.replaceChildren();for(const [key,value] of Object.entries(current.fields)){const label=document.createElement('label');label.textContent=key;const input=document.createElement('textarea');input.name=key;input.value=Array.isArray(value)?value.join('\\n'):value;label.append(input);editor.append(label);}status.textContent='Loaded';}catch(e){status.textContent=e.message;}}
document.getElementById('reload').onclick=load;document.getElementById('documents').onchange=load;
document.getElementById('save').onclick=async()=>{try{const fields={};for(const input of editor.querySelectorAll('textarea'))fields[input.name]=input.name==='success_criteria'?input.value.split('\\n').filter(x=>x.trim()):input.value;const r=await fetch('/api/document/save',{method:'POST',headers:{'Content-Type':'application/json','X-Tamesu-Token':TOKEN},body:JSON.stringify({path:current.path,revision:current.revision,fields})});const data=await r.json();if(!r.ok)throw Error(data.error);current=data;status.textContent='Saved';}catch(e){status.textContent=e.message;}};
load();</script>'''


def serve_project(root: Path, port: int) -> ThreadingHTTPServer:
    """Project index, with each case retaining the existing live/review server."""
    import html
    children = {}
    watchers = []
    lock = threading.Lock()

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self.headers.get('Host') not in {f'localhost:{self.server.server_address[1]}', f'127.0.0.1:{self.server.server_address[1]}'}:
                self.send_error(403)
                return
            route = urlsplit(self.path).path
            if route.startswith('/cases/'):
                name = unquote(route[len('/cases/'):]).strip('/')
                cases = {path.parent.name: path.parent for path in root.glob('cases/*/case.yml')}
                if name not in cases:
                    self.send_error(404)
                    return
                with lock:
                    if name not in children:
                        from .presenting import present_case
                        from .present_watch import watch
                        case = cases[name]
                        server = serve_live(present_case(case), 0, case)
                        stop = threading.Event()
                        def render(case=case):
                            return present_case(case)
                        watcher = threading.Thread(target=watch, args=(case, render), kwargs={"stop": stop}, daemon=True)
                        watcher.start()
                        watchers.append((stop, watcher))
                        children[name] = server
                    server = children[name]
                self.send_response(302)
                self.send_header('Location', f'http://localhost:{server.server_address[1]}/')
                self.end_headers()
                return
            if route != '/':
                self.send_error(404)
                return
            links = []
            for path in sorted(root.glob('cases/*/case.yml')):
                try:
                    case = load_yaml(path)
                    label = case.get('title') or case.get('name') or path.parent.name
                    description = case.get('description', '')
                except TamesuError:
                    label, description = path.parent.name, 'Invalid case document'
                links.append(f'<article><h2><a href="/cases/{html.escape(path.parent.name, quote=True)}">{html.escape(str(label))}</a></h2><p>{html.escape(str(description))}</p></article>')
            body = ('<!doctype html><meta charset="utf-8"><title>Tamesu cases</title><style>body{font:16px system-ui;max-width:1000px;margin:2rem auto}</style><h1>Cases</h1>' + ''.join(links) + '<script>setTimeout(()=>location.reload(),5000)</script>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.case_servers = children
    server.case_watchers = watchers
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
