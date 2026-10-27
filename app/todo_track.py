"""Completed tasks for lists that do not report them (Alexa's to-do list drops a task the moment it
is checked off). Every minute the open tasks are compared with the last look: a task that has gone
is recorded as completed at that moment. Kept in the data folder, so restarts lose nothing.
Limitation: a deleted task looks the same as a completed one."""
import datetime
import json
import os

import config
import ha

FILE = os.path.join(config.DATA, 'todo-track.json')
KEEP = datetime.timedelta(days=2)


def _load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'seen': {}, 'done': []}


def _save(data):
    tmp = FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, FILE)


def poll(cfg, now=None):
    """One look at the configured lists. Returns the tasks newly recorded as completed."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lists = (cfg.get('home_assistant') or {}).get('todo_lists') or []
    if not lists or not ha.configured(cfg):
        return []
    ids, _ = ha.resolve_todo_lists(lists, cfg)
    live = [e for e in ids if (ha.state(e, cfg).get('state') or 'unavailable') not in ('unavailable', 'unknown')]
    if not live:
        return []  # integration reconnecting: an empty answer now would look like everything was completed
    items = ha.todo_items(live, cfg)
    data = _load()
    newly = []
    for entity in live:
        current = {i.get('uid') or i['summary']: i['summary'] for i in items.get(entity, []) if not i['done']}
        before = data['seen'].get(entity)
        if before is not None:  # the first look only sets the baseline
            for key, summary in before.items():
                if key not in current:
                    data['done'].append({'summary': summary, 'at': now.isoformat(), 'list': entity})
                    newly.append(summary)
        reopened = set(current.values())
        data['done'] = [d for d in data['done'] if not (d.get('list') == entity and d['summary'] in reopened)]
        data['seen'][entity] = current
    data['done'] = [d for d in data['done'] if now - datetime.datetime.fromisoformat(d['at']) < KEEP]
    _save(data)
    return newly


def done_today(today, tz):
    """[(completed at, summary)] recorded today (local time)."""
    out = []
    for d in _load()['done']:
        at = datetime.datetime.fromisoformat(d['at']).astimezone(tz)
        if at.date() == today:
            out.append((at, d['summary']))
    return out
