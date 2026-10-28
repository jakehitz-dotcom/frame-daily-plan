"""Collect today's plan: calendar entries, to-dos, public holidays and weather."""
import datetime
import json
import sys
import urllib.parse
import urllib.request
import zoneinfo

import holidays
import icalendar
import recurring_ical_events

import banner
import ha
import nws
import todo_track

TODO_PREFIXES = ('to-do:', 'todo:', 'task:', 'aufgabe:')

# WMO weather code → emoji, German text, English text
WEATHER_CODES = {
    0: ('☀️', 'Sonnig', 'Sunny'), 1: ('🌤️', 'Überwiegend sonnig', 'Mostly sunny'),
    2: ('⛅', 'Teilweise bewölkt', 'Partly cloudy'), 3: ('☁️', 'Bewölkt', 'Cloudy'),
    45: ('🌫️', 'Neblig', 'Foggy'), 48: ('🌫️', 'Neblig', 'Foggy'),
    51: ('🌦️', 'Leichter Nieselregen', 'Light drizzle'), 53: ('🌦️', 'Nieselregen', 'Drizzle'),
    55: ('🌧️', 'Starker Nieselregen', 'Heavy drizzle'), 61: ('🌦️', 'Leichter Regen', 'Light rain'),
    63: ('🌧️', 'Regen', 'Rain'), 65: ('🌧️', 'Starker Regen', 'Heavy rain'),
    71: ('🌨️', 'Leichter Schnee', 'Light snow'), 73: ('🌨️', 'Schnee', 'Snow'), 75: ('❄️', 'Starker Schnee', 'Heavy snow'),
    80: ('🌦️', 'Regenschauer', 'Showers'), 81: ('🌧️', 'Regenschauer', 'Showers'), 82: ('🌧️', 'Starke Regenschauer', 'Heavy showers'),
    95: ('⛈️', 'Gewitter', 'Thunderstorm'), 96: ('⛈️', 'Gewitter mit Hagel', 'Thunderstorm with hail'),
    99: ('⛈️', 'Gewitter mit Hagel', 'Thunderstorm with hail'),
}
WORDS = {
    'de': {'all_day': 'Ganztägig', 'holiday': 'Feiertag', 'changeable': 'Wechselhaft', 'rain': 'Regen',
           'hint_rain': 'Regenjacke mitnehmen', 'hint_cold': 'Warme Jacke anziehen',
           'hint_cool': 'Pulli oder Jacke mitnehmen', 'hint_hot': 'Viel trinken und Sonnencreme', 'now': 'Jetzt: {}',
           'quiet': 'Ruhige Woche: kein Regen oder Sturm erwartet'},
    'en': {'all_day': 'All day', 'holiday': 'Holiday', 'changeable': 'Changeable', 'rain': 'chance of rain',
           'hint_rain': 'Bring a rain jacket', 'hint_cold': 'Wear a warm coat',
           'hint_cool': 'Bring a sweater or jacket', 'hint_hot': 'Drink plenty, wear sunscreen', 'now': '{} now',
           'quiet': 'Quiet week ahead: no storms or rain expected'},
}


def words(cfg):
    return WORDS.get(cfg.get('plan_lang', 'en'), WORDS['en'])


def nice_title(title):
    return ' '.join(title.split())


def fetch_calendar(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'frame-daily-plan'})
    return icalendar.Calendar.from_ical(urllib.request.urlopen(req, timeout=30).read())


def calendar_entries(cal, color, day, tz, w, todo_calendar=False):
    events, todos = [], []
    for ev in recurring_ical_events.of(cal).at(day):
        title = nice_title(str(ev.get('SUMMARY', '')))
        if not title:
            continue
        is_todo_prefix = title.lower().startswith(TODO_PREFIXES)
        if is_todo_prefix:
            title = title.split(':', 1)[1].strip()
        if todo_calendar or is_todo_prefix:
            todos.append(title)
            continue
        start = ev['DTSTART'].dt
        end = ev['DTEND'].dt if ev.get('DTEND') else None
        if isinstance(start, datetime.datetime):
            start = start.astimezone(tz)
            end = end.astimezone(tz) if end else None
            events.append({'sort': start.strftime('%H:%M'), 'start': start.strftime('%H:%M'),
                           'end': end.strftime('%H:%M') if end and end != start else None,
                           'end_dt': end or start, 'title': title, 'color': color})
        else:
            events.append({'sort': '00:00', 'start': w['all_day'], 'end': None, 'end_dt': None,
                           'title': title, 'color': color})
    return events, todos


