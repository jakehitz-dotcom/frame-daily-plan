"""US National Weather Service (api.weather.gov): free, no key, needs a User-Agent.

- now(stations): the latest observation from the first station with a fresh one (e.g. KTOA, then KHHR)
- outlook(lat, lon, ...): one line about significant weather in the next 7 days, from the point
  forecast (twice-daily periods) and active alerts for the location
- local(lat, lon, ...): now / high / low / rain chance / sky for the 2.5 km forecast grid cell the
  house is in: one forecaster-adjusted source, so the numbers agree with each other
"""
import datetime
import re

import requests

HEADERS = {'User-Agent': 'frame-daily-plan (github.com/jakehitz-dotcom/frame-daily-plan)', 'Accept': 'application/geo+json'}
TIMEOUT = 15
STALE = datetime.timedelta(hours=2)
_points = {}         # (lat, lon) -> the point's properties (forecast URLs); the grid cell never moves
_cache = {}          # url -> (fetched at, json): one fetch per 10 minutes serves every view
FRESH = 600


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _cached(url):
    import time
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < FRESH:
        return hit[1]
    body = _get(url)
    _cache[url] = (time.time(), body)
    return body


def point(lat, lon):
    key = (round(lat, 4), round(lon, 4))
    if key not in _points:
        _points[key] = _get(f'https://api.weather.gov/points/{key[0]},{key[1]}')['properties']
    return _points[key]


def _intervals(values, tz):
    """NWS grid values ('2026-10-27T21:00:00+00:00/PT2H') as (start, end, value), local time."""
    out = []
    for v in values or []:
        if v.get('value') is None:
            continue
        start_s, _, dur = v['validTime'].partition('/')
        start = datetime.datetime.fromisoformat(start_s).astimezone(tz)
        m = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?', dur)
        hours = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) + int(m.group(3) or 0) / 60 if m else 1
        out.append((start, start + datetime.timedelta(hours=hours), v['value']))
    return out


def _c_to_f(c):
    return c * 9 / 5 + 32


def local(lat, lon, now, tz):
    """{'now_f', 'hi_f', 'lo_f', 'pop', 'text', 'icon'} for the grid cell at (lat, lon).
    hi/lo cover the local calendar day: the hourly temperatures plus the grid's day maximum and
    overnight minimum (so hours already past still count). Missing parts are None."""
    p = point(lat, lon)
    grid = _cached(p['forecastGridData'])['properties']
    today = now.date()
    temps = _intervals((grid.get('temperature') or {}).get('values'), tz)
    current = next((v for s, e, v in temps if s <= now < e), None)
    todays = [v for s, e, v in temps if s.date() == today or (s < now < e)]
    highs = [v for s, e, v in _intervals((grid.get('maxTemperature') or {}).get('values'), tz) if s.date() == today]
    lows = [v for s, e, v in _intervals((grid.get('minTemperature') or {}).get('values'), tz)
            if e.date() == today or s.date() == today and s.hour < 6]
    pops = [v for s, e, v in _intervals((grid.get('probabilityOfPrecipitation') or {}).get('values'), tz)
            if s.date() == today and e > now]
    hi = max(todays + highs) if todays or highs else None
    lo = min(todays + lows) if todays or lows else None
    text = icon = None
    try:
        hourly = _cached(p['forecastHourly'])['properties']['periods']
        hour = next((h for h in hourly if datetime.datetime.fromisoformat(h['startTime']) <= now
                     < datetime.datetime.fromisoformat(h['endTime'])), hourly[0] if hourly else None)
        if hour:
            text = hour.get('shortForecast', '')
            icon = sky_icon(text, not hour.get('isDaytime', True))
            if current is None and hour.get('temperatureUnit') == 'F':
                current = (hour['temperature'] - 32) * 5 / 9
    except Exception:  # noqa: BLE001  the numbers still stand without the sky text
        pass
    f = lambda c: None if c is None else round(_c_to_f(c))  # noqa: E731
    return {'now_f': f(current), 'hi_f': f(hi), 'lo_f': f(lo), 'pop': round(max(pops)) if pops else None,
            'text': text, 'icon': icon}


def sky_icon(text, night=False):
    t = text.lower()
    for words, icon in ((('thunder',), '⛈️'), (('snow', 'sleet', 'ice', 'hail'), '🌨️'),
                        (('rain', 'shower', 'drizzle'), '🌧️'), (('fog', 'mist', 'haze', 'smoke'), '🌫️'),
                        (('partly',), '⛅'), (('mostly cloudy',), '🌥️'), (('cloudy', 'overcast'), '☁️'),
                        (('mostly sunny', 'mostly clear'), '🌙' if night else '🌤️'),
                        (('sunny', 'clear', 'fair'), '🌙' if night else '☀️'), (('wind', 'breez'), '💨')):
        if any(w in t for w in words):
            return icon
    return '⛅'


