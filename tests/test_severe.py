import datetime
import zoneinfo

from PIL import Image

import jobs
import plan_data
import render
import severe

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
CFG = {'timezone': 'America/Los_Angeles', 'plan_lang': 'en', 'calendars': [], 'tv_ip': '192.168.1.50',
       'weather': {'enabled': True, 'lat': 33.96, 'lon': -117.56, 'name': 'Eastvale, CA'},
       'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4], 'refresh_minutes': 5}}
NOW = datetime.datetime(2026, 10, 27, 13, 7, tzinfo=TZ)   # Tuesday afternoon, outside the planner window


def om(rain=0.0, gust=12.0, wet_from=None):
    """An Open-Meteo answer: 48 hours from midnight, 15-minute rain from noon."""
    start = datetime.datetime(2026, 10, 27, 0, 0)
    hours = [start + datetime.timedelta(hours=i) for i in range(48)]
    quarters = [datetime.datetime(2026, 10, 27, 12, 0) + datetime.timedelta(minutes=15 * i) for i in range(96)]
    return {'current': {'temperature_2m': 61.0, 'apparent_temperature': 57.0, 'wind_speed_10m': 14.0,
                        'wind_gusts_10m': 27.0, 'wind_direction_10m': 225},
            'hourly': {'time': [t.isoformat(timespec='minutes') for t in hours],
                       'precipitation_probability': [70] * 48, 'precipitation': [rain] * 48,
                       'wind_speed_10m': [10.0] * 48, 'wind_gusts_10m': [gust] * 48,
                       'wind_direction_10m': [200] * 48, 'temperature_2m': [60.0] * 48},
            'minutely_15': {'time': [t.isoformat(timespec='minutes') for t in quarters],
                            'precipitation': [0.02 if wet_from is not None and i >= wet_from else 0.0
                                              for i in range(96)]}}


def gathered(o, alerts=()):
    hours = severe._hours_from_open_meteo(o, NOW)
    return {'now': NOW, 'hours': hours, 'alerts': list(alerts), 'eta': severe.rain_eta(o, hours, NOW),
            'current_om': o['current'], 'om': o, 'errors': []}


def test_hours_start_with_the_current_hour_and_cover_a_day():
    hours = severe._hours_from_open_meteo(om(), NOW)
    assert len(hours) == 24 and hours[0]['t'].hour == 13 and hours[0]['t'].tzinfo == TZ


def test_triggers():
    calm = severe.evaluate(CFG, gathered(om(rain=0.01, gust=20)))
    assert not calm['active'] and calm['reasons'] == []
    rain = severe.evaluate(CFG, gathered(om(rain=0.02)))          # 24 x 0.02 = 0.48 in
    assert rain['active'] and rain['reasons'] == ['0.48 in rain / 24 h'] and rain['signature'] == 'rain'
    wind = severe.evaluate(CFG, gathered(om(gust=31)))
    assert wind['active'] and wind['reasons'][0].startswith('Gusts 31 mph')
    alert = {'event': 'Wind Advisory', 'severity': 'Moderate', 'headline': '', 'summary': '', 'ends': None}
    adv = severe.evaluate(CFG, gathered(om(), [alert]))
    assert adv['active'] and adv['reasons'] == ['Wind Advisory'] and adv['signature'] == 'alert:Wind Advisory'
    custom = dict(CFG, severe={'gust_mph': 35})
    assert not severe.evaluate(custom, gathered(om(gust=31)))['active']   # thresholds are settings


def test_minutes_until_rain():
    soon = severe.rain_eta(om(wet_from=6), [], NOW)                       # 13:30 slot is the first wet one
    assert soon['state'] == 'soon' and soon['minutes'] == 23 and len(soon['strip']) == 12
    now = severe.rain_eta(om(wet_from=0), [], NOW)
    assert now['state'] == 'now'
    dry = om()
    hours = severe._hours_from_open_meteo(dict(dry, hourly=dict(dry['hourly'], precipitation=[0.0] * 18 + [0.05] * 30)), NOW)
    later = severe.rain_eta(dry, hours, NOW)                              # beyond the 15-minute data: hourly
    assert later['state'] == 'later' and later['at'].hour == 18
    assert severe.rain_eta(None, [], NOW)['state'] == 'none'


def test_refresh_times_moves_the_window_forward():
    g = gathered(om(wet_from=6))
    later = severe.refresh_times(g, NOW + datetime.timedelta(minutes=10))
    assert later['eta']['minutes'] == 13 and later['hours'][0]['t'].hour == 13


def test_quiet_hours():
    at = lambda h: NOW.replace(hour=h, minute=0)  # noqa: E731
    assert severe.in_quiet_hours(CFG, at(23)) and severe.in_quiet_hours(CFG, at(3))
    assert not severe.in_quiet_hours(CFG, at(6)) and not severe.in_quiet_hours(CFG, at(21))


SEV = {'active': True, 'signature': 'gust', 'reasons': ['Gusts 31 mph 8 pm']}


def test_due_shows_severe_outside_the_planner_windows_only():
    t = NOW.timestamp()
    assert jobs._due(CFG, {}, NOW, SEV) == 'severe'
    assert jobs._due(CFG, {}, NOW, dict(SEV, active=False)) is None
    showing = {'showing': 'severe', 'shown_at': t}
    assert jobs._due(CFG, showing, NOW + datetime.timedelta(minutes=2), SEV) is None
    assert jobs._due(CFG, showing, NOW + datetime.timedelta(minutes=5), SEV) == 'severe_refresh'
    assert jobs._due(CFG, showing, NOW, dict(SEV, active=False)) == 'restore'           # conditions cleared
    assert jobs._due(CFG, {'severe_dismissed': 'gust'}, NOW, SEV) is None                 # hidden: stays away
    assert jobs._due(CFG, {'severe_dismissed': 'gust'}, NOW, dict(SEV, signature='alert:Wind Advisory|gust')) == 'severe'
    morning = NOW.replace(hour=8)                                                         # the planner wins
    assert jobs._due(CFG, {}, morning, SEV) == 'show'
    assert jobs._due(CFG, {'showing': 'severe', 'shown_on': '2026-10-27', 'shown_view': 'day'}, morning, SEV) == 'show'


def test_due_voice_request_lasts_its_time_even_without_triggers():
    t = NOW.timestamp()
    state = {'showing': 'severe', 'shown_at': t, 'until': t + 1800}
    assert jobs._due(CFG, state, NOW + datetime.timedelta(minutes=10)) == 'severe_refresh'
    assert jobs._due(CFG, state, NOW + datetime.timedelta(minutes=31)) == 'restore'


def _tv(monkeypatch, sev, awake=False):
    calls = {'wake': 0, 'severe': []}
    up = [awake]
    monkeypatch.setattr(severe, 'status', lambda cfg, now=None: sev)
    monkeypatch.setattr(jobs.tv, 'awake', lambda ip: up[0])

    def wake(ip, mac, wait=60):
        calls['wake'] += 1
        up[0] = True
        return True
    monkeypatch.setattr(jobs.tv, 'wake', wake)
    monkeypatch.setattr(jobs, 'show_severe', lambda cfg, refresh=False, woke=False, until=None: (
        calls['severe'].append((refresh, woke)), jobs.save_state(dict(jobs.load_state(), showing='severe',
                                                                       shown_at=NOW.timestamp()))))
    return calls, up


def test_tick_wakes_once_per_episode(monkeypatch):
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    calls, up = _tv(monkeypatch, SEV)
    cfg = dict(CFG, wake_tv=True)
    assert jobs.tick(cfg, NOW, {}) == 'severe' and calls['wake'] == 1 and calls['severe'] == [(False, True)]
    jobs.save_state(dict(jobs.load_state(), showing=None))     # the TV put something else up
    up[0] = False
    assert jobs.tick(cfg, NOW + datetime.timedelta(minutes=1), {}) is None and calls['wake'] == 1   # no second wake


def test_tick_does_not_wake_in_quiet_hours(monkeypatch):
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    calls, _ = _tv(monkeypatch, SEV)
    assert jobs.tick(dict(CFG, wake_tv=True), NOW.replace(hour=23), {}) is None and calls['wake'] == 0


def test_dismissal_rearms_when_conditions_clear(monkeypatch):
    jobs.save_state({'plan_ids': [], 'severe_dismissed': 'gust'})
    _tv(monkeypatch, dict(SEV, active=False), awake=True)
    jobs.tick(CFG, NOW, {})
    assert jobs.load_state().get('severe_dismissed') is None


def test_planner_notice(monkeypatch):
    monkeypatch.setattr(severe, 'status', lambda cfg, now=None: dict(SEV, reasons=['Wind Advisory', 'Gusts 31 mph 8 pm']))
    wx = {'icon': '🌧️', 'text': 'Rain', 'max': 64, 'min': 52, 'now': 58, 'severe': 'Wind Advisory  ·  Gusts 31 mph 8 pm'}
    im = Image.new('RGBA', (1200, 1400), (20, 20, 20, 255))
    d = render.ImageDraw.Draw(im)
    tall = render.weather_panel(None, d, wx, None, 0, 1100, 0)
    short = render.weather_panel(None, d, {k: v for k, v in wx.items() if k != 'severe'}, None, 0, 1100, 0)
    assert tall > short                                    # measured, so the layout makes room for it
    render.weather_panel(im, d, wx, None, 0, 1100, 0)


def test_render_severe_screen(tmp_path):
    g = gathered(om(rain=0.03, gust=34, wet_from=4),
                 [{'event': 'Wind Advisory', 'severity': 'Moderate', 'headline': 'Wind Advisory until 6 AM',
                   'summary': '', 'ends': NOW + datetime.timedelta(hours=12)}])
    ev = severe.evaluate(CFG, g)
    sv = {'settings': severe.settings(CFG), 'active': True, 'reasons': ev['reasons'], 'place': 'Eastvale',
          'updated': '1:07 PM', 'alerts': g['alerts'], 'hours': g['hours'], 'eta': g['eta'], 'rain_24': ev['rain_24'],
          'gust_max': ev['gust_max'], 'gust_at': ev['gust_at'],
          'current': {'now': 61, 'max': 64, 'min': 52, 'icon': '🌧️', 'text': 'Scattered Rain Showers And Patchy Fog', 'feels': 57, 'pop': 70,
                      'wind_text': '14 G 27 mph SW'},
          'radar': {'image': None, 'source': None, 'time': None, 'm_per_px': 200}}
    out = tmp_path / 'severe.jpg'
    render.render_severe(sv, str(out))
    assert Image.open(out).size == (3840, 2160)
