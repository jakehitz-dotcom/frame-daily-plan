"""Severe weather monitoring: decides when it is on, and gathers what the monitoring screen shows.

On when, within the next 24 hours: rain adds up to more than 0.3 in, a gust tops 25 mph, or the
National Weather Service has any alert out for the house (thresholds are settings).

Sources (each with a fallback, so one outage never blanks the screen):
- alerts: NWS active alerts for the point
- hourly rain %, rain amount, wind and gusts: Open-Meteo hourly (fallback: the NWS forecast grid)
- minutes until rain: Open-Meteo 15-minute forecast (HRRR in the US); fallback: the hourly forecast
- current conditions: the planner's own numbers (NWS grid cell) so both screens agree
- radar: see radar.py
"""
import datetime
import json
import sys
import time
import urllib.parse
import urllib.request
import zoneinfo

import nws

DEFAULTS = {'enabled': True, 'precip_in': 0.3, 'gust_mph': 25, 'minutes': 30, 'wake': True,
            'quiet_start': '22:00', 'quiet_end': '06:00'}
RAIN_15 = 0.005   # inches in 15 minutes: the lightest rain that counts as "rain starts"
_cache = {}       # key -> (time, value)
FRESH = 600


def settings(cfg):
    return dict(DEFAULTS, **(cfg.get('severe') or {}))


def _cached(key, fn, fresh=FRESH):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < fresh:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def open_meteo(lat, lon, tz_name):
    """Current, hourly (48 h) and 15-minute (24 h) forecast in °F, mph and inches."""
    base = {'latitude': lat, 'longitude': lon, 'timezone': tz_name, 'temperature_unit': 'fahrenheit',
            'wind_speed_unit': 'mph', 'precipitation_unit': 'inch',
            'current': 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,'
                       'wind_direction_10m,precipitation',
            'hourly': 'precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m,wind_direction_10m,'
                      'temperature_2m'}
    attempts = [dict(base, minutely_15='precipitation', forecast_hours=48, forecast_minutely_15=96),
                dict(base, forecast_days=3)]           # older servers: no minutely, plain days
    last = None
    for params in attempts:
        try:
            url = 'https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params)
            return json.load(urllib.request.urlopen(url, timeout=20))
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def _hours_from_open_meteo(om, now):
    h = om.get('hourly') or {}
    out = []
    for i, t in enumerate(h.get('time') or []):
        start = datetime.datetime.fromisoformat(t).replace(tzinfo=now.tzinfo)
        if start + datetime.timedelta(hours=1) <= now:
            continue
        get = lambda k: ((h.get(k) or [None] * (i + 1))[i])  # noqa: E731
        out.append({'t': start, 'pop': get('precipitation_probability') or 0, 'rain': get('precipitation') or 0.0,
                    'wind': get('wind_speed_10m') or 0.0, 'gust': get('wind_gusts_10m') or 0.0,
                    'dir': get('wind_direction_10m'), 'temp': get('temperature_2m')})
        if len(out) == 24:
            break
    return out


def _hours_from_nws(lat, lon, now, tz):
    """Fallback: the NWS grid's hourly-ish values (rain amounts spread across their 6-hour blocks)."""
    grid = nws._cached(nws.point(lat, lon)['forecastGridData'])['properties']
    series = {k: nws._intervals((grid.get(k) or {}).get('values'), tz)
              for k in ('probabilityOfPrecipitation', 'quantitativePrecipitation', 'windSpeed', 'windGust',
                        'windDirection', 'temperature')}

    def at(key, t, per_hour=False):
        for s, e, v in series[key]:
            if s <= t < e:
                return v / max(1, (e - s).total_seconds() / 3600) if per_hour else v
        return None
    out = []
    start = now.replace(minute=0, second=0, microsecond=0)
    for k in range(24):
        t = start + datetime.timedelta(hours=k)
        kmh = lambda v: None if v is None else v * 0.621371  # noqa: E731
        mm = at('quantitativePrecipitation', t, per_hour=True)
        temp = at('temperature', t)
        out.append({'t': t, 'pop': at('probabilityOfPrecipitation', t) or 0, 'rain': (mm or 0) / 25.4,
                    'wind': kmh(at('windSpeed', t)) or 0.0, 'gust': kmh(at('windGust', t)) or 0.0,
                    'dir': at('windDirection', t), 'temp': None if temp is None else temp * 9 / 5 + 32})
    return out


