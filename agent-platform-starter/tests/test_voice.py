"""Server speech for the Chief's car mode (HLA_VOICE_V1): config, limits, sniffing, auth, provider errors.

python3 tests/test_voice.py   (no database, no network: a fake OpenAI-compatible service through httpx.MockTransport)
"""
import os as _os, pathlib as _pl  # the fixture inventory these suites were written against (tests/fixtures/)
_os.environ['HLA_INVENTORY'] = str(_pl.Path(__file__).resolve().parent / 'fixtures' / 'inventory.yaml')
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for k in [k for k in os.environ if k.startswith('VOICE_')]:
    os.environ.pop(k)
import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import voice  # noqa: E402

failures = []
def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond: failures.append(label)

KEY = 'sk-VOICE-secret-0123456789'
SEEN = []
MODE = {'status': 200, 'raise': None}


def handler(request):
    SEEN.append(request)
    if MODE['raise']:
        raise MODE['raise']
    if MODE['status'] != 200:
        return httpx.Response(MODE['status'], json={'error': {'message': 'quota exceeded'}})
    if request.url.path.endswith('/audio/transcriptions'):
        return httpx.Response(200, json={'text': '  Hoe staat het met de projecten?  '})
    if request.url.path.endswith('/audio/speech'):
        return httpx.Response(200, content=b'ID3fake-mp3', headers={'content-type': 'audio/mpeg'})
    return httpx.Response(404)


voice._transport = httpx.MockTransport(handler)


def read(request: Request):
    if request.headers.get('x-test-auth') != 'ok':
        raise HTTPException(401, 'Login required')


def write(request: Request):
    read(request)
    if request.headers.get('x-agent-ui') != '1':
        raise HTTPException(403, 'Missing UI request header')


app = FastAPI()
app.include_router(voice.build_router(read, write))
c = TestClient(app)
R = {'x-test-auth': 'ok'}
W = {'x-test-auth': 'ok', 'x-agent-ui': '1'}
WEBM = b'\x1a\x45\xdf\xa3' + b'\x00' * 200
OGG = b'OggS' + b'\x00' * 50
WAV = b'RIFF\x00\x00\x00\x00WAVEfmt ' + b'\x00' * 40
M4A = b'\x00\x00\x00\x20ftypM4A ' + b'\x00' * 40

# Off by default.
r = c.get('/ui/api/voice/config', headers=R)
check(r.status_code == 200 and r.json() == {'server_stt': False, 'server_tts': False, 'tts_voices': [], 'stt_language': 'en',
                                         'language': 'en-US'},
      f'config: everything off by default {r.json()}')
check(c.post('/ui/api/voice/transcribe', headers=W, content=WEBM).status_code == 404, 'transcribe off -> 404')
check(c.post('/ui/api/voice/speak', headers=W, json={'text': 'hallo'}).status_code == 404, 'speak off -> 404')

# Auth.
check(c.get('/ui/api/voice/config').status_code == 401, 'config needs a login')
check(c.post('/ui/api/voice/transcribe', headers=R, content=WEBM).status_code == 403, 'transcribe needs the UI header')
check(c.post('/ui/api/voice/speak', headers=R, json={'text': 'x'}).status_code == 403, 'speak needs the UI header')

# On.
os.environ.update({'VOICE_STT_BASE_URL': 'http://whisper.local:8000/v1/', 'VOICE_STT_API_KEY': KEY, 'VOICE_STT_MODEL': 'Systran/faster-whisper-small',
                   'VOICE_TTS_BASE_URL': 'https://tts.example/v1', 'VOICE_TTS_API_KEY': KEY, 'VOICE_TTS_VOICES': 'alloy, nova'})
r = c.get('/ui/api/voice/config', headers=R)
check(r.json() == {'server_stt': True, 'server_tts': True, 'tts_voices': ['alloy', 'nova'], 'stt_language': 'en', 'language': 'en-US'}, f'config on {r.json()}')
check(KEY not in r.text and 'whisper.local' not in r.text, 'config shows neither key nor URL')

# Sniffing and limits.
check(voice.sniff_audio(WEBM)[0] == 'audio/webm' and voice.sniff_audio(OGG)[0] == 'audio/ogg' and voice.sniff_audio(WAV)[0] == 'audio/wav'
      and voice.sniff_audio(M4A)[0] == 'audio/mp4' and voice.sniff_audio(b'ID3\x03' + b'\x00' * 10)[0] == 'audio/mpeg', 'audio formats sniffed by content')
