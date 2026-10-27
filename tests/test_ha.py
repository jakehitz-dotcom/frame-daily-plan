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
    assert config.load()['home_assistant'] == {'url': url, 'token': TOKEN}
    assert TOKEN not in c.get('/').get_data(as_text=True)  # never sent back to the browser
