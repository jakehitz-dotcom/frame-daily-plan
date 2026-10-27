import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
import ha
import web

TOKEN = 'good-token'
STATES = [{'entity_id': 'sensor.outdoor_temperature', 'state': '18.5', 'attributes': {}},
          {'entity_id': 'lock.front_door', 'state': 'locked', 'attributes': {}}]


class FakeHA(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != f'Bearer {TOKEN}':
            self.send_response(401)
            self.end_headers()
            return
        body = {'/api/config': {'location_name': 'Home', 'version': '2026.10.1'},
                '/api/states': STATES,
                '/api/states/lock.front_door': STATES[1]}.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if body:
            self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


def fake_server():
    srv = HTTPServer(('127.0.0.1', 0), FakeHA)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{srv.server_port}'


def test_check_state_and_domain_filter():
    cfg = {'home_assistant': {'url': fake_server() + '/', 'token': TOKEN}}
    assert ha.check(cfg) == 'ok · Home · version 2026.10.1 · 2 entities'
    assert ha.state('lock.front_door', cfg)['state'] == 'locked'
    assert [s['entity_id'] for s in ha.states(cfg, 'sensor')] == ['sensor.outdoor_temperature']


def test_bad_token_says_so():
    cfg = {'home_assistant': {'url': fake_server(), 'token': 'wrong'}}
    try:
        ha.check(cfg)
        assert False, 'should have failed'
    except RuntimeError as e:
        assert '401' in str(e)


def test_test_button_saves_and_keeps_token_when_left_empty():
    url = fake_server()
    c = web.app.test_client()
    r = c.post('/ha-test', data={'ha_url': url, 'ha_token': TOKEN})
    assert r.status_code == 302 and 'ok' in r.headers['Location']
    c.post('/ha-test', data={'ha_url': url, 'ha_token': ''})  # empty field: keep the saved token
    saved = config.load()['home_assistant']
    assert (saved['url'], saved['token']) == (url, TOKEN) and saved['entities']['thermostat']  # entity list kept
    assert TOKEN not in c.get('/').get_data(as_text=True)  # never sent back to the browser


def _snap(**over):
    """States shaped like Jake's real entities (thermostat, Home Connect, spa, forecast helpers)."""
    s = {
        'climate.den_thermostat': {'entity_id': 'climate.den_thermostat', 'state': 'heat', 'attributes': {
            'current_temperature': 72.4, 'temperature': 74, 'target_temp_high': None, 'target_temp_low': None}},
        'sensor.back_patio_time_machine_current_temperature': {'state': '95.0', 'attributes': {}},
        'input_boolean.dishwasher_clean': {'state': 'on', 'attributes': {}},
        'input_number.rolling_hills_max_wind_7_day': {'state': '13.17', 'attributes': {}},
        'input_number.rolling_hills_rain_7_day': {'state': '0.55', 'attributes': {}},
    }
    s.update(over)
    return s


def test_home_reads_every_item(monkeypatch):
    import plan_data
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap())
    home, week = plan_data.home(config.load())
    assert home == {'inside': 72, 'set': {'mode': 'heat', 'temps': [74]}, 'hot_tub': 95, 'dishwasher_clean': True}
    assert week == {'wind': 13, 'wind_alert': False, 'rain': 0.55, 'rain_alert': True}


def test_home_hides_what_should_not_show(monkeypatch):
    import plan_data
    off = {'state': 'off', 'attributes': {'current_temperature': 70, 'temperature': None}}
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap(**{
        'climate.den_thermostat': off,                                          # system off: no set temperature
        'input_boolean.dishwasher_clean': {'state': 'off', 'attributes': {}},   # door opened: nothing shown
        'sensor.back_patio_time_machine_current_temperature': {'state': 'unavailable', 'attributes': {}}}))
    home, _ = plan_data.home(config.load())
    assert home == {'inside': 70}


def test_auto_mode_shows_both_set_points(monkeypatch):
    import plan_data
    auto = {'state': 'heat_cool', 'attributes': {'current_temperature': 71, 'target_temp_low': 68, 'target_temp_high': 76}}
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap(**{'climate.den_thermostat': auto}))
    assert plan_data.home(config.load())[0]['set'] == {'mode': 'heat_cool', 'temps': [68, 76]}


