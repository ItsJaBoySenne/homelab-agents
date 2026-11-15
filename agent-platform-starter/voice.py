"""HLA_VOICE_V1: optional server-side speech for the Chief's car mode (/ui/api/voice).

The car mode in the dashboard speaks and listens with the browser by default (Web Speech API, speechSynthesis). These
endpoints are the alternative for browsers without speech recognition (or when the user picks "Server"): any
OpenAI-compatible speech service, e.g. a self-hosted faster-whisper/speaches container or OpenAI itself.

  GET  /ui/api/voice/config      what is configured: {"server_stt", "server_tts", "tts_voices", "stt_language"}
  POST /ui/api/voice/transcribe  raw audio body (webm/ogg/mp4/wav/mp3, sniffed by content, max 25 MB) -> {"text"}
  POST /ui/api/voice/speak       {"text"} (max 4000 characters) -> audio/mpeg

Settings (router .env; the keys never reach the browser and never appear in an error):
  VOICE_STT_BASE_URL  e.g. http://whisper:8000/v1 or https://api.openai.com/v1   (unset: server STT off)
  VOICE_STT_API_KEY   bearer key, optional for a self-hosted service
  VOICE_STT_MODEL     default whisper-1 (Systran/faster-whisper-small for speaches)
  VOICE_STT_LANGUAGE  default nl
  VOICE_TTS_BASE_URL  e.g. http://speaches:8000/v1 or https://api.openai.com/v1   (unset: server TTS off)
  VOICE_TTS_API_KEY, VOICE_TTS_MODEL (default tts-1), VOICE_TTS_VOICE (default alloy),
  VOICE_TTS_VOICES    comma-separated voices offered in the car mode picker (default: VOICE_TTS_VOICE)
"""
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

MAX_AUDIO = 25 * 1024 * 1024
MAX_TEXT = 4000
_transport = None  # tests put an httpx.MockTransport here


def _env(name, default=''):
    return (os.getenv(name) or default).strip()


def stt_enabled():
    return bool(_env('VOICE_STT_BASE_URL'))


def tts_enabled():
    return bool(_env('VOICE_TTS_BASE_URL'))


def tts_voices():
    default = _env('VOICE_TTS_VOICE', 'alloy')
    voices = [v.strip() for v in _env('VOICE_TTS_VOICES').split(',') if v.strip()]
    return voices or [default]


def sniff_audio(data):
    """(mime, filename) of a recording from its first bytes, or None when it is not audio we accept."""
    if data[:4] == b'\x1a\x45\xdf\xa3':
        return 'audio/webm', 'speech.webm'          # Chrome/Firefox MediaRecorder (Matroska/WebM)
    if data[:4] == b'OggS':
        return 'audio/ogg', 'speech.ogg'
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'audio/wav', 'speech.wav'
    if data[4:8] == b'ftyp':
        return 'audio/mp4', 'speech.m4a'            # Safari/iOS MediaRecorder
    if data[:3] == b'ID3' or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return 'audio/mpeg', 'speech.mp3'
    return None


def _url(base, path):
    return base.rstrip('/') + path


def _headers(key_env):
    key = _env(key_env)
    return {'Authorization': 'Bearer ' + key} if key else {}


def _client(timeout):
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


def _provider_error(what, response=None, exc=None):
    """A clear message for the dashboard; never the key or the request headers."""
    if exc is not None:
        return HTTPException(502, f'The {what} service is not reachable ({type(exc).__name__}). Check VOICE_{what.upper()}_BASE_URL.')
    status = response.status_code
    detail = ''
    try:
        body = response.json()
        err = body.get('error') if isinstance(body, dict) else None
        detail = (err.get('message') if isinstance(err, dict) else err) or body.get('detail') or ''
    except ValueError:
        detail = response.text[:200]
    hint = {401: 'the API key was refused', 403: 'the API key may not use this', 404: 'wrong base URL or model',
            429: 'rate limit or credits used up'}.get(status, 'the service returned an error')
    text = f'The {what} service answered {status}: {hint}'
    if detail:
        text += ' (' + str(detail)[:200] + ')'
    return HTTPException(502, text)


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    voice: str | None = Field(default=None, max_length=64)


def build_router(dashboard_read, dashboard_write):
    router = APIRouter(prefix='/ui/api/voice', tags=['voice'])

    @router.get('/config')
    def config(request: Request):
        dashboard_read(request)
        return {'server_stt': stt_enabled(), 'server_tts': tts_enabled(),
                'tts_voices': tts_voices() if tts_enabled() else [], 'stt_language': _env('VOICE_STT_LANGUAGE', 'nl')}

    @router.post('/transcribe')
    async def transcribe(request: Request, language: str | None = None):
        dashboard_write(request)
        if not stt_enabled():
            raise HTTPException(404, 'Server speech recognition is off (set VOICE_STT_BASE_URL)')
        declared = request.headers.get('content-length')
        if declared and declared.isdigit() and int(declared) > MAX_AUDIO:
            raise HTTPException(413, 'Recording is larger than 25 MB')
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_AUDIO:
                raise HTTPException(413, 'Recording is larger than 25 MB')
            chunks.append(chunk)
        data = b''.join(chunks)
        if not data:
            raise HTTPException(422, 'Empty recording')
        kind = sniff_audio(data)
        if not kind:
            raise HTTPException(415, 'Not a recording this accepts (webm, ogg, mp4/m4a, wav or mp3)')
        mime, filename = kind
        lang = (language or _env('VOICE_STT_LANGUAGE', 'nl')).split('-')[0].lower()[:5]
        form = {'model': _env('VOICE_STT_MODEL', 'whisper-1'), 'response_format': 'json'}
        if lang.isalpha():
            form['language'] = lang
        try:
            async with _client(120) as client:
                r = await client.post(_url(_env('VOICE_STT_BASE_URL'), '/audio/transcriptions'), headers=_headers('VOICE_STT_API_KEY'),
                                      data=form, files={'file': (filename, data, mime)})
        except httpx.HTTPError as exc:
            raise _provider_error('stt', exc=exc)
        if r.status_code >= 400:
            raise _provider_error('stt', r)
        try:
            text = r.json().get('text', '')
        except ValueError:
            text = r.text
        return {'text': (text or '').strip()}

    @router.post('/speak')
    async def speak(body: SpeakBody, request: Request):
        dashboard_write(request)
        if not tts_enabled():
            raise HTTPException(404, 'Server speech is off (set VOICE_TTS_BASE_URL)')
        text = body.text.strip()
        if not text:
            raise HTTPException(422, 'Nothing to say')
        voices = tts_voices()
        voice = body.voice if body.voice in voices else voices[0]
        payload = {'model': _env('VOICE_TTS_MODEL', 'tts-1'), 'voice': voice, 'input': text, 'response_format': 'mp3'}
        try:
            async with _client(60) as client:
                r = await client.post(_url(_env('VOICE_TTS_BASE_URL'), '/audio/speech'), headers=_headers('VOICE_TTS_API_KEY'), json=payload)
        except httpx.HTTPError as exc:
            raise _provider_error('tts', exc=exc)
        if r.status_code >= 400:
            raise _provider_error('tts', r)
        return Response(r.content, media_type='audio/mpeg', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
