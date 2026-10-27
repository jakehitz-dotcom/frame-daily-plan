import datetime
import io
import json
import zoneinfo

import nws
import plan_data

LA = zoneinfo.ZoneInfo('America/Los_Angeles')
NOW = datetime.datetime(2026, 10, 27, 19, 0, tzinfo=datetime.timezone.utc)  # noon in LA


def obs(station, text, minutes_old=20, temp_c=19.4, night=False):
    return {'properties': {'timestamp': (NOW - datetime.timedelta(minutes=minutes_old)).isoformat(),
                           'textDescription': text, 'temperature': {'unitCode': 'wmoUnit:degC', 'value': temp_c},
                           'icon': f"https://api.weather.gov/icons/land/{'night' if night else 'day'}/sct?size=medium"}}


def test_ktoa_first_khhr_when_ktoa_is_stale(monkeypatch):
    feeds = {'KTOA': obs('KTOA', 'Partly Cloudy'), 'KHHR': obs('KHHR', 'Clear')}
    monkeypatch.setattr(nws, '_get', lambda url: feeds[url.split('/')[4]])
    assert nws.now(['KTOA', 'KHHR'], NOW) == {'station': 'KTOA', 'text': 'Partly cloudy', 'icon': '⛅', 'temp_f': 67}
    feeds['KTOA'] = obs('KTOA', 'Partly Cloudy', minutes_old=300)   # 5 h old: skip it
    assert nws.now(['KTOA', 'KHHR'], NOW)['station'] == 'KHHR'
    feeds['KHHR'] = obs('KHHR', '', minutes_old=10)                 # no description either
    assert nws.now(['KTOA', 'KHHR'], NOW) is None


def test_sky_icons():
    assert nws.sky_icon('Mostly Clear', night=True) == '🌙'
    assert nws.sky_icon('Light Rain and Fog/Mist') == '🌧️'
    assert nws.sky_icon('Fog/Mist') == '🌫️'
    assert nws.sky_icon('Thunderstorms and Rain') == '⛈️'
    assert nws.sky_icon('Mostly Cloudy') == '🌥️'


def period(name, start, day, short, pop=None, temp=70):
    return {'name': name, 'startTime': start, 'isDaytime': day, 'shortForecast': short, 'temperature': temp,
            'temperatureUnit': 'F', 'probabilityOfPrecipitation': {'unitCode': 'wmoUnit:percent', 'value': pop}}


WEEK = [
    period('This Afternoon', '2026-10-27T12:00:00-07:00', True, 'Sunny', None, 73),
    period('Tonight', '2026-10-27T18:00:00-07:00', False, 'Patchy Fog', None, 57),
    period('Wednesday', '2026-10-28T06:00:00-07:00', True, 'Mostly Sunny', None, 72),
    period('Wednesday Night', '2026-10-28T18:00:00-07:00', False, 'Slight Chance Rain Showers', 15, 56),
    period('Thursday', '2026-10-29T06:00:00-07:00', True, 'Sunny', None, 92),
    period('Thursday Night', '2026-10-29T18:00:00-07:00', False, 'Clear', None, 60),
    period('Friday', '2026-10-30T06:00:00-07:00', True, 'Partly Sunny', None, 75),
    period('Friday Night', '2026-10-30T18:00:00-07:00', False, 'Mostly Cloudy', 10, 58),
    period('Saturday', '2026-10-31T06:00:00-07:00', True, 'Mostly Cloudy', 20, 68),
    period('Saturday Night', '2026-10-31T18:00:00-07:00', False, 'Rain Likely', 60, 55),
    period('Sunday', '2026-11-01T06:00:00-07:00', True, 'Rain', 70, 61),
    period('Sunday Night', '2026-11-01T18:00:00-08:00', False, 'Chance Rain', 40, 52),
    period('Monday', '2026-11-02T06:00:00-08:00', True, 'Sunny', None, 66),
]


def test_outlook_names_the_storm_and_the_heat(monkeypatch):
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: WEEK)
    monkeypatch.setattr(nws, 'alerts', lambda lat, lon: [])
    text = nws.outlook(33.77, -118.36, datetime.date(2026, 10, 27), LA)
    assert text == 'Hot (92°) Thu · Rain Sat night–Sun night (70%)'


