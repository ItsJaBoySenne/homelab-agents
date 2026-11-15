"""Attachments (HLA_ATTACHMENTS_V1): types, extraction, masking, model input, agent tools, upload/download API.

python3 tests/test_attachments.py
  Part 1 needs nothing. Part 2 (API, runs, tools) needs a throwaway Postgres like the other backend suites:
  TEST_DATABASE_URL=... I_UNDERSTAND_THIS_WIPES_TEST_DB=1 python3 tests/test_attachments.py
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
STORE = tempfile.mkdtemp(prefix='hla-attachments-')
os.environ.update({'HLA_ATTACHMENTS_DIR': STORE, 'HLA_ATTACHMENT_MAX_MB': '2', 'OPENROUTER_API_KEY': 'x',
                   'HLA_VISION_MODELS': 'other/model', 'HLA_ATTACHMENTS_PER_MESSAGE': '4'})

import attachments as att  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)


def png(size=(40, 30), color=(200, 30, 30)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, 'PNG')
    return out.getvalue()


def pdf(text):
    """A one-page PDF with a line of text (hand-written: pypdf cannot add text)."""
    stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = io.BytesIO(), []
    out.write(b'%PDF-1.4\n')
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f'{i} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = out.tell()
    out.write(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for o in offsets:
        out.write(f'{o:010d} 00000 n \n'.encode())
    out.write(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return out.getvalue()


def docx_bytes(text):
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = 'host', 'docker'
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def xlsx_bytes():
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'VMs'
    sheet.append(['vmid', 'name'])
    sheet.append([205, 'monitoring'])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# ================================================================ part 1: no database
PNG = png()
check(att.sniff(PNG, 'photo.txt') == ('image/png', 'image'), 'sniff: PNG by content, not by its .txt name')
check(att.sniff(b'\xff\xd8\xff\xe0' + b'0' * 20, 'a.png')[0] == 'image/jpeg', 'sniff: JPEG')
check(att.sniff(b'GIF89a' + b'0' * 10)[0] == 'image/gif' and att.sniff(b'RIFF0000WEBPVP8 ')[0] == 'image/webp', 'sniff: GIF and WebP')
check(att.sniff(pdf('Hello'), 'x.jpg') == ('application/pdf', 'document'), 'sniff: PDF named .jpg is a PDF')
check(att.sniff(docx_bytes('x'), 'x.zip') == (att.DOCX, 'document') and att.sniff(xlsx_bytes())[0] == att.XLSX, 'sniff: DOCX and XLSX by zip contents')
check(att.sniff(b'<html><script>alert(1)</script></html>', 'page.html') == ('text/html', 'text'), 'sniff: HTML is text (never inline)')
check(att.sniff(b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'logo.svg') == ('image/svg+xml', 'text'), 'sniff: SVG is text, not an inline image')
check(att.sniff(b'\x00\x01\x02binary', 'a.txt') == ('application/octet-stream', 'binary'), 'sniff: binary with a .txt name stays binary')
check(att.sniff('naïve café, ünïcode'.encode(), 'notes.md') == ('text/markdown', 'text'), 'sniff: UTF-8 Markdown')
check(att.clean_name('../../etc/passwd') == 'passwd' and att.clean_name('a"b<c>.txt') == 'abc.txt' and att.clean_name('') == 'file',
      'names: no paths or quotes')

row = lambda data, name: {'sha256': att._write_blob(data), 'kind': att.sniff(data, name)[1], 'mime': att.sniff(data, name)[0], 'name': name}
check('Quarterly backup report' in att.extract_text(row(pdf('Quarterly backup report'), 'r.pdf')), 'extract: PDF text (pypdf)')
docx_text = att.extract_text(row(docx_bytes('Runbook for jellyfin'), 'r.docx'))
check('Runbook for jellyfin' in docx_text and 'host | docker' in docx_text, 'extract: DOCX paragraphs and tables')
check('205,monitoring' in att.extract_text(row(xlsx_bytes(), 'vms.xlsx')), 'extract: XLSX rows per sheet')
check(att.extract_text(row(PNG, 'p.png')) is None, 'extract: images have no text')
check(att.extract_text(row(b'PK\x03\x04broken', 'x.docx')) is None, 'extract: a broken zip is binary, no text')

secret = 'DB_PASSWORD=Sup3rS3cretValue!x9'
masked = att.masked(f'host: docker\n{secret}\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\ndef\n-----END OPENSSH PRIVATE KEY-----\nport: 8080')
check(secret not in masked and 'abc' not in masked and 'host: docker' in masked and 'port: 8080' in masked
      and '[masked: private key]' in masked, 'masking: secret values and key blocks hidden, the rest kept')
check(att.masked('password stored in Infisical at /Omada/CLIENT_SECRET') .startswith('password stored'), 'masking: references stay')

msgs = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'Look at this'}]
out = att.apply(msgs, {'text': 'FILES', 'images': ['data:image/png;base64,AAA']})
check(out[1]['content'][0] == {'type': 'text', 'text': 'Look at this\n\nFILES'} and out[1]['content'][1]['image_url']['url'].startswith('data:image/png')
      and msgs[1]['content'] == 'Look at this', 'apply: text + image parts on the last user message (input not changed)')
check(att.apply(msgs, {'text': 'FILES', 'images': []})[1]['content'] == 'Look at this\n\nFILES', 'apply: text only stays a string')
check(att.text_of(out[1]['content']) == 'Look at this\n\nFILES', 'text_of: parts back to text')
big = png((4000, 3000))
url = att.image_data_url({'mime': 'image/png'}, big)
from PIL import Image  # noqa: E402
import base64  # noqa: E402
small = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))
check(max(small.size) == att.IMAGE_SIDE, f'images: downscaled to {att.IMAGE_SIDE}px for the model ({small.size})')
check(att.model_sees_images('other/model') and not att.model_sees_images('base/model') and att.model_sees_images('openai/gpt-4o')
      and att.model_sees_images('xai/grok-4') and not att.model_sees_images('qwen/qwen3-235b-a22b'), 'vision: env list, name hints')
os.environ['HLA_NO_VISION_MODELS'] = 'openai/gpt-4o'
check(not att.model_sees_images('openai/gpt-4o'), 'vision: HLA_NO_VISION_MODELS wins')
os.environ.pop('HLA_NO_VISION_MODELS')
names = lambda tools: [t['function']['name'] for t in tools]
check(names(att.tool_definitions([], True)) == ['attachment_list', 'attachment_read'], 'tools: read-only runs only list and read')
check(names(att.tool_definitions(['sandbox'])) == list(att.TOOL_NAMES), 'tools: copy tools with a free-shell server')
check('attachment_to_server' not in names(att.tool_definitions([])), 'tools: no copy tools without a free shell')
check(names(att.tool_definitions(['sandbox'], False, False)) == ['attachment_create', 'attachment_from_server']
      and att.tool_definitions([], True, False) == [], 'tools: without files only create/fetch (none in a read-only run)')
check([len(c.encode()) <= 5 for c in att.utf8_chunks('ééééé', 5)] == [True, True, True] and ''.join(att.utf8_chunks('ééééé', 5)) == 'ééééé',
      'chunks: never split a UTF-8 character')
check(att.content_disposition('attachment', 'rapport été.pdf') == 'attachment; filename="rapport _t_.pdf"; filename*=UTF-8\'\'rapport%20%C3%A9t%C3%A9.pdf',
      'download name: ASCII fallback and UTF-8')

if not os.environ.get('TEST_DATABASE_URL') or os.environ.get('I_UNDERSTAND_THIS_WIPES_TEST_DB') != '1':
    print('\nPart 2 skipped: set TEST_DATABASE_URL and I_UNDERSTAND_THIS_WIPES_TEST_DB=1 for the API tests')
    print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
    sys.exit(1 if failures else 0)

# ================================================================ part 2: API, runs and tools against Postgres
os.environ.update({'DATABASE_URL': os.environ['TEST_DATABASE_URL'], 'AGENT_PLATFORM_TOKEN': 't' * 64, 'DASHBOARD_PASSWORD': 'pw',
                   'TYPESAFE_API_KEY': 'x', 'HLA_RISK_REVIEW': 'true', 'OPENROUTER_WORKER_MODEL': 'base/model', 'AGENT_ALLOWED_MODELS': 'base/model,other/model',
                   'HLA_WEBSITE_CHECK_MINUTES': '0', 'TOOL_GATEWAY_TOKEN': 'g' * 40})
import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as db:
    db.execute('DROP SCHEMA public CASCADE'); db.execute('CREATE SCHEMA public')

STATE = {'bodies': [], 'script': [], 'files': {}, 'gateway': []}


def reply_json(content=None, calls=None):
    message = {'role': 'assistant', 'content': content}
    if calls:
        message['tool_calls'] = [{'id': f'c{i}', 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a)}}
                                 for i, (n, a) in enumerate(calls)]
    return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if calls else 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'cost': 0.0001}}


async def handler(request):
    if 'typesafe' in request.url.host:
        return httpx.Response(200, json={'answers': {'expert': {'noul': 0.1}, 'risky': {'noul': 0.05}}, 'usage': {}})
    if 'openrouter' in request.url.host and request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': []})
    if 'openrouter' in request.url.host:
        body = json.loads(request.content)
        STATE['bodies'].append(body)
        if STATE['script']:
            return httpx.Response(200, json=STATE['script'].pop(0))
        if body.get('stream'):
            text = 'Seen.'
            events = [{'choices': [{'delta': {'content': text}}]}, {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'cost': 0.0001}}]
            return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n',
                                  headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json=reply_json('Plan text {"summary": "s", "tasks": []}'))
    if request.url.host == 'ssh-gateway-api' and request.url.path == '/v1/execute':
        sent = json.loads(request.content)
        STATE['gateway'].append(sent)
        if sent['mode'] == 'write_file':
            old = STATE['files'].get(sent['path'], '') if sent.get('append') else ''
            STATE['files'][sent['path']] = old + sent['content']
            return httpx.Response(200, json={'path': sent['path'], 'bytes': len(STATE['files'][sent['path']].encode()), 'sha256': 'f' * 64})
        if sent['mode'] == 'read_file':
            text = STATE['files'].get(sent['path'])
            if text is None:
                return httpx.Response(200, json={'path': sent['path'], 'exit_code': 2, 'stderr': 'no such file'})
            data = text.encode()
            part = data[sent['offset']:sent['offset'] + sent['length']]
            return httpx.Response(200, json={'path': sent['path'], 'size': len(data), 'offset': sent['offset'],
                                             'content': part.decode('utf-8', 'replace'), 'eof': sent['offset'] + len(part) >= len(data)})
    if request.url.host == 'ssh-gateway-api':
        return httpx.Response(200, json={'servers': {}, 'services': {}})
    return httpx.Response(404, json={})

_Real = httpx.AsyncClient
class MockAsync(_Real):
    def __init__(self, *a, **kw):
        kw['transport'] = httpx.MockTransport(handler)
        super().__init__(*a, **kw)
httpx.AsyncClient = MockAsync

import app as appmod  # noqa: E402
from agent_tool import runtime  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

H = {'x-agent-ui': '1'}
J = {**H, 'Content-Type': 'application/json'}


def q(sql, *args):
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        return db.execute(sql, args).fetchall()


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn(): return True
        time.sleep(0.1)
    return False


def last_user(body):
    return [m for m in body['messages'] if m['role'] == 'user'][-1]['content']


with TestClient(appmod.app) as c:
    anon = TestClient(appmod.app)
    check(anon.post('/ui/api/attachments?name=a.txt', content=b'hello', headers=H).status_code == 401, 'upload needs a session')
    check(c.post('/ui/api/login', json={'password': 'pw'}).status_code == 200, 'login')
    check(c.post('/ui/api/attachments?name=a.txt', content=b'hello').status_code == 403, 'upload needs the UI header (CSRF guard)')
    tid = c.post('/ui/api/threads', headers=J, json={'title': 'Files', 'workspace': 'home'}).json()['id']

    up = lambda data, name, **params: c.post('/ui/api/attachments', headers=H, content=data,
                                             params={'name': name, **{k: str(v) for k, v in params.items()}})
    r = up(PNG, 'holiday.txt', thread_id=tid, staged='true')
    image = r.json()
    check(r.status_code == 200 and image['mime'] == 'image/png' and image['kind'] == 'image' and image['inline'] and image['staged'],
          'upload: a PNG named .txt is stored as an image, staged for the message')
    check(up(b'x' * (2 * 1024 * 1024 + 10), 'big.bin', staged='true').status_code == 413, 'upload: over HLA_ATTACHMENT_MAX_MB is refused (413)')
    check(up(b'x', 'a.txt', thread_id=uuid.uuid4(), staged='true').status_code == 404, 'upload: unknown conversation 404')
    doc = up(f'Backup runbook for docker\nRun restic nightly.\n{secret}\n'.encode(), 'runbook.md', thread_id=tid, staged='true').json()
    html = up(b'<html><script>alert(document.cookie)</script></html>', 'page.html', thread_id=tid).json()
    svg = up(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 'logo.svg', thread_id=tid).json()

    g = c.get(image['url'])
    check(g.status_code == 200 and g.headers['content-type'] == 'image/png' and g.headers['content-disposition'].startswith('inline')
          and g.headers['x-content-type-options'] == 'nosniff' and 'sandbox' in g.headers['content-security-policy'] and g.content == PNG,
          'download: images inline with nosniff and a sandbox CSP')
    check(c.get(image['url'] + '?download=1').headers['content-disposition'].startswith('attachment'), 'download: ?download=1 forces a download')
    for item, label in ((html, 'HTML'), (svg, 'SVG')):
        g = c.get(item['url'])
        check(g.headers['content-type'].startswith('text/plain') and g.headers['content-disposition'].startswith('attachment'),
              f'download: {label} is never rendered (text/plain, attachment)')
    check(anon.get(image['url']).status_code == 401, 'download needs a session')
    check(c.get(f'/ui/api/attachments/{doc["id"]}/text').json()['text'].startswith('Backup runbook'), 'preview: extracted text')
    check(c.get(f'/ui/api/attachments/{image["id"]}/text').status_code == 415, 'preview: images have no text preview')

    # A vision model gets the image as an image part and the document text (masked) in the prompt.
    r = c.post(f'/ui/api/threads/{tid}/messages', headers=J, json={'content': 'What is in these files?', 'model_override': 'other/model',
                                                                    'attachment_ids': [image['id'], doc['id']]})
    check(r.status_code == 200, 'send: message with two staged files')
    wait_for(lambda: q('SELECT status FROM agent_threads WHERE id=%s', tid)[0]['status'] == 'idle')
    content = last_user(STATE['bodies'][-1])
    text = att.text_of(content)
    check(isinstance(content, list) and any(p.get('type') == 'image_url' and p['image_url']['url'].startswith('data:image/') for p in content),
          'model input: vision model gets the image as an image_url part')
    check('Backup runbook for docker' in text and 'runbook.md' in text and secret not in text and 'untrusted' in text,
          'model input: document text in the prompt, untrusted, secret line masked')
    thread = c.get(f'/ui/api/threads/{tid}').json()
    sent = [m for m in thread['messages'] if m['role'] == 'user'][-1]
    check(sorted(a['name'] for a in sent['attachments']) == ['holiday.txt', 'runbook.md'] and not any(a['staged'] for a in sent['attachments']),
          'thread view: the files are on the message they were sent with')
    r = c.post(f'/ui/api/threads/{tid}/messages', headers=J, json={'content': 'again', 'attachment_ids': [image['id']]})
    check(r.status_code == 422, 'send: a file that was already sent cannot be attached again')
    many = [up(b'n', f'n{i}.txt', thread_id=tid, staged='true').json()['id'] for i in range(5)]
    check(c.post(f'/ui/api/threads/{tid}/messages', headers=J, json={'content': 'many', 'attachment_ids': many}).status_code == 422,
          'send: more than HLA_ATTACHMENTS_PER_MESSAGE files refused')

    # A follow-up with a model that cannot see images: no image part, a note instead; the image still counts.
    c.post(f'/ui/api/threads/{tid}/messages', headers=J, json={'content': 'And the photo?', 'model_override': 'base/model'})
    wait_for(lambda: q('SELECT status FROM agent_threads WHERE id=%s', tid)[0]['status'] == 'idle')
    content = last_user(STATE['bodies'][-1])
    check(isinstance(content, str) and 'cannot read images' in content and 'holiday.txt' in content,
          'model input: non-vision model gets a note instead of the image (no failure)')
    lim = c.get('/ui/api/attachments/limits', params={'model': 'base/model'}).json()
    check(lim['vision'] is False and lim['max_per_message'] == 4 and lim['max_bytes'] == 2 * 1024 * 1024
          and c.get('/ui/api/attachments/limits', params={'model': 'other/model'}).json()['vision'] is True, 'limits: vision per model for the UI')

    # Agent tools: list, read, create, copy to and from a free-shell server.
    task_id = q("SELECT current_task_id FROM agent_threads WHERE id=%s", tid)[0]['current_task_id'] or \
        q("SELECT task_id FROM agent_thread_messages WHERE thread_id=%s AND role='assistant' ORDER BY created_at DESC LIMIT 1", tid)[0]['task_id']
    scope = {'task_id': task_id, 'thread_id': uuid.UUID(tid), 'project_id': None}
    STATE['files']['/srv/report.log'] = 'line one\nline two'
    STATE['script'] = [
        reply_json(calls=[('attachment_list', {}), ('attachment_read', {'id': doc['id'], 'length': 30}),
                          ('attachment_read', {'id': str(uuid.uuid4())})]),
        reply_json(calls=[('attachment_create', {'name': 'summary.md', 'content': '# Summary\nAll good.\nAPI_KEY=abcd1234efgh5678ijkl', 'mime': 'text/markdown'}),
                          ('attachment_to_server', {'id': doc['id'], 'server': 'sandbox', 'path': '/srv/runbook.md'}),
                          ('attachment_to_server', {'id': image['id'], 'server': 'sandbox', 'path': '/srv/p.png'}),
                          ('attachment_to_server', {'id': doc['id'], 'server': 'docker', 'path': '/srv/x.md'}),
                          ('attachment_from_server', {'server': 'sandbox', 'path': '/srv/report.log'})]),
        reply_json('Done: summary.md is ready.')]
    perms = {'sandbox': {'level': 6, 'free_shell': True}, 'docker': {'level': 1}}

    async def tool_run():
        async with httpx.AsyncClient() as client:
            return await runtime.completion_with_tools(client, {'model': 'base/model', 'messages': [{'role': 'user', 'content': 'go'}]}, 'x',
                                                       agent_id=uuid.uuid4(), permissions=perms, attachment_scope=scope, task_id=task_id)
    result = asyncio.run(tool_run())
    tool_msgs = [json.loads(m['content']) for m in STATE['bodies'][-1]['messages'] if m['role'] == 'tool']
    names = [t['function']['name'] for t in STATE['bodies'][-1]['tools']]
    check(all(n in names for n in att.TOOL_NAMES), 'tools: all attachment tools offered with a free-shell server')
    listing = tool_msgs[0]['attachments']
    check({'holiday.txt', 'runbook.md'} <= {a['name'] for a in listing}, 'attachment_list: the conversation\'s files')
    check(tool_msgs[1]['content'] == 'Backup runbook for docker\nRun ' and not tool_msgs[1]['eof'] and tool_msgs[1]['untrusted_data'],
          'attachment_read: a page of the text, untrusted')
    check(tool_msgs[2]['error'] == 'not_found', 'attachment_read: unknown id refused')
    created = tool_msgs[3]
    check(created['status'] == 'created' and created['type'] == 'text/markdown', 'attachment_create: a downloadable file')
    stored = c.get(f'/ui/api/attachments/{created["id"]}/content').content.decode()
    check(stored.startswith('# Summary') and 'abcd1234efgh5678ijkl' not in stored, 'attachment_create: secret values masked in agent files')
    check(tool_msgs[4]['status'] == 'written' and STATE['files']['/srv/runbook.md'].startswith('Backup runbook'),
          'attachment_to_server: text file written through the gateway write_file path')
    check(tool_msgs[5]['error'] == 'binary_not_supported', 'attachment_to_server: binary files refused (text-only gateway path)')
    check(tool_msgs[6]['error'] == 'file_tools_need_free_shell', 'attachment_to_server: needs a free shell on the server')
    fetched = tool_msgs[7]
    check(fetched['status'] == 'created' and fetched['name'] == 'report.log'
          and c.get(f'/ui/api/attachments/{fetched["id"]}/content').content == b'line one\nline two', 'attachment_from_server: file made downloadable')
    check(result['choices'][0]['message']['content'] == 'Done: summary.md is ready.', 'tool run finishes normally')
    files = c.get('/ui/api/attachments', params={'thread_id': tid}).json()['attachments']
    check({'summary.md', 'report.log'} <= {f['name'] for f in files if f['origin'] == 'agent'}, 'files view: agent files listed with the user\'s')
    thread = c.get(f'/ui/api/threads/{tid}').json()
    reply = [m for m in thread['messages'] if m['role'] == 'assistant' and m['task_id'] == str(task_id)]
    check(reply and {'summary.md', 'report.log'} <= {a['name'] for a in reply[0]['attachments']}, 'thread view: agent files shown on the reply')

    async def read_only_run():
        STATE['script'] = [reply_json('ok')]
        async with httpx.AsyncClient() as client:
            await runtime.completion_with_tools(client, {'model': 'base/model', 'messages': [{'role': 'user', 'content': 'go'}]}, 'x',
                                                agent_id=uuid.uuid4(), permissions=perms, attachment_scope=scope, read_only=True)
    asyncio.run(read_only_run())
    names = [t['function']['name'] for t in STATE['bodies'][-1].get('tools') or []]
    check('attachment_read' in names and 'attachment_create' not in names and 'attachment_to_server' not in names,
          'tools: a read-only run can only list and read files')

    # Projects: reference files reach the planner.
    ref = up(b'Use the domain bakery.example and Dutch copy.', 'brief.txt', staged='true').json()
    pid = c.post('/ui/api/projects', headers=J, json={'title': 'Site', 'goal': 'Build a website for the client.',
                                                      'attachment_ids': [ref['id']]}).json()['id']
    wait_for(lambda: any('Write the plan.' in str(b['messages']) for b in STATE['bodies']))
    plan_body = [b for b in STATE['bodies'] if 'Write the plan.' in str(b['messages'])][-1]
    check('Use the domain bakery.example' in plan_body['messages'][-1]['content'], 'project: reference files are in the planner prompt')
    check([f['name'] for f in c.get('/ui/api/attachments', params={'project_id': pid}).json()['attachments']] == ['brief.txt'],
          'project: files view lists the reference file')
    proj_upload = up(b'More notes', 'notes.txt', project_id=pid).json()
    check(proj_upload['project_id'] == pid and not proj_upload['staged'], 'project: upload straight to a project')

    # Dashboard task: files sent with "Launch a task".
    tfile = up(b'Disk report: /srv 91% full', 'disk.txt', staged='true').json()
    r = c.post('/ui/api/tasks/start-stream', headers=J, json={'task': 'Summarise the attached report', 'attachment_ids': [tfile['id']]})
    t = r.json()['id']
    wait_for(lambda: q('SELECT status FROM tasks WHERE id=%s', t)[0]['status'] != 'running')
    check('Disk report: /srv 91% full' in att.text_of(last_user(STATE['bodies'][-1])), 'task: files of a launched task reach the model')
    check([f['name'] for f in c.get('/ui/api/attachments', params={'task_id': t}).json()['attachments']] == ['disk.txt'], 'task: files listed per task')

    # Delete: the row goes, and the stored bytes once nothing else uses them.
    path = Path(STORE) / proj_upload['id'][:0]
    sha = q('SELECT sha256 FROM attachments WHERE id=%s', proj_upload['id'])[0]['sha256']
    check(c.delete(f'/ui/api/attachments/{proj_upload["id"]}', headers=H).status_code == 200
          and not (Path(STORE) / sha[:2] / sha).exists(), 'delete: row and unused file removed')
    check(anon.delete(f'/ui/api/attachments/{image["id"]}', headers=H).status_code == 401, 'delete needs a session')
    dup = up(PNG, 'again.png', thread_id=tid).json()
    check(q('SELECT count(DISTINCT sha256) AS n FROM attachments WHERE sha256=(SELECT sha256 FROM attachments WHERE id=%s)', dup['id'])[0]['n'] == 1
          and len(list((Path(STORE)).glob('*/' + q('SELECT sha256 FROM attachments WHERE id=%s', dup['id'])[0]['sha256']))) == 1,
          'storage: the same bytes are stored once')
    c.delete(f'/ui/api/attachments/{dup["id"]}', headers=H)
    check((Path(STORE) / q('SELECT sha256 FROM attachments WHERE id=%s', image['id'])[0]['sha256'][:2]).exists(),
          'delete: bytes still used by another attachment stay')

print('\n%d FAILED' % len(failures) if failures else '\nALL PASSED')
sys.exit(1 if failures else 0)