def holiday_calendar(cfg, year):
    h = cfg.get('holidays') or {}
    if not h.get('country'):
        return {}
    subdiv = h.get('subdiv') or None
    cal = holidays.country_holidays(h['country'], subdiv=subdiv, years=year)
    # Use the plan language if the country's holiday data has it, otherwise its default names.
    lang = cfg.get('plan_lang', 'en')
    pick = next((code for code in (getattr(cal, 'supported_languages', ()) or ()) if code.split('_')[0] == lang), None)
    return holidays.country_holidays(h['country'], subdiv=subdiv, years=year, language=pick) if pick else cal


def holiday_entries(cfg, day, w):
    cal = holiday_calendar(cfg, day.year)
    if day not in cal:
        return []
    return [{'sort': '00:00', 'start': w['holiday'], 'end': None, 'end_dt': None, 'title': n.strip(), 'color': 'red'}
            for n in cal.get(day).split(';')]


def dress(hi_f, lo_f, rain_pct, uv_max, lang='en', cool='AM/PM'):
    """What to wear for the hours ahead: their warmest/coolest (°F), rain chance and UV. cool says when
    the cool part is ('later' in the evening, 'this morning' early on). One short line."""
    if lang == 'de':
        base = ('Kurze Hose und T-Shirt' if hi_f >= 80 else 'T-Shirt-Wetter' if hi_f >= 72 else 'Langarm oder leichte Schicht'
                if hi_f >= 64 else 'Pulli oder leichte Jacke' if hi_f >= 55 else 'Warme Jacke' if hi_f >= 45 else 'Wintermantel')
        extra = (['Jacke für morgens und abends'] if hi_f >= 64 and lo_f < 55 and rain_pct < 50 else []) + \
            (['Regenjacke'] if rain_pct >= 50 else []) + (['Sonnencreme'] if uv_max >= 6 else [])
        return ' · '.join([base] + extra)
    if hi_f >= 90:
        base = 'Shorts & T-shirt, hydrate'
    elif hi_f >= 80:
        base = 'Shorts & T-shirt'
    elif hi_f >= 72:
        base = 'T-shirt' + (f' + light jacket {cool}' if lo_f < 58 else ' weather')
    elif hi_f >= 64:
        base = 'Long sleeves' + (f' + jacket {cool}' if lo_f <= 55 else ' or a light layer')
    elif hi_f >= 55:
        base = 'Sweater or light jacket'
    elif hi_f >= 45:
        base = 'Warm jacket'
    else:
        base = 'Winter coat, hat & gloves'
    extra = (['rain jacket or umbrella'] if rain_pct >= 50 else []) + (['sunscreen'] if uv_max >= 6 else [])
    return ' · '.join([base] + extra)


def _hour(h):
    return datetime.time(h % 24).strftime('%-I %p')


def weather(cfg, day, w):
    """Today's forecast (Open-Meteo): high/low, rain chance, max UV and the hours at UV 6+.
    Temperatures are returned in the display unit; advice is computed in °F."""
    loc = cfg['weather']
    url = 'https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode({
        'latitude': loc['lat'], 'longitude': loc['lon'], 'timezone': cfg['timezone'],
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max',
        'hourly': 'uv_index,temperature_2m,precipitation_probability', 'start_date': day.isoformat(),
        'end_date': (day + datetime.timedelta(days=1)).isoformat()})
    body = json.load(urllib.request.urlopen(url, timeout=20))
    d = body['daily']
    code, tmax, tmin = d['weather_code'][0], d['temperature_2m_max'][0], d['temperature_2m_min'][0]
    rain = d['precipitation_probability_max'][0] or 0
    h = body['hourly']
    hours = [{'t': t, 'uv': uv or 0, 'temp_f': None if c is None else c * 9 / 5 + 32, 'pop': p or 0}
             for t, uv, c, p in zip(h['time'], h['uv_index'], h.get('temperature_2m') or [None] * len(h['time']),
                                    h.get('precipitation_probability') or [0] * len(h['time']))]
    today_hours = [x for x in hours if x['t'][:10] == day.isoformat()]
    uv_hours = [int(x['t'][11:13]) for x in today_hours if x['uv'] >= 6]
    uv_max = max([(d.get('uv_index_max') or [0])[0] or 0] + [x['uv'] for x in today_hours])
    entry = WEATHER_CODES.get(code)
    icon, text = (entry[0], entry[1 if cfg.get('plan_lang') == 'de' else 2]) if entry else ('⛅', w['changeable'])
    hi_f, lo_f = tmax * 9 / 5 + 32, tmin * 9 / 5 + 32
    if loc.get('unit', 'F') == 'F':
        tmax, tmin = hi_f, lo_f
    return {'icon': icon, 'max': round(tmax), 'min': round(tmin), 'text': text, 'rain': rain,
            'uv': {'max': round(uv_max), 'from': _hour(uv_hours[0]) if uv_hours else None,
                   'to': _hour(uv_hours[-1] + 1) if uv_hours else None},
            'hint': dress(hi_f, lo_f, rain, uv_max, cfg.get('plan_lang', 'en')), 'hours': hours}


