import config
import web


def client():
    return web.app.test_client()


def test_fresh_install_shows_setup():
    html = client().get('/').get_data(as_text=True)
    assert 'Getting started' in html and 'Search the network' in html


def test_language_toggle():
    c = client()
    c.get('/lang/de')
    assert 'Erste Schritte' in c.get('/').get_data(as_text=True)
    c.get('/lang/en')
    assert config.load()['ui_lang'] == 'en'


def test_set_place_fills_location_timezone_and_holidays():
    r = client().post('/set-place', data={'label': 'Munich, Bavaria, Germany', 'lat': '48.137', 'lon': '11.575',
                                         'timezone': 'Europe/Berlin', 'country_code': 'DE'})
    assert r.status_code == 302
    cfg = config.load()
    assert cfg['weather']['lat'] == 48.137 and cfg['timezone'] == 'Europe/Berlin' and cfg['holidays']['country'] == 'DE'


def test_regions_and_health():
    assert 'BY' in client().get('/regions/de').get_json()['regions']
    assert client().get('/health').get_json() == {'ok': True}


def test_bad_timezone_is_ignored():
    c = client()
    c.post('/set-place', data={'label': 'X', 'lat': '1', 'lon': '2', 'timezone': 'Not/AZone', 'country_code': ''})
    assert config.load()['timezone'] != 'Not/AZone'


def test_todo_hint_setting_saves_and_reaches_the_plan():
    import datetime
    import zoneinfo
    import plan_data
    cfg = config.load()
    assert cfg['todo_hint'] == 'Alexa, add [task] to my to-do list | Alexa, mark [task] as complete'
    cfg['weather']['enabled'] = False
    data = plan_data.build(cfg, datetime.datetime(2026, 10, 27, 8, tzinfo=zoneinfo.ZoneInfo('Europe/London')))
    assert data['todo_hint'] == cfg['todo_hint']


def test_old_saved_hint_is_replaced_by_the_new_default():
    cfg = config.load()
    cfg['todo_hint'] = 'Alexa, add to my to-do list: [task] · or check it off in the app'
    config.save(cfg)
    assert config.load()['todo_hint'] == 'Alexa, add [task] to my to-do list | Alexa, mark [task] as complete'
    cfg['todo_hint'] = 'My own words | second line'
    config.save(cfg)
    assert config.load()['todo_hint'] == 'My own words | second line'      # a custom hint is kept