def test_outlook_leads_with_an_active_alert_and_says_when_quiet(monkeypatch):
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: WEEK[:3])
    monkeypatch.setattr(nws, 'alerts', lambda lat, lon: [{'event': 'Wind Advisory', 'ends': '2026-10-28T15:00:00-07:00'}])
    assert nws.outlook(33.77, -118.36, datetime.date(2026, 10, 27), LA) == 'Wind Advisory until Wed 3 PM'
    monkeypatch.setattr(nws, 'alerts', lambda lat, lon: [])
    assert nws.outlook(33.77, -118.36, datetime.date(2026, 10, 27), LA) is None   # plan shows "Quiet week ahead"


def test_dress_advice_in_american_english():
    assert plan_data.dress(73, 56, 10, 7) == 'T-shirt + light jacket AM/PM · sunscreen'
    assert plan_data.dress(68, 54, 60, 3) == 'Long sleeves + jacket AM/PM · rain jacket or umbrella'
    assert plan_data.dress(85, 68, 0, 9) == 'Shorts & T-shirt · sunscreen'
    assert plan_data.dress(58, 48, 0, 2) == 'Sweater or light jacket'
    assert 'jumper' not in plan_data.dress(60, 50, 0, 0)


def test_forecast_high_low_uv_window(monkeypatch):
    hours = [f'2026-10-27T{h:02d}:00' for h in range(24)]
    uv = [0] * 8 + [1.5, 3.2, 5.1, 6.4, 7.2, 6.8, 6.1, 4.0, 2.0] + [0] * 7
    body = {'daily': {'weather_code': [3], 'temperature_2m_max': [22.8], 'temperature_2m_min': [13.3],
                      'precipitation_probability_max': [10], 'uv_index_max': [7.25]},
            'hourly': {'time': hours, 'uv_index': uv}}
    monkeypatch.setattr(plan_data.urllib.request, 'urlopen', lambda url, timeout: io.BytesIO(json.dumps(body).encode()))
    cfg = {'weather': {'lat': 33.77, 'lon': -118.36, 'unit': 'F'}, 'timezone': 'America/Los_Angeles', 'plan_lang': 'en'}
    wx = plan_data.weather(cfg, datetime.date(2026, 10, 27), plan_data.words(cfg))
    assert (wx['max'], wx['min']) == (73, 56)
    assert wx['uv'] == {'max': 7, 'from': '11 AM', 'to': '3 PM'}
    assert wx['hint'] == 'T-shirt + light jacket AM/PM · sunscreen'


def grid(now_c=23.9, rows=None):
    """A slice of /gridpoints/LOX/x,y shaped like the real thing (values in °C, ISO intervals)."""
    hours = rows or [('2026-10-27T13:00:00+00:00/PT2H', 14.0), ('2026-10-27T15:00:00+00:00/PT3H', 17.2),
                     ('2026-10-27T18:00:00+00:00/PT3H', 21.1), ('2026-10-27T21:00:00+00:00/PT1H', now_c),
                     ('2026-10-27T22:00:00+00:00/PT2H', 22.8), ('2026-10-28T00:00:00+00:00/PT4H', 18.3),
                     ('2026-10-28T04:00:00+00:00/PT6H', 15.6)]
    return {'properties': {
        'temperature': {'uom': 'wmoUnit:degC', 'values': [{'validTime': t, 'value': v} for t, v in hours]},
        'maxTemperature': {'values': [{'validTime': '2026-10-27T14:00:00+00:00/PT13H', 'value': 24.4}]},
        'minTemperature': {'values': [{'validTime': '2026-10-27T02:00:00+00:00/PT14H', 'value': 13.3}]},
        'probabilityOfPrecipitation': {'values': [{'validTime': '2026-10-27T13:00:00+00:00/PT11H', 'value': 5},
                                                  {'validTime': '2026-10-28T00:00:00+00:00/PT6H', 'value': 20}]}}}


def fake_point(monkeypatch, grid_body, hourly_text='Sunny', day=True):
    hourly = {'properties': {'periods': [{'startTime': '2026-10-27T14:00:00-07:00', 'endTime': '2026-10-27T15:00:00-07:00',
                                          'temperature': 75, 'temperatureUnit': 'F', 'isDaytime': day,
                                          'shortForecast': hourly_text}]}}
    monkeypatch.setattr(nws, 'point', lambda lat, lon: {'forecastGridData': 'grid', 'forecastHourly': 'hourly', 'forecast': 'fc'})
    monkeypatch.setattr(nws, '_cached', lambda url: {'grid': grid_body, 'hourly': hourly}[url])