def sky_now(cfg, data, w):
    """The sky right now from the nearest fresh NWS observation (KTOA, then KHHR), and the current
    temperature. Falls back to the forecast text when no station has a recent report."""
    stations = [s.strip().upper() for s in (cfg['weather'].get('stations') or 'KTOA, KHHR').split(',') if s.strip()]
    obs = nws.now(stations)
    if not obs:
        return
    data['weather'].update(icon=obs['icon'], station=obs['station'], text=w['now'].format(obs['text']))
    if obs['temp_f'] is not None:
        t = obs['temp_f'] if cfg['weather'].get('unit', 'F') == 'F' else round((obs['temp_f'] - 32) * 5 / 9)
        data['weather']['now'] = t


def local_weather(cfg, data, now, tz, w):
    """Make the weather panel about the house: now / high / low / rain chance / sky from the NWS 2.5 km
    grid cell at the house's coordinates (one forecaster-adjusted source, so they agree). Falls back to
    the nearest airport observation and the Open-Meteo day. An outdoor sensor in Home Assistant, if
    configured, gives the reading 'now'. The high is never below now, the low never above it, and the
    dressing advice follows the final numbers."""
    wx, loc = data['weather'], cfg['weather']
    unit = loc.get('unit', 'F')
    to_unit = (lambda f: f) if unit == 'F' else (lambda f: round((f - 32) * 5 / 9))
    to_f = (lambda t: t) if unit == 'F' else (lambda t: t * 9 / 5 + 32)
    grid = {}
    try:
        grid = nws.local(loc['lat'], loc['lon'], now, tz)
    except Exception as e:  # noqa: BLE001
        data['errors'].append(f'NWS grid: {e}')
    if grid.get('hi_f') is not None:
        wx['max'] = to_unit(grid['hi_f'])
    if grid.get('lo_f') is not None:
        wx['min'] = to_unit(grid['lo_f'])
    if grid.get('pop') is not None:
        wx['rain'] = grid['pop']
    if grid.get('now_f') is not None:
        wx['now'] = to_unit(grid['now_f'])
        wx['source'] = 'grid'
    if grid.get('text'):
        wx.update(icon=grid['icon'], text=w['now'].format(grid['text'][:1].upper() + grid['text'][1:].lower()))
    else:
        try:
            sky_now(cfg, data, w)  # nearest airport (KTOA, then KHHR)
        except Exception as e:  # noqa: BLE001
            data['errors'].append(f'NWS observation: {e}')
    sensor = ((cfg.get('home_assistant') or {}).get('entities') or {}).get('outdoor_temp')
    if sensor and ha.configured(cfg):
        try:
            st = ha.state(sensor, cfg)
            reading = float(st['state'])
            unit_ha = (st.get('attributes') or {}).get('unit_of_measurement', '°F')
            reading_f = reading if 'F' in unit_ha else reading * 9 / 5 + 32
            wx['now'], wx['source'] = to_unit(round(reading_f)), 'sensor'
        except Exception as e:  # noqa: BLE001  unavailable sensor: the grid value stands
            data['errors'].append(f'Outdoor sensor: {e}')
    if wx.get('now') is not None:
        wx['max'] = max(wx['max'], wx['now'])
        wx['min'] = min(wx['min'], wx['now'])
    rain = wx.get('rain') or 0
    if 30 <= rain < 50:
        wx['text'] = wx['text'].split(', ')[0] + f", {rain}% {w['rain']}"
    ahead(cfg, wx, grid, now, to_f)


AHEAD = datetime.timedelta(hours=6)
UV_WINDOW = 4   # the hours above this UV index are shown


