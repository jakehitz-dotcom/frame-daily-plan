"""US National Weather Service (api.weather.gov): free, no key, needs a User-Agent.

- now(stations): the latest observation from the first station with a fresh one (e.g. KTOA, then KHHR)
- outlook(lat, lon, ...): one line about significant weather in the next 7 days, from the point
  forecast (twice-daily periods) and active alerts for the location
"""
import datetime
import re

import requests

HEADERS = {'User-Agent': 'frame-daily-plan (github.com/jakehitz-dotcom/frame-daily-plan)', 'Accept': 'application/geo+json'}
TIMEOUT = 15
STALE = datetime.timedelta(hours=2)
_forecast_urls = {}  # (lat, lon) -> forecast URL; the grid point never moves


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


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


_periods_cache = {}  # (lat, lon) -> (fetched at, periods): one fetch serves today's high, the outlook and the week


def periods(lat, lon):
    import time
    key = (round(lat, 4), round(lon, 4))
    hit = _periods_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if key not in _forecast_urls:
        _forecast_urls[key] = _get(f'https://api.weather.gov/points/{key[0]},{key[1]}')['properties']['forecast']
    result = _get(_forecast_urls[key])['properties']['periods']
    _periods_cache[key] = (time.time(), result)
    return result


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