def rain_eta(om, hours, now):
    """{'state': 'now'|'soon'|'later'|'none', 'minutes', 'at', 'ends', 'strip': [(t, in/15min)]}"""
    m = om.get('minutely_15') or {} if om else {}
    slots = []
    for t, v in zip(m.get('time') or [], m.get('precipitation') or []):
        start = datetime.datetime.fromisoformat(t).replace(tzinfo=now.tzinfo)
        if start + datetime.timedelta(minutes=15) > now:
            slots.append((start, v or 0.0))
    strip = slots[:12]   # the next three hours, drawn as a bar strip
    if slots:
        wet = [(s, v) for s, v in slots if v >= RAIN_15]
        if wet and wet[0][0] <= now:
            end = next((s for s, v in slots if s > now and v < RAIN_15), None)
            return {'state': 'now', 'minutes': 0, 'at': None, 'ends': end, 'strip': strip}
        if wet:
            mins = max(1, round((wet[0][0] - now).total_seconds() / 60))
            return {'state': 'soon', 'minutes': mins, 'at': wet[0][0], 'ends': None, 'strip': strip}
    for h in hours:  # beyond the 15-minute range (or without it): the first likely-wet hour
        if h['rain'] >= 0.01 and h['pop'] >= 40:
            mins = max(0, round((h['t'] - now).total_seconds() / 60))
            return {'state': 'later', 'minutes': mins, 'at': h['t'], 'ends': None, 'strip': strip}
    return {'state': 'none', 'minutes': None, 'at': None, 'ends': None, 'strip': strip}


def _alert(a, tz):
    ends = a.get('ends') or a.get('expires')
    summary = (a.get('headline') or '').strip()
    desc = ' '.join((a.get('description') or '').split())
    if desc:  # the first one or two sentences of the description, without the "* WHAT..." scaffolding
        desc = desc.replace('* WHAT...', '').replace('* WHERE...', ' Where: ').replace('* WHEN...', ' When: ')
        desc = desc.replace('* IMPACTS...', ' Impacts: ')
    return {'event': a.get('event', 'Alert'), 'severity': a.get('severity') or '', 'headline': summary,
            'summary': desc[:420], 'ends': datetime.datetime.fromisoformat(ends).astimezone(tz) if ends else None}


def gather(cfg, now=None):
    """Everything the monitoring screen and the triggers need, from live sources."""
    tz = zoneinfo.ZoneInfo(cfg['timezone'])
    now = now or datetime.datetime.now(tz)
    loc = cfg['weather']
    errors, om, hours = [], None, []
    try:
        om = open_meteo(loc['lat'], loc['lon'], cfg['timezone'])
        hours = _hours_from_open_meteo(om, now)
    except Exception as e:  # noqa: BLE001
        errors.append(f'Open-Meteo: {e}')
    if not hours:
        try:
            hours = _hours_from_nws(loc['lat'], loc['lon'], now, tz)
        except Exception as e:  # noqa: BLE001
            errors.append(f'NWS grid: {e}')
    try:
        alerts = [_alert(a, tz) for a in nws.alerts(loc['lat'], loc['lon'])
                  if (a.get('status') or 'Actual') == 'Actual']
    except Exception as e:  # noqa: BLE001
        alerts = []
        errors.append(f'NWS alerts: {e}')
    current = (om or {}).get('current') or {}
    return {'now': now, 'hours': hours, 'alerts': alerts, 'eta': rain_eta(om, hours, now),
            'current_om': current, 'om': om, 'errors': errors}


def refresh_times(g, now):
    """The same data seen from a later moment (the check is cached for up to 10 minutes)."""
    om = g.get('om')
    hours = _hours_from_open_meteo(om, now) if om and om.get('hourly') else \
        [h for h in g['hours'] if h['t'] + datetime.timedelta(hours=1) > now]
    return dict(g, now=now, hours=hours, eta=rain_eta(om, hours, now))


def evaluate(cfg, g):
    """{'active', 'reasons': [short chips], 'signature', 'rain_24', 'gust_max', 'gust_at'}"""
    s = settings(cfg)
    hours = g['hours']
    rain_24 = round(sum(h['rain'] for h in hours), 2)
    gust = max(hours, key=lambda h: h['gust'], default=None)
    reasons, keys = [], []
    for a in g['alerts']:
        reasons.append(a['event'])
        keys.append('alert:' + a['event'])
    if gust and gust['gust'] > s['gust_mph']:
        reasons.append(f"Gusts {round(gust['gust'])} mph {gust['t'].strftime('%-I %p').lower()}")
        keys.append('gust')
    if rain_24 > s['precip_in']:
        reasons.append(f'{rain_24:.2f} in rain / 24 h')
        keys.append('rain')
    return {'active': bool(keys) and s['enabled'], 'reasons': reasons, 'signature': '|'.join(sorted(keys)),
            'rain_24': rain_24, 'gust_max': round(gust['gust']) if gust else None,
            'gust_at': gust['t'] if gust else None}


def status(cfg, now=None):
    """Cached (10 min) trigger check, used by the scheduler and the planner's notice."""
    if not settings(cfg)['enabled'] or (cfg.get('weather') or {}).get('lat') is None:
        return {'active': False, 'reasons': [], 'signature': ''}
    key = ('status', round(cfg['weather']['lat'], 3), round(cfg['weather']['lon'], 3))

    def fresh():
        g = gather(cfg, now)
        result = dict(evaluate(cfg, g), gathered=g)
        if g['errors']:
            print('severe weather:', g['errors'], file=sys.stderr)
        return result
    return _cached(key, fresh)


def in_quiet_hours(cfg, now):
    s = settings(cfg)
    t = now.strftime('%H:%M')
    start, end = s['quiet_start'], s['quiet_end']
    return (start <= t or t < end) if start > end else (start <= t < end)
