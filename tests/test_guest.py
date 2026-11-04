import time

import guest
import jobs
import render
import web

RECIPE = 'Pancakes\n\nIngredients:\n1 cup flour\n1 egg\n\nDirections:\nMix.\nCook.\n'


def client():
    return web.app.test_client()


def test_address_is_learned_and_upper_case():
    guest.learn_base('http://localhost:8080/')            # ignored: phones can't reach it
    guest.learn_base('http://192.168.0.20:8080/')
    url = guest.url({})
    assert url == url.upper() and url.startswith('HTTP://192.168.0.20:8080/G/') and len(url.split('/')[-1]) == 6
    assert guest.url({'guest': {'enabled': False}}) is None
    assert guest.url({'guest': {'url': 'http://frame.home:8080'}}).startswith('HTTP://FRAME.HOME:8080/G/')


def test_rotate_retires_the_old_code():
    old = guest.token()
    new = guest.rotate()
    assert new != old and guest.check(new.lower()) and not guest.check(old)


def test_qr_is_compact_and_crisp():
    tile = render.qr_image('HTTP://192.168.0.20:8080/G/ABC234', 196)
    assert 150 <= tile.width <= 196 and tile.width == tile.height   # version 2: 25 squares + margin, whole pixels


def test_wrong_code_is_refused():
    assert client().get('/G/NOPE99').status_code == 404
    assert client().post('/G/NOPE99/show', data={'text': RECIPE}).status_code == 404


def test_guest_shows_a_recipe_and_takes_it_down(monkeypatch):
    tok = guest.token()
    shown, ended = [], []
    monkeypatch.setattr(jobs, 'show_recipe', lambda cfg, rec, notify=None, **k: (
        shown.append((rec['name'], notify)),
        jobs.save_state(dict(jobs.load_state(), showing='recipe', until=time.time() + 600))))
    monkeypatch.setattr(jobs, 'screen_mode', lambda cfg, mode, minutes=30: ended.append(mode))
    web._guest_last[0] = 0
    page = client().get(f'/G/{tok}').get_data(as_text=True)
    assert 'Frame Remote' in page and 'Show recipe on TV' in page
    r = client().post(f'/G/{tok}/show', data={'text': RECIPE})
    assert r.status_code == 302 and shown == [('Pancakes', False)]   # nobody's phone is notified
    # A second guest is asked before replacing what's on the TV.
    web._guest_last[0] = 0
    ask = client().post(f'/g/{tok.lower()}/show', data={'text': RECIPE}).get_data(as_text=True)
    assert 'Replace it with yours?' in ask and len(shown) == 1
    assert client().post(f'/G/{tok}/hide').status_code == 302 and ended == ['art']


def test_too_fast_and_not_a_recipe():
    tok = guest.token()
    web._guest_last[0] = time.time()
    assert 'One moment' in client().post(f'/G/{tok}/show', data={'text': RECIPE}).get_data(as_text=True)
    web._guest_last[0] = 0
    jobs.save_state(dict(jobs.load_state(), showing=None))
    assert 'doesn’t look like' in client().post(f'/G/{tok}/show', data={'text': 'hello'}).get_data(as_text=True)


def test_remote_buttons_switch_modes(monkeypatch):
    tok = guest.token()
    chosen = []
    monkeypatch.setattr(jobs, 'screen_mode', lambda cfg, mode, minutes=30: chosen.append(mode))
    jobs.save_state(dict(jobs.load_state(), showing='plan', shown_view='day', manual_until=None))
    page = client().get(f'/G/{tok}').get_data(as_text=True)
    assert 'Next 7 days' in page and 'Weather' in page and 'Artwork' in page
    assert page.count('mode on') == 1                       # the planner is marked "On now"
    web._guest_last[0] = 0
    r = client().post(f'/G/{tok}/mode', data={'mode': 'week'})
    assert r.status_code == 302 and chosen == ['week'] and 'mode-week' in r.headers['Location']
    web._guest_last[0] = 0
    assert 'Unknown' in client().post(f'/G/{tok}/mode', data={'mode': 'nope'}).get_data(as_text=True)