def ahead(cfg, wx, grid, now, to_f):
    """UV now (hidden below 1) and today's hours above UV 4 (until they have passed), and what to
    wear for the next six hours, as if heading out now."""
    hours = []
    for x in wx.pop('hours', None) or []:
        start = datetime.datetime.fromisoformat(x['t']).replace(tzinfo=now.tzinfo)
        hours.append((start, x))
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    if hours:
        uv_now = next((x['uv'] for s, x in hours if s == current_hour), 0)
        high = [s for s, x in hours if s.date() == now.date() and x['uv'] > UV_WINDOW]
        ended = not high or high[-1] + datetime.timedelta(hours=1) <= now
        uv = {'now': round(uv_now) if uv_now >= 1 else None,
              'from': None if ended else _hour(high[0].hour), 'to': None if ended else _hour(high[-1].hour + 1)}
        wx['uv'] = uv if uv['now'] is not None or uv['from'] else None
    window = [(s, x) for s, x in hours if current_hour <= s < now + AHEAD]
    temps = list(grid.get('next') or [])                       # (start, °F) from the NWS grid cell
    if not temps:
        temps = [(s, x['temp_f']) for s, x in window if x['temp_f'] is not None]
    if wx.get('now') is not None:
        temps.insert(0, (now, to_f(wx['now'])))
    pops = [grid['pop_next']] if grid.get('pop_next') is not None else [x['pop'] for _, x in window]
    uv_ahead = max([x['uv'] for _, x in window] or [0])
    if not temps:  # nothing hourly at all: the day's numbers
        wx['hint'] = dress(to_f(wx['max']), to_f(wx['min']), wx.get('rain') or 0, uv_ahead, cfg.get('plan_lang', 'en'))
        return
    warm = max(temps, key=lambda t: t[1])
    cool = min(temps, key=lambda t: t[1])
    when = 'later' if cool[0] > warm[0] else ('this morning' if now.hour < 12 else 'now')
    wx['hint'] = dress(warm[1], cool[1], max(pops or [0]), uv_ahead, cfg.get('plan_lang', 'en'), when)


WIND_ALERT_MPH = 20   # the same thresholds as the alert tiles on the Eastvale dashboard
RAIN_ALERT_IN = 0.25


def _number(st):
    try:
        return float(st['state'])
    except (TypeError, KeyError, ValueError):  # missing, unknown or unavailable: leave it out
        return None


def home(cfg, now=None):
    """Read the Home Assistant entities in one request. Returns (home, week): anything that is
    missing or unavailable is simply left out, so the plan only shows what it knows."""
    ents = (cfg.get('home_assistant') or {}).get('entities') or {}
    snap = ha.snapshot(cfg)
    get = lambda key: snap.get(ents.get(key) or '')  # noqa: E731
    out = {}
    stat = get('thermostat')
    if stat:
        a = stat.get('attributes', {})
        mode = stat.get('state')
        if mode == 'heat' and a.get('temperature') is not None:
            out['set'] = {'mode': 'heat', 'temps': [round(a['temperature'])]}
        elif mode == 'cool' and a.get('temperature') is not None:
            out['set'] = {'mode': 'cool', 'temps': [round(a['temperature'])]}
        elif mode == 'heat_cool' and a.get('target_temp_low') is not None and a.get('target_temp_high') is not None:
            out['set'] = {'mode': 'heat_cool', 'temps': [round(a['target_temp_low']), round(a['target_temp_high'])]}
        if a.get('current_temperature') is not None:
            out['inside'] = round(a['current_temperature'])
    inside = _number(get('indoor_temp'))
    if inside is not None:
        out['inside'] = round(inside)
    tub = _number(get('hot_tub'))
    if tub is not None:
        out['hot_tub'] = round(tub)
    bottle = get('last_bottle')
    if bottle and now is not None:
        try:
            at = datetime.datetime.fromisoformat(bottle['state']).astimezone(now.tzinfo)
        except (TypeError, ValueError, KeyError):  # unknown / unavailable
            at = None
        mins = (now - at).total_seconds() // 60 if at else -1
        if 0 <= mins < 24 * 60:  # older than a day means stale data, not a hungry baby
            out['bottle'] = {'at': at.strftime('%-I:%M %p'), 'ago': f'{int(mins // 60)}h {int(mins % 60)}m'
                             if mins >= 60 else f'{int(mins)}m'}
    dish = get('dishwasher_clean')
    if dish and dish.get('state') == 'on':
        out['dishwasher_clean'] = True
    week = {}
    wind, rain = _number(get('wind_7d')), _number(get('rain_7d'))
    if wind is not None:
        week['wind'], week['wind_alert'] = round(wind), wind > WIND_ALERT_MPH
    if rain is not None:
        week['rain'], week['rain_alert'] = round(rain, 2), rain > RAIN_ALERT_IN
    return out, week