def test_plan_survives_home_assistant_being_down(monkeypatch):
    import datetime
    import zoneinfo
    import plan_data
    def down(cfg):
        raise RuntimeError('connection refused')
    monkeypatch.setattr(ha, 'snapshot', down)
    cfg = config.load()
    cfg['home_assistant'].update(url='http://192.0.2.1:8123', token='t')
    cfg['weather']['enabled'] = False
    data = plan_data.build(cfg, datetime.datetime(2026, 10, 27, 8, tzinfo=zoneinfo.ZoneInfo('America/Los_Angeles')))
    assert data['home'] == {} and any('Home Assistant' in e for e in data['errors'])


def test_render_with_and_without_home(tmp_path):
    import datetime
    import plan_data
    import render
    d = plan_data.demo(config.load(), datetime.datetime(2026, 10, 27, 8), 'en')
    render.render(d, str(tmp_path / 'with.jpg'))
    render.render(dict(d, home={}, week={}), str(tmp_path / 'without.jpg'))
    assert (tmp_path / 'with.jpg').stat().st_size > 100_000 and (tmp_path / 'without.jpg').stat().st_size > 100_000


def test_last_bottle_time_and_elapsed(monkeypatch):
    import datetime
    import zoneinfo
    import plan_data
    la = zoneinfo.ZoneInfo('America/Los_Angeles')
    bottle = {'state': '2026-10-27T16:38:53+00:00', 'attributes': {'amount': 170, 'units': 'ml'}}  # Huckleberry, UTC
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap(**{'sensor.finn_bottle': bottle}))
    cfg = config.load()
    home, _ = plan_data.home(cfg, datetime.datetime(2026, 10, 27, 11, 32, 30, tzinfo=la))
    assert home['bottle'] == {'at': '9:38 AM', 'ago': '1h 53m'}
    home, _ = plan_data.home(cfg, datetime.datetime(2026, 10, 27, 10, 5, tzinfo=la))
    assert home['bottle']['ago'] == '26m'
    home, _ = plan_data.home(cfg, datetime.datetime(2026, 10, 28, 12, 0, tzinfo=la))
    assert 'bottle' not in home                                 # over a day old: stale, not shown
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap(**{'sensor.finn_bottle': {'state': 'unavailable'}}))
    assert 'bottle' not in plan_data.home(cfg, datetime.datetime(2026, 10, 27, 11, 0, tzinfo=la))[0]


def test_google_tasks_via_home_assistant(monkeypatch):
    import datetime
    import zoneinfo
    import plan_data
    seen = {}

    def items(entity_ids, cfg):
        seen['lists'] = entity_ids
        return {'todo.my_tasks': [
            {'summary': 'Renew registration', 'due': '2026-10-20', 'done': False, 'completed': None},  # newest on top
            {'summary': 'Call the plumber', 'due': '2026-10-27', 'done': False, 'completed': None},
            {'summary': 'Book flights', 'due': None, 'done': False, 'completed': None},
            {'summary': 'Plan the party', 'due': '2026-11-03', 'done': False, 'completed': None},       # later: waits
            {'summary': 'Unload the dishwasher', 'due': None, 'done': True, 'completed': '2026-10-27T16:05:00+00:00'},
            {'summary': 'Birthday cards', 'due': None, 'done': True, 'completed': '2026-10-27T17:30:00+00:00'},
            {'summary': 'Pay rent', 'due': None, 'done': True, 'completed': '2026-10-26T18:00:00+00:00'}]}  # yesterday
    monkeypatch.setattr(ha, 'todo_items', items)
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: {})
    cfg = config.load()
    cfg['home_assistant'].update(url='http://ha', token='t', todo_lists=['todo.my_tasks'])
    cfg['weather']['enabled'] = False
    data = plan_data.build(cfg, datetime.datetime(2026, 10, 27, 8, tzinfo=zoneinfo.ZoneInfo('America/Los_Angeles')))
    assert seen['lists'] == ['todo.my_tasks']
    assert data['todos'] == ['Renew registration', 'Call the plumber', 'Book flights']   # the list's own order
    assert data['todos_done'] == ['Birthday cards', 'Unload the dishwasher']            # today, latest first


