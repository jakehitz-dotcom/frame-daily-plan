import datetime
import zoneinfo

import config
import ha
import todo_track

LA = zoneinfo.ZoneInfo('America/Los_Angeles')
ALEXA = 'todo.jakehitz_yahoo_com_to_do_list'


def setup(monkeypatch, lists, state='1'):
    monkeypatch.setattr(ha, 'configured', lambda cfg: True)
    monkeypatch.setattr(ha, 'state', lambda e, cfg: {'state': state[0] if isinstance(state, list) else state})
    monkeypatch.setattr(ha, 'todo_items', lambda ids, cfg: {ALEXA: [
        {'summary': s, 'uid': u, 'done': False, 'due': None, 'completed': None} for u, s in lists[0]]})
    cfg = config.load()
    cfg['home_assistant'].update(url='http://ha', token='t', todo_lists=[ALEXA])
    return cfg


def at(h, m=0, day=27):
    return datetime.datetime(2026, 10, day, h, m, tzinfo=LA)


def test_checked_off_alexa_task_counts_as_completed_today(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_track, 'FILE', str(tmp_path / 't.json'))
    lists = [[('a', 'Test the planner'), ('b', 'Call the plumber')]]
    cfg = setup(monkeypatch, lists)
    assert todo_track.poll(cfg, at(14, 20)) == []                         # baseline only
    lists[0] = [('b', 'Call the plumber')]                                # "test the planner" checked off
    assert todo_track.poll(cfg, at(14, 21)) == ['Test the planner']
    assert todo_track.done_today(datetime.date(2026, 10, 27), LA) == [(at(14, 21), 'Test the planner')]
    assert todo_track.done_today(datetime.date(2026, 10, 28), LA) == []   # gone tomorrow


def test_unchecked_task_is_open_again_and_outages_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_track, 'FILE', str(tmp_path / 't.json'))
    lists = [[('a', 'Water the plants')]]
    state = ['1']
    cfg = setup(monkeypatch, lists, state)
    todo_track.poll(cfg, at(9))
    lists[0] = []
    todo_track.poll(cfg, at(9, 1))
    lists[0] = [('a', 'Water the plants')]                                # un-checked in the Alexa app
    todo_track.poll(cfg, at(9, 2))
    assert todo_track.done_today(datetime.date(2026, 10, 27), LA) == []
    state[0] = 'unavailable'                                              # integration reconnecting
    lists[0] = []
    assert todo_track.poll(cfg, at(9, 3)) == []
    state[0] = '1'
    lists[0] = [('a', 'Water the plants')]
    assert todo_track.poll(cfg, at(9, 4)) == []                           # nothing falsely completed


def test_plan_shows_tracked_completions(monkeypatch, tmp_path):
    import plan_data
    monkeypatch.setattr(todo_track, 'FILE', str(tmp_path / 't.json'))
    lists = [[('a', 'Test the planner'), ('b', 'Call the plumber')]]
    cfg = setup(monkeypatch, lists)
    todo_track.poll(cfg, at(14, 20))
    lists[0] = [('b', 'Call the plumber')]
    todo_track.poll(cfg, at(14, 21))
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: {})
    cfg['weather']['enabled'] = False
    data = plan_data.build(cfg, at(14, 25))
    assert data['todos'] == ['Call the plumber'] and data['todos_done'] == ['Test the planner']