def build(cfg, now):
    tz = zoneinfo.ZoneInfo(cfg['timezone'])
    w = words(cfg)
    today = now.date()
    tomorrow = today + datetime.timedelta(days=1)
    errors = []
    try:
        events, events_tomorrow = holiday_entries(cfg, today, w), holiday_entries(cfg, tomorrow, w)
    except Exception as e:  # noqa: BLE001  e.g. an unknown country code
        events, events_tomorrow = [], []
        errors.append(f'Holidays: {e}')
    todos = []
    for c in cfg['calendars']:
        if not c.get('url'):
            continue
        try:
            cal = fetch_calendar(c['url'])
        except Exception as e:  # noqa: BLE001  one broken calendar must not hide the others
            errors.append(f"Calendar {c.get('name')}: {e}")
            continue
        is_todo = c.get('role') == 'todos'
        e1, t1 = calendar_entries(cal, c.get('color', 'green'), today, tz, w, is_todo)
        e2, _ = calendar_entries(cal, c.get('color', 'green'), tomorrow, tz, w, is_todo)
        events += e1
        todos += t1
        events_tomorrow += e2
    events.sort(key=lambda e: e['sort'])
    events_tomorrow.sort(key=lambda e: e['sort'])
    for e in events:
        e['past'] = bool(cfg.get('dim_past_events')) and e['end_dt'] is not None and e['end_dt'] <= now
    data = {'date': today.isoformat(), 'lang': cfg.get('plan_lang', 'en'), 'today': events,
            'tomorrow': events_tomorrow, 'todos': sorted(set(todos)), 'todos_done': [], 'weather': None, 'errors': errors}
    loc = cfg.get('weather') or {}
    if loc.get('enabled') and loc.get('lat') is not None and loc.get('lon') is not None:
        try:
            data['weather'] = weather(cfg, today, w)
        except Exception as e:  # noqa: BLE001  the plan must still appear without weather
            errors.append(f'Weather: {e}')
        if data['weather']:
            local_weather(cfg, data, now, tz, w)
            try:
                data['weather']['outlook'] = nws.outlook(loc['lat'], loc['lon'], today, tz) or w['quiet']
            except Exception as e:  # noqa: BLE001
                errors.append(f'NWS outlook: {e}')
    data['todo_hint'] = cfg.get('todo_hint', '')
    data['updated'] = now.strftime('%-I:%M %p')
    clock = cfg.get('clock') or {}
    if clock.get('enabled', True):  # a few minutes ahead: the image is up to 5 minutes old when you see it
        data['clock'] = (now + datetime.timedelta(minutes=int(clock.get('ahead_minutes', 3)))).strftime('%H:%M')
        data['clock_style'], data['clock_opacity'] = clock.get('style', 'ring'), float(clock.get('opacity', 0.28))
    data['banner'], data['countdown'] = banner.for_day(cfg, today)
    lists = (cfg.get('home_assistant') or {}).get('todo_lists') or []
    if lists and ha.configured(cfg):
        try:  # due today, overdue or undated; tasks dated later wait for their day
            lists, missing = ha.resolve_todo_lists(lists, cfg)
            if missing:
                errors.append(f"To-do lists not found in Home Assistant: {', '.join(missing)}")
            open_, done = [], []
            for entity, items in (ha.todo_items(lists, cfg) if lists else {}).items():
                for i in items:
                    if i['done']:  # completed today (local time) stays, checked off, until midnight
                        try:
                            at = datetime.datetime.fromisoformat(i['completed']).astimezone(tz)
                        except (TypeError, ValueError):
                            continue
                        if at.date() == today:
                            done.append((at, i['summary']))
                    elif not i['due'] or i['due'] <= today.isoformat():
                        open_.append(i['summary'])
            done += todo_track.done_today(today, tz)  # lists that drop checked-off tasks (Alexa): noticed by the app
            # the list's own order (Google Tasks: newest on top), then calendar to-dos; latest ticked off first
            data['todos'] = list(dict.fromkeys(open_ + data['todos']))
            data['todos_done'] = list(dict.fromkeys(s for _, s in sorted(done, reverse=True) if s not in data['todos']))
        except Exception as e:  # noqa: BLE001
            errors.append(f'To-do lists: {e}')
    data['home'], data['week'] = {}, {}
    if ha.configured(cfg):
        try:
            data['home'], data['week'] = home(cfg, now)
        except Exception as e:  # noqa: BLE001  Home Assistant down: the plan appears without it
            errors.append(f'Home Assistant: {e}')
    for e in data['today'] + data['tomorrow']:
        e.pop('end_dt', None)
        e.pop('sort', None)
    if errors:
        print('plan warnings:', errors, file=sys.stderr)
    return data


