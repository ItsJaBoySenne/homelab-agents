"""Approval identity, expiry and audit trail (HLA_APPROVAL_AUDIT_V1).

Identity comes from Authelia's Remote-User/Remote-Groups headers, which Traefik adds after forward-auth.
The headers are only trusted when the TCP peer is a configured proxy address: the API port is also
reachable on the LAN, where anyone could send those headers themselves.
"""
import os
import uuid

from fastapi import HTTPException


def _csv(key):
    return {item.strip() for item in os.getenv(key, '').split(',') if item.strip()}


def ttl_hours():
    try: return min(720, max(1, int(os.getenv('HLA_APPROVAL_TTL_HOURS', '72'))))
    except (ValueError, TypeError): return 72


def identity(request):
    """The Authelia user behind this request, or None when it cannot be verified."""
    peer = request.client.host if request.client else None
    if not peer or peer not in _csv('HLA_TRUSTED_PROXY_IPS'):
        return None
    user = request.headers.get('remote-user', '').strip()
    if not user or len(user) > 128 or any(ch in user for ch in '\r\n,'):
        return None
    groups = {g.strip() for g in request.headers.get('remote-groups', '').split(',') if g.strip()}
    return {'user': user, 'groups': sorted(groups)}


def approver_status(request):
    """Who would be recorded as approver, and whether they may approve. Never raises."""
    users, groups = _csv('HLA_APPROVER_USERS'), _csv('HLA_APPROVER_GROUPS')
    # Naming approvers implies identity is required; otherwise it is opt-in for a gradual rollout.
    required = os.getenv('HLA_REQUIRE_APPROVER_IDENTITY', 'false').lower() == 'true' or bool(users or groups)
    who = identity(request)
    if who is None:
        if required:
            return {'allowed': False, 'actor': None, 'verified': False,
                    'reason': 'Approvals need a verified Authelia identity (open the dashboard through Traefik).'}
        return {'allowed': True, 'actor': 'dashboard_session', 'verified': False, 'reason': None}
    if (users or groups) and who['user'] not in users and not groups.intersection(who['groups']):
        return {'allowed': False, 'actor': who['user'], 'verified': True,
                'reason': who['user'] + ' is not an approver.'}
    return {'allowed': True, 'actor': who['user'], 'verified': True, 'reason': None}


def require_approver(request):
    status = approver_status(request)
    if not status['allowed']:
        raise HTTPException(403, status['reason'])
    return status['actor']


def record(db, task_id, action, actor, *, reason=None, scope=None):
    db.execute('INSERT INTO task_approval_events(id,task_id,action,actor,reason,scope) VALUES(%s,%s,%s,%s,%s,%s)',
               (uuid.uuid4(), task_id, action, actor, reason, scope))


def expire_stale(db):
    """Retire approval requests older than the TTL so an old, forgotten request cannot be run later."""
    rows = db.execute("""UPDATE tasks SET status='expired', error_message='Approval request expired'
        WHERE status='human_approval_required' AND created_at < NOW() - make_interval(hours => %s)
        RETURNING id""", (ttl_hours(),)).fetchall()
    # HLA_CHAT_APPROVAL_V1: a command an agent asked to run expires the same way.
    commands = db.execute("""UPDATE tasks SET pending_command=pending_command||'{"status":"expired"}'
        WHERE pending_command->>'status'='pending' AND created_at < NOW() - make_interval(hours => %s)
        RETURNING id""", (ttl_hours(),)).fetchall()
    for row in rows:
        record(db, row['id'], 'expired', 'system')
    for row in commands:
        record(db, row['id'], 'expired', 'system', scope='command')
    if rows or commands:
        db.commit()


def events(db, task_id):
    return db.execute('''SELECT action,actor,reason,scope,created_at FROM task_approval_events
        WHERE task_id=%s ORDER BY created_at''', (task_id,)).fetchall()
