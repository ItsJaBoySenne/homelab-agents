"""Masking of known secret values (credentials, generated secrets) wherever the gateway returns or stores text.

One helper for every caller (credentials.redact, HTTP tools, MCP results, audit details) so the encoded forms are
caught everywhere alike. A value (at least MIN_LENGTH characters) is replaced when it appears
  - as is, in any string or dict key of a JSON-like payload;
  - with its special characters escaped, in any mix: JSON (\\/ \\" \\uXXXX), percent-encoding (quote / quote_plus,
    upper- or lowercase hex), HTML entities (&amp; &#x2F; &#47; ...);
  - base64-encoded (standard and URL-safe, with or without padding), also as `user:value` for Basic auth when the
    caller names the user part;
  - cut off: a string that ends with the first PREFIX_LENGTH or more characters of a value (a truncated answer).
"""
import base64
import functools
import re

MIN_LENGTH = 6
PREFIX_LENGTH = 8
_NAMED = {'&': ('&amp;',), '<': ('&lt;',), '>': ('&gt;',), '"': ('&quot;',), "'": ('&apos;',), '/': ('&sol;',),
          '=': ('&equals;',), ':': ('&colon;',), '+': ('&plus;',), '?': ('&quest;',), '#': ('&num;',), '%': ('&percnt;',)}
_JSON = {'\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b', '\f': '\\f'}


def _char(c):
    if c.isascii() and c.isalnum():
        return re.escape(c)
    code = ord(c)
    alts = [re.escape(c), '(?i:' + ''.join('%%%02x' % b for b in c.encode()) + ')']
    if c == ' ':
        alts.append(r'\+')
    if c in '"\\/':
        alts.append(re.escape('\\' + c))
    if c in _JSON:
        alts.append(re.escape(_JSON[c]))
    if code < 0x10000:
        alts.append(r'(?i:\\u%04x)' % code)
    alts += [r'(?i:&#x0*%x;)' % code, r'&#0*%d;' % code] + [re.escape(e) for e in _NAMED.get(c, ())]
    return '(?:' + '|'.join(alts) + ')'


def _b64_forms(raw):
    out = []
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        text = enc(raw.encode()).decode().rstrip('=')
        if len(text) >= MIN_LENGTH:
            out.append(re.escape(text) + '={0,2}')
    return out


@functools.lru_cache(maxsize=512)
def _pattern(value, users):
    alts = [''.join(_char(c) for c in value)] + _b64_forms(value)
    for user in users:
        alts += _b64_forms(user + ':' + value)
    return re.compile('|'.join(alts))


def prepare(entries):
    """[(value, replacement[, users])] -> the compiled list mask_text/mask use; short or empty values are skipped."""
    out = []
    for entry in entries:
        value, replacement = entry[0], entry[1]
        users = tuple(sorted(u for u in (entry[2] if len(entry) > 2 else ()) if isinstance(u, str) and u))
        if isinstance(value, str) and len(value) >= MIN_LENGTH:
            out.append((value, replacement, _pattern(value, users)))
    out.sort(key=lambda e: -len(e[0]))
    return out


def _tail(text, value, replacement):
    """A string ending with a long enough prefix of the value (cut off in the middle of it)."""
    window = text[-(len(value) - 1):] if len(value) > 1 else ''
    start = window.find(value[:PREFIX_LENGTH])
    while start != -1:
        rest = window[start:]
        if value.startswith(rest):
            return text[:len(text) - len(rest)] + replacement
        start = window.find(value[:PREFIX_LENGTH], start + 1)
    return text


def mask_text(text, prepared):
    if not isinstance(text, str) or not text or not prepared:
        return text
    for value, replacement, pattern in prepared:
        text = pattern.sub(lambda _m, r=replacement: r, text)
        if len(value) > PREFIX_LENGTH:
            text = _tail(text, value, replacement)
    return text


def mask(payload, prepared):
    """A JSON-like payload with every known value masked in strings and dict keys."""
    if not prepared:
        return payload

    def walk(item):
        if isinstance(item, str):
            return mask_text(item, prepared)
        if isinstance(item, list):
            return [walk(x) for x in item]
        if isinstance(item, tuple):
            return tuple(walk(x) for x in item)
        if isinstance(item, dict):
            return {walk(k) if isinstance(k, str) else k: walk(v) for k, v in item.items()}
        return item
    return walk(payload)