def now(stations, at=None):
    """{'station', 'text', 'icon', 'temp_f'} from the first station with an observation under 2 hours old."""
    at = at or datetime.datetime.now(datetime.timezone.utc)
    for station in stations:
        try:
            p = _get(f'https://api.weather.gov/stations/{station}/observations/latest')['properties']
            when = datetime.datetime.fromisoformat(p['timestamp'])
        except Exception:  # noqa: BLE001  station down or not reporting: try the next one
            continue
        text = (p.get('textDescription') or '').strip()
        if at - when > STALE or not text:
            continue
        c = (p.get('temperature') or {}).get('value')
        return {'station': station, 'text': text[0].upper() + text[1:].lower(),
                'icon': sky_icon(text, '/night/' in (p.get('icon') or '')),
                'temp_f': None if c is None else round(c * 9 / 5 + 32)}
    return None


def periods(lat, lon):
    return _cached(point(lat, lon)['forecast'])['properties']['periods']


def today_high(lat, lon, today, tz):
    """Today's forecast high (°F) from the daytime period that starts today; None after it has passed."""
    for p in periods(lat, lon):
        if p.get('isDaytime') and datetime.datetime.fromisoformat(p['startTime']).astimezone(tz).date() == today \
                and p.get('temperatureUnit') == 'F' and p.get('temperature') is not None:
            return p['temperature']
    return None


def alerts(lat, lon):
    feats = _get(f'https://api.weather.gov/alerts/active?point={round(lat, 4)},{round(lon, 4)}').get('features', [])
    return [f['properties'] for f in feats if f.get('properties', {}).get('event')]


# What makes a forecast period worth a line: storms, rain, snow, real wind, or heat.
SIGNIFICANT = re.compile(r'thunder|rain|shower|snow|sleet|hail|storm|windy|wind advisory|dust|smoke', re.I)
HOT_F = 90


def _significant(p):
    pop = (p.get('probabilityOfPrecipitation') or {}).get('value') or 0
    short = p.get('shortForecast', '')
    hot = p.get('isDaytime') and p.get('temperatureUnit') == 'F' and (p.get('temperature') or 0) >= HOT_F
    # "Slight Chance Rain" at 10-20 % is noise on a 7-day line; storms count at any chance.
    return bool(re.search('thunder|storm|snow|windy|wind advisory', short, re.I) or (SIGNIFICANT.search(short) and pop >= 30) or hot)


def _when(p, today):
    start = datetime.datetime.fromisoformat(p['startTime'])
    night = not p.get('isDaytime', True)
    if start.date() == today:
        return 'tonight' if night else 'today'
    if start.date() == today + datetime.timedelta(days=1) and not night:
        return 'tomorrow'
    return start.strftime('%a') + (' night' if night else '')


def _phrase(group, today):
    worst = max(group, key=lambda p: ((p.get('probabilityOfPrecipitation') or {}).get('value') or 0,
                                      bool(re.search('thunder|storm', p.get('shortForecast', ''), re.I))))
    short = worst.get('shortForecast', '')
    hot = not SIGNIFICANT.search(short) and (worst.get('temperature') or 0) >= HOT_F
    what = f"Hot ({worst['temperature']}°)" if hot else short[0].upper() + short[1:].lower()
    first, last = _when(group[0], today), _when(group[-1], today)
    when = first if first == last else f'{first}–{last}'
    pop = (worst.get('probabilityOfPrecipitation') or {}).get('value')
    return f"{what} {when}" + (f' ({pop}%)' if pop and pop >= 30 and not hot else '')


def outlook(lat, lon, today, tz):
    """One or two short phrases, most urgent first: active alerts, then the first two spells of
    significant weather in the 7-day forecast. 'Quiet week ahead' when nothing stands out."""
    parts = []
    try:
        for a in alerts(lat, lon)[:1]:
            ends = a.get('ends') or a.get('expires')
            until = ''
            if ends:
                e = datetime.datetime.fromisoformat(ends).astimezone(tz)
                until = f" until {e.strftime('%a') if e.date() != today else ''} {e.strftime('%-I %p')}".replace('  ', ' ')
            parts.append(a['event'] + until)
    except Exception:  # noqa: BLE001  alerts are a bonus; the forecast still counts
        pass
    groups, run = [], []
    for p in periods(lat, lon):
        if _significant(p):
            run.append(p)
        elif run:
            groups.append(run)
            run = []
    if run:
        groups.append(run)
    parts += [_phrase(g, today) for g in groups[:2]]
    return ' · '.join(parts[:2]) if parts else None