def test_grid_cell_now_high_low_agree(monkeypatch):
    fake_point(monkeypatch, grid())
    at = datetime.datetime(2026, 10, 27, 14, 30, tzinfo=LA)                   # 21:30 UTC
    g = nws.local(33.77, -118.36, at, LA)
    assert g == {'now_f': 75, 'hi_f': 76, 'lo_f': 56, 'pop': 20, 'text': 'Sunny', 'icon': '☀️'}
    assert g['lo_f'] <= g['now_f'] <= g['hi_f']


def test_weather_panel_is_local_and_consistent(monkeypatch):
    fake_point(monkeypatch, grid(now_c=25.0))                                 # 77°F now, grid max 76°F
    cfg = {'weather': {'lat': 33.77, 'lon': -118.36, 'unit': 'F'}, 'timezone': 'America/Los_Angeles', 'plan_lang': 'en',
           'home_assistant': {'url': '', 'token': '', 'entities': {}}}
    data = {'weather': {'icon': '☁️', 'max': 67, 'min': 58, 'text': 'Cloudy', 'rain': 0, 'uv': {'max': 7}, 'hint': ''},
            'errors': []}
    plan_data.local_weather(cfg, data, datetime.datetime(2026, 10, 27, 14, 30, tzinfo=LA), LA, plan_data.words(cfg))
    wx = data['weather']
    assert (wx['now'], wx['max'], wx['min'], wx['source']) == (77, 77, 56, 'grid')     # high lifted to now
    assert wx['text'] == 'Sunny now' and wx['icon'] == '☀️'
    assert wx['hint'] == 'T-shirt + light jacket AM/PM · sunscreen'


def test_outdoor_sensor_wins_for_now(monkeypatch):
    import ha
    fake_point(monkeypatch, grid())
    monkeypatch.setattr(ha, 'configured', lambda cfg: True)
    monkeypatch.setattr(ha, 'state', lambda e, cfg: {'state': '71.6', 'attributes': {'unit_of_measurement': '°F'}})
    cfg = {'weather': {'lat': 33.77, 'lon': -118.36, 'unit': 'F'}, 'timezone': 'America/Los_Angeles', 'plan_lang': 'en',
           'home_assistant': {'url': 'http://ha', 'token': 't', 'entities': {'outdoor_temp': 'sensor.backyard_temperature'}}}
    data = {'weather': {'icon': '☁️', 'max': 67, 'min': 58, 'text': 'Cloudy', 'rain': 0, 'uv': {'max': 3}, 'hint': ''},
            'errors': []}
    plan_data.local_weather(cfg, data, datetime.datetime(2026, 10, 27, 14, 30, tzinfo=LA), LA, plan_data.words(cfg))
    assert (data['weather']['now'], data['weather']['source'], data['weather']['max']) == (72, 'sensor', 76)


def test_grid_down_falls_back_to_airport_and_still_agrees(monkeypatch):
    def down(*a):
        raise OSError('api.weather.gov unreachable')
    monkeypatch.setattr(nws, 'local', down)
    monkeypatch.setattr(nws, 'now', lambda stations: {'station': 'KTOA', 'text': 'Clear', 'icon': '☀️', 'temp_f': 75})
    cfg = {'weather': {'lat': 33.77, 'lon': -118.36, 'unit': 'F'}, 'timezone': 'America/Los_Angeles', 'plan_lang': 'en',
           'home_assistant': {'url': '', 'token': '', 'entities': {}}}
    data = {'weather': {'icon': '☁️', 'max': 67, 'min': 58, 'text': 'Cloudy', 'rain': 0, 'uv': {'max': 7}, 'hint': ''},
            'errors': []}
    plan_data.local_weather(cfg, data, datetime.datetime(2026, 10, 27, 14, 30, tzinfo=LA), LA, plan_data.words(cfg))
    assert (data['weather']['now'], data['weather']['max'], data['weather']['text']) == (75, 75, 'Clear now')


def test_today_high_from_periods(monkeypatch):
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: WEEK)
    assert nws.today_high(33.77, -118.36, datetime.date(2026, 10, 27), LA) == 73
    assert nws.today_high(33.77, -118.36, datetime.date(2026, 10, 28), LA) == 72
