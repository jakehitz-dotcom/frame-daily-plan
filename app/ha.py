"""Reading from Home Assistant over its REST API (read-only: this app never changes anything there).

Needs the Home Assistant address (e.g. http://192.168.0.20:8123) and a long-lived access token
(Home Assistant → your profile → Security → Long-lived access tokens).
"""
import requests

import config

TIMEOUT = 10  # seconds; the plan must never wait long on Home Assistant


def settings(cfg=None):
    ha = (cfg or config.load()).get('home_assistant') or {}
    return (ha.get('url') or '').strip().rstrip('/'), (ha.get('token') or '').strip()


def configured(cfg=None):
    url, token = settings(cfg)
    return bool(url and token)


def _get(path, cfg=None):
    url, token = settings(cfg)
    if not (url and token):
        raise RuntimeError('Home Assistant address or token not set')
    r = requests.get(f'{url}/api/{path}', headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT)
    if r.status_code == 401:
        raise RuntimeError('Home Assistant refused the token (401): create a new long-lived access token')
    r.raise_for_status()
    return r.json()


def check(cfg=None):
    """One line for the log: does the connection work, which version, how many entities."""
    info = _get('config', cfg)
    states = _get('states', cfg)
    return f"ok · {info.get('location_name', 'Home')} · version {info.get('version', '?')} · {len(states)} entities"


def state(entity_id, cfg=None):
    """One entity, e.g. state('sensor.outdoor_temperature') → {'state': '18.5', 'attributes': {...}, ...}"""
    return _get(f'states/{entity_id}', cfg)


def states(cfg=None, domain=None):
    """All entities, or only one domain such as 'sensor' or 'lock'."""
    all_states = _get('states', cfg)
    return [s for s in all_states if not domain or s['entity_id'].startswith(domain + '.')]