def geocode(name, lang='en'):
    """Place search for the settings page (Open-Meteo geocoding, no key needed)."""
    url = 'https://geocoding-api.open-meteo.com/v1/search?' + urllib.parse.urlencode(
        {'name': name, 'count': 6, 'language': lang, 'format': 'json'})
    results = json.load(urllib.request.urlopen(url, timeout=15)).get('results') or []
    return [{'label': ', '.join(x for x in (r.get('name'), r.get('admin1'), r.get('country')) if x),
             'lat': round(r['latitude'], 4), 'lon': round(r['longitude'], 4), 'timezone': r.get('timezone'),
             'country_code': r.get('country_code')} for r in results]


DEMO = {
    'de': {
        'today': [('Ganztägig', None, 'Projektwoche in der Schule', 'blue'),
                  ('07:15', '07:45', 'Frühstück zusammen', 'green'),
                  ('08:15', '14:00', 'Schule', 'blue'),
                  ('15:30', '16:30', 'Schwimmkurs', 'green'),
                  ('18:00', None, 'Abendessen bei Oma', 'green'),
                  ('19:30', '19:45', 'Rucksack für morgen packen', 'green')],
        'tomorrow': [('08:15', '14:00', 'Schule', 'blue'),
                     ('16:00', '17:00', 'Klavierunterricht', 'green'),
                     ('19:00', None, 'Spieleabend', 'green')],
        'todos': ['Englisch-Vokabeln lernen', 'Mathe üben', 'Zimmer aufräumen'],
        'weather': {'icon': '⛅', 'max': 19, 'min': 12, 'text': 'Teilweise bewölkt', 'hint': 'Pulli oder Jacke mitnehmen'},
    },
    'en': {
        'today': [('All day', None, 'Project week at school', 'blue'),
                  ('07:15', '07:45', 'Breakfast together', 'green'),
                  ('08:15', '14:00', 'School', 'blue'),
                  ('15:30', '16:30', 'Swimming lesson', 'green'),
                  ('18:00', None, "Dinner at Grandma's", 'green'),
                  ('19:30', '19:45', 'Pack the school bag', 'green')],
        'tomorrow': [('08:15', '14:00', 'School', 'blue'),
                     ('16:00', '17:00', 'Piano lesson', 'green'),
                     ('19:00', None, 'Board game night', 'green')],
        'todos': ['Learn English vocabulary', 'Practise maths', 'Tidy up your room'],
        'weather': {'icon': '⛅', 'now': 66, 'max': 73, 'min': 56, 'text': 'Partly cloudy now', 'rain': 10,
                    'uv': {'now': 3, 'from': '11 AM', 'to': '2 PM'},
                    'hint': 'Long sleeves + jacket AM/PM · sunscreen',
                    'outlook': 'Rain likely Sat night–Sun (70%) · Windy Tue'},
    },
}


def demo(cfg, now, lang='en'):
    """Made-up plan for screenshots and videos: no real names, appointments or location."""
    d = DEMO[lang]

    def entries(rows, past_check):
        out = []
        for start, end, title, color in rows:
            past = False
            if past_check and start[0].isdigit():
                h, m = map(int, (end or start).split(':'))
                past = bool(cfg.get('dim_past_events')) and (now.hour, now.minute) >= (h, m)
            out.append({'start': start, 'end': end, 'title': title, 'color': color, 'past': past})
        return out
    return {'date': now.date().isoformat(), 'lang': lang, 'today': entries(d['today'], True),
            'tomorrow': entries(d['tomorrow'], False), 'todos': d['todos'], 'weather': dict(d['weather']),
            'home': {'bottle': {'at': '6:45 AM', 'ago': '1h 15m'}, 'inside': 71, 'set': {'mode': 'heat', 'temps': [70]}, 'hot_tub': 101, 'dishwasher_clean': True},
            'week': {'wind': 24, 'wind_alert': True, 'rain': 0.4, 'rain_alert': True}, 'errors': [],
            'updated': now.strftime('%-I:%M %p'), 'clock': (now + datetime.timedelta(minutes=3)).strftime('%H:%M')}