def test_todo_items_reads_the_service_response(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {'changed_states': [], 'service_response': {'todo.my_tasks': {'items': [
                {'summary': 'Book flights', 'uid': '1', 'status': 'needs_action'},
                {'summary': 'Call the plumber', 'uid': '2', 'status': 'needs_action', 'due': '2026-10-27'},
                {'summary': 'Charge the car', 'uid': '3', 'status': 'completed', 'completed': '2026-10-27T17:00:00+00:00'}]}}}
    sent = {}
    monkeypatch.setattr(ha.requests, 'post', lambda url, **kw: sent.update(url=url, **kw) or R())
    out = ha.todo_items(['todo.my_tasks'], {'home_assistant': {'url': 'http://ha:8123', 'token': 't'}})
    assert sent['url'] == 'http://ha:8123/api/services/todo/get_items?return_response'
    assert sent['json'] == {'entity_id': ['todo.my_tasks']}
    assert out == {'todo.my_tasks': [
        {'summary': 'Book flights', 'due': None, 'done': False, 'completed': None, 'uid': '1'},
        {'summary': 'Call the plumber', 'due': '2026-10-27', 'done': False, 'completed': None, 'uid': '2'},
        {'summary': 'Charge the car', 'due': None, 'done': True, 'completed': '2026-10-27T17:00:00+00:00', 'uid': '3'}]}


def test_weather_panel_uses_ktoa_now_and_the_nws_outlook(monkeypatch):
    import datetime
    import zoneinfo
    import nws
    import plan_data
    monkeypatch.setattr(ha, 'snapshot', lambda cfg: _snap())
    monkeypatch.setattr(plan_data, 'weather', lambda cfg, day, w: {'icon': '☁️', 'max': 73, 'min': 56, 'text': 'Cloudy',
                                                                 'rain': 10, 'uv': {'max': 7}, 'hint': 'T-shirt'})
    monkeypatch.setattr(nws, 'now', lambda stations: {'station': stations[0], 'text': 'Partly cloudy', 'icon': '⛅', 'temp_f': 66})
    monkeypatch.setattr(nws, 'outlook', lambda lat, lon, today, tz: 'Rain Sat night–Sun (70%)')
    cfg = config.load()
    cfg['home_assistant'].update(url='http://ha', token='t')
    cfg['weather'].update(enabled=True, lat=33.77, lon=-118.36)
    la = zoneinfo.ZoneInfo('America/Los_Angeles')
    wx = plan_data.build(cfg, datetime.datetime(2026, 10, 27, 12, tzinfo=la))['weather']
    assert (wx['icon'], wx['text'], wx['now'], wx['station']) == ('⛅', 'Partly cloudy now', 66, 'KTOA')
    assert wx['outlook'] == 'Rain Sat night–Sun (70%)' and (wx['max'], wx['min']) == (73, 56)

    def down(*a, **k):
        raise OSError('api.weather.gov unreachable')
    monkeypatch.setattr(nws, 'now', down)
    monkeypatch.setattr(nws, 'outlook', down)
    data = plan_data.build(cfg, datetime.datetime(2026, 10, 27, 12, tzinfo=la))
    assert data['weather']['text'] == 'Cloudy' and 'outlook' not in data['weather']   # forecast still shown
    assert any('NWS' in e for e in data['errors'])


def test_task_list_by_name_or_entity_and_unknown_ones_reported(monkeypatch):
    lists = [{'entity_id': 'todo.jake_hitzeroth_s_list', 'state': '2', 'attributes': {'friendly_name': "Jake hitzeroth's list"}},
             {'entity_id': 'todo.shopping_list', 'state': '0', 'attributes': {'friendly_name': 'Shopping List'}}]
    monkeypatch.setattr(ha, 'states', lambda cfg=None, domain=None: lists)
    assert ha.resolve_todo_lists(["Jake hitzeroth's list", 'shopping list']) == (
        ['todo.jake_hitzeroth_s_list', 'todo.shopping_list'], [])
    assert ha.resolve_todo_lists(['Groceries', 'todo.shopping_list']) == (['todo.shopping_list'], ['Groceries'])
    assert ha.resolve_todo_lists(['todo.jake_hitzeroth_s_list']) == (['todo.jake_hitzeroth_s_list'], [])


def test_connection_test_names_the_to_do_lists():
    cfg = {'home_assistant': {'url': fake_server(), 'token': TOKEN}}
    STATES.append({'entity_id': 'todo.jake_hitzeroth_s_list', 'state': '2', 'attributes': {}})
    try:
        assert ha.check(cfg).endswith('to-do lists: todo.jake_hitzeroth_s_list')
    finally:
        STATES.pop()
