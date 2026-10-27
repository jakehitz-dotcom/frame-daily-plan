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
