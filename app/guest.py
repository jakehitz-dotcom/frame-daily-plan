"""Guest recipe sharing: a QR code on the plan opens a phone page where anyone in the house can paste a
recipe link (or recipe text) and put it on the Frame. No password, no app: the code carries a short
secret token instead, and a new code can be made at any time (which retires the old one).

The address in the code is the one the settings page was last opened at (e.g. http://192.168.0.20:8080),
unless a fixed address is set. Upper-case only, so the code uses QR's compact alphanumeric mode: fewer,
bigger squares that a phone reads from across the kitchen.
"""
import json
import os
import secrets
import threading

import config

FILE = os.path.join(config.DATA, 'guest.json')
DEFAULTS = {'enabled': True, 'url': ''}
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O or 1/I, should anyone ever type it
_lock = threading.Lock()


def settings(cfg):
    return dict(DEFAULTS, **(cfg.get('guest') or {}))


def _load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f)
    os.replace(tmp, FILE)


def token():
    with _lock:
        d = _load()
        if not d.get('token'):
            d['token'] = ''.join(secrets.choice(ALPHABET) for _ in range(6))
            _save(d)
        return d['token']


def rotate():
    with _lock:
        d = _load()
        d['token'] = ''.join(secrets.choice(ALPHABET) for _ in range(6))
        _save(d)
        return d['token']


def check(t):
    return bool(t) and secrets.compare_digest(t.upper(), token())


def learn_base(host_url):
    """Remember the address the settings page was opened at: the phones are on the same network."""
    base = (host_url or '').rstrip('/')
    host = base.split('://')[-1].split(':')[0].lower()
    if not base or host in ('localhost', '127.0.0.1', '0.0.0.0', '[::1]') or host.startswith('172.17.'):
        return
    with _lock:
        d = _load()
        if d.get('base') != base:
            d['base'] = base
            _save(d)


def base(cfg):
    return (settings(cfg)['url'] or _load().get('base') or '').rstrip('/')


def url(cfg):
    """The QR code's address, or None when sharing is off or no address is known yet."""
    if not settings(cfg)['enabled']:
        return None
    b = base(cfg)
    return f'{b}/G/{token()}'.upper() if b else None
