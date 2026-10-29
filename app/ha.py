"""Home Assistant: reads states over REST; listens for shares and notification buttons; sends
the recipe notification to the phone that shared. It never changes devices or configuration.

Needs the Home Assistant address (e.g. http://192.168.0.20:8123) and a long-lived access token
(Home Assistant → your profile → Security → Long-lived access tokens).
"""
import json
import re

import requests

import config

TIMEOUT = 10  # seconds; the plan must never wait long on Home Assistant


def settings(cfg=None):
    ha = (cfg or config.load()).get('home_assistant') or {}
    return (ha.get('url') or '').strip().rstrip('/'), (ha.get('token') or '').strip()


def configured(cfg=None):
    url, token = settings(cfg)
    return bool(url and token)


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


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
    everything = _get('states', cfg)
    lists = [s['entity_id'] for s in everything if s['entity_id'].startswith('todo.')]
    return (f"ok · {info.get('location_name', 'Home')} · version {info.get('version', '?')} · {len(everything)} entities"
            + (f" · to-do lists: {', '.join(lists)}" if lists else ''))


def state(entity_id, cfg=None):
    """One entity, e.g. state('sensor.outdoor_temperature') → {'state': '18.5', 'attributes': {...}, ...}"""
    return _get(f'states/{entity_id}', cfg)


def resolve_todo_lists(names, cfg=None):
    """Entity IDs for the configured lists; a list can also be given by its name ("Jake's list")."""
    wanted = [n.strip() for n in names if n.strip()]
    if all(n.startswith('todo.') for n in wanted):
        return wanted, []
    lists = {s['entity_id']: (s.get('attributes') or {}).get('friendly_name', '') for s in states(cfg, 'todo')}
    found, missing = [], []
    for n in wanted:
        match = n if n in lists else next((e for e, name in lists.items() if name.lower() == n.lower()), None)
        (found if match else missing).append(match or n)
    return found, missing


def todo_lists(cfg=None):
    """[(entity_id, name, open items)] for every to-do list in Home Assistant."""
    return [(s['entity_id'], (s.get('attributes') or {}).get('friendly_name', ''), s.get('state'))
            for s in states(cfg, 'todo')]


def todo_items(entity_ids, cfg=None):
    """Items of Home Assistant to-do lists (Google Tasks, Shopping List, Todoist, ...), in the list's
    own order: {entity_id: [{'summary', 'due': 'YYYY-MM-DD' or None, 'done': bool, 'completed': ISO or None}]}.
    todo.get_items only reads."""
    url, token = settings(cfg)
    if not (url and token):
        raise RuntimeError('Home Assistant address or token not set')
    r = requests.post(f'{url}/api/services/todo/get_items?return_response', headers=_headers(token), timeout=TIMEOUT,
                      json={'entity_id': list(entity_ids)})
    r.raise_for_status()
    out = {}
    for entity, body in (r.json().get('service_response') or {}).items():
        out[entity] = [{'summary': i.get('summary', '').strip(), 'due': (i.get('due') or '')[:10] or None,
                        'done': i.get('status') == 'completed', 'completed': i.get('completed'), 'uid': i.get('uid')}
                       for i in body.get('items', []) if i.get('summary', '').strip()]
    return out


def snapshot(cfg=None):
    """Every entity's state in one request, as {entity_id: state dict}."""
    return {s['entity_id']: s for s in _get('states', cfg)}


def states(cfg=None, domain=None):
    """All entities, or only one domain such as 'sensor' or 'lock'."""
    all_states = _get('states', cfg)
    return [s for s in all_states if not domain or s['entity_id'].startswith(domain + '.')]


# ── Events (websocket): shares from the phone app, notification buttons ───────────────────────
def _ws_url(cfg=None):
    url, _ = settings(cfg)
    return re.sub(r'^http', 'ws', url) + '/api/websocket'


def _ws_open(cfg=None, timeout=TIMEOUT):
    import websocket
    _, token = settings(cfg)
    ws = websocket.create_connection(_ws_url(cfg), timeout=timeout)
    if json.loads(ws.recv()).get('type') != 'auth_required':
        raise RuntimeError('unexpected Home Assistant websocket greeting')
    ws.send(json.dumps({'type': 'auth', 'access_token': token}))
    reply = json.loads(ws.recv())
    if reply.get('type') != 'auth_ok':
        ws.close()
        raise RuntimeError('Home Assistant refused the token on the websocket')
    return ws


def ws_call(message, cfg=None):
    """One websocket command (e.g. the device registry), on a short-lived connection."""
    ws = _ws_open(cfg)
    try:
        ws.send(json.dumps(dict(message, id=1)))
        while True:
            reply = json.loads(ws.recv())
            if reply.get('id') == 1 and reply.get('type') == 'result':
                if not reply.get('success'):
                    raise RuntimeError(str(reply.get('error')))
                return reply.get('result')
    finally:
        ws.close()


def listen(load_cfg, handlers, log=print, idle=60):
    """Forever: subscribe to the given event types and call handlers[event_type](data).
    Reconnects after Home Assistant restarts; says so in the log once per outage."""
    import time
    import websocket
    failing = False
    while True:
        cfg = load_cfg()
        if not configured(cfg):
            time.sleep(30)
            continue
        try:
            ws = _ws_open(cfg, timeout=idle)
            for n, event_type in enumerate(handlers, 1):
                ws.send(json.dumps({'id': n, 'type': 'subscribe_events', 'event_type': event_type}))
            if failing:
                log('Home Assistant events connected again')
            failing = False
            while True:
                try:
                    msg = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    ws.ping()
                    continue
                if msg.get('type') == 'event':
                    ev = msg.get('event') or {}
                    fn = handlers.get(ev.get('event_type'))
                    if fn:
                        try:
                            fn(ev.get('data') or {})
                        except Exception as e:  # noqa: BLE001  one bad share must not end the listener
                            log(f"Could not handle {ev.get('event_type')} ({type(e).__name__}: {e})")
        except Exception as e:  # noqa: BLE001
            if not failing:
                log(f'Home Assistant events not reachable ({e}); still trying')
            failing = True
            time.sleep(15)


def slug(name):
    name = re.sub(r"[’'`]", '', name.lower())
    return re.sub(r'[^a-z0-9]+', '_', name).strip('_')


def notify_service_for(app_device_id, cfg=None):
    """notify.mobile_app_<phone> for the phone that sent a share (its app's device id)."""
    if not app_device_id:
        return None
    for dev in ws_call({'type': 'config/device_registry/list'}, cfg) or []:
        if any(i[:2] == ['mobile_app', app_device_id] for i in dev.get('identifiers') or []):
            return 'mobile_app_' + slug(dev.get('name_by_user') or dev.get('name') or '')
    return None


def notify(service, message, cfg=None, title=None, data=None):
    url, token = settings(cfg)
    body = {'message': message}
    if title:
        body['title'] = title
    if data:
        body['data'] = data
    r = requests.post(f"{url}/api/services/notify/{service.removeprefix('notify.')}", headers=_headers(token),
                      json=body, timeout=TIMEOUT)
    r.raise_for_status()
