"""Strict single-purpose request for creating a development secret.

This is a trusted backend workflow, not a general model risk bypass.
The input never supplies a secret value, Infisical path, or project id.
"""
import re

PATTERN = re.compile(
    r'\ACREATE_DEV_SECRET name=(HLA_DEV_[A-Z0-9_]{1,52}) kind=(password|token|hex_key)\Z'
)

def parse_request(task_text):
    if not isinstance(task_text, str) or len(task_text) > 20000:
        return None
    # Conversation wrapper from threads/routes.py. Only the last instruction is actionable.
    if 'LATEST USER MESSAGE:' in task_text:
        latest = task_text.rsplit('LATEST USER MESSAGE:', 1)[1].strip()
    else:
        latest = task_text.strip()
    if len(latest) > 110:
        return None
    match = PATTERN.fullmatch(latest)
    if match is None:
        return None
    return {'name': match.group(1), 'kind': match.group(2)}