check(voice.sniff_audio(b'<html><script>') is None and voice.sniff_audio(b'\x89PNG\r\n\x1a\n') is None, 'non-audio refused')
r = c.post('/ui/api/voice/transcribe', headers={**W, 'content-type': 'audio/webm'}, content=b'<html>not audio</html>')
check(r.status_code == 415, 'a non-audio upload is refused (415) whatever its Content-Type says')
check(c.post('/ui/api/voice/transcribe', headers=W, content=b'').status_code == 422, 'empty recording refused')
r = c.post('/ui/api/voice/transcribe', headers={**W, 'content-length': str(voice.MAX_AUDIO + 1)}, content=WEBM)
check(r.status_code == 413, 'declared size above 25 MB refused')
old = voice.MAX_AUDIO; voice.MAX_AUDIO = 100
r = c.post('/ui/api/voice/transcribe', headers=W, content=WEBM)
check(r.status_code == 413, 'streamed size above the limit refused')
voice.MAX_AUDIO = old
check(c.post('/ui/api/voice/speak', headers=W, json={'text': 'x' * 4001}).status_code == 422, 'speak text above 4000 characters refused')

# Transcribe.
SEEN.clear()
r = c.post('/ui/api/voice/transcribe?language=en-US', headers=W, content=M4A)
check(r.status_code == 200 and r.json() == {'text': 'Hoe staat het met de projecten?'}, f'transcription returned trimmed {r.text}')
req = SEEN[0]
body = req.content.decode('latin-1')
check(str(req.url) == 'http://whisper.local:8000/v1/audio/transcriptions' and req.headers['authorization'] == 'Bearer ' + KEY,
      'OpenAI-compatible endpoint with the server-side key')
check('Systran/faster-whisper-small' in body and 'name="language"\r\n\r\nen' in body and 'filename="speech.m4a"' in body
      and 'audio/mp4' in body, 'model, language from the browser, file name and type from the sniffed content')
SEEN.clear()
c.post('/ui/api/voice/transcribe', headers=W, content=WEBM)
check('name="language"\r\n\r\nen' in SEEN[0].content.decode('latin-1'), 'default language en (from HLA_VOICE_LANGUAGE=en-US)')
os.environ['HLA_VOICE_LANGUAGE'] = 'nl-BE'
SEEN.clear()
c.post('/ui/api/voice/transcribe', headers=W, content=WEBM)
r = c.get('/ui/api/voice/config', headers=R)
check('name="language"\r\n\r\nnl' in SEEN[0].content.decode('latin-1') and r.json()['language'] == 'nl-BE' and r.json()['stt_language'] == 'nl',
      'HLA_VOICE_LANGUAGE=nl-BE: car mode defaults to Dutch, server transcription too')
os.environ['HLA_VOICE_LANGUAGE'] = 'en-US; drop'
check(voice.default_language() == 'en-US', 'a malformed HLA_VOICE_LANGUAGE falls back to en-US')
del os.environ['HLA_VOICE_LANGUAGE']

# Speak.
SEEN.clear()
r = c.post('/ui/api/voice/speak', headers=W, json={'text': 'Alles draait.', 'voice': 'nova'})
check(r.status_code == 200 and r.headers['content-type'] == 'audio/mpeg' and r.content == b'ID3fake-mp3'
      and r.headers.get('cache-control') == 'no-store', 'speech returned as audio/mpeg, not cached')
sent = json.loads(SEEN[0].content)
check(str(SEEN[0].url) == 'https://tts.example/v1/audio/speech' and sent == {'model': 'tts-1', 'voice': 'nova', 'input': 'Alles draait.', 'response_format': 'mp3'},
      f'speech request {sent}')
SEEN.clear()
c.post('/ui/api/voice/speak', headers=W, json={'text': 'x', 'voice': 'not-offered'})
check(json.loads(SEEN[0].content)['voice'] == 'alloy', 'a voice that is not offered falls back to the first')
os.environ.pop('VOICE_TTS_API_KEY')
SEEN.clear()
c.post('/ui/api/voice/speak', headers=W, json={'text': 'x'})
check('authorization' not in SEEN[0].headers, 'no key configured: no Authorization header (self-hosted service)')
os.environ['VOICE_TTS_API_KEY'] = KEY

# Provider errors: clear, and never the key.
MODE['status'] = 429
r = c.post('/ui/api/voice/speak', headers=W, json={'text': 'x'})
check(r.status_code == 502 and 'rate limit or credits' in r.json()['detail'] and 'quota exceeded' in r.json()['detail'] and KEY not in r.text,
      f'429 explained without the key: {r.json()["detail"]}')
MODE['status'] = 401
r = c.post('/ui/api/voice/transcribe', headers=W, content=WEBM)
check(r.status_code == 502 and 'API key was refused' in r.json()['detail'] and KEY not in r.text, 'refused key explained')
MODE['status'] = 200; MODE['raise'] = httpx.ConnectError('boom')
r = c.post('/ui/api/voice/transcribe', headers=W, content=WEBM)
check(r.status_code == 502 and 'not reachable' in r.json()['detail'] and 'VOICE_STT_BASE_URL' in r.json()['detail'] and KEY not in r.text,
      'unreachable service explained')
MODE['raise'] = None

print()
print(f'{len(failures)} FAILED: {failures}' if failures else 'ALL PASSED')
sys.exit(1 if failures else 0)
