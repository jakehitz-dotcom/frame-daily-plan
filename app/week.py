"""The week ahead (shown on Sunday evenings): Monday to Sunday of the coming week, each day's weather
and events, and a short rule-based 'week at a glance'."""
import datetime
import json
import sys
import urllib.parse
import urllib.request
import zoneinfo

import banner
import ha
import nws
import plan_data

WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
WEEKDAYS_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def week_start(today):
    """The Monday after today (on Sunday: tomorrow)."""
    return today + datetime.timedelta(days=(7 - today.weekday()) or 7)


def ampm(hhmm):
    """'08:00' -> '8:00 AM'"""
    return datetime.datetime.strptime(hhmm, '%H:%M').strftime('%-I:%M %p')


def _f(c, unit):
    return round(c * 9 / 5 + 32) if unit == 'F' else round(c)


def weather(cfg, start, tz):
    """{date: {'icon', 'hi', 'lo', 'text', 'pop'}} for the 7 days: the NWS forecast where it reaches
    (it ends about six and a half days out), Open-Meteo for the rest."""
    loc, unit = cfg['weather'], cfg['weather'].get('unit', 'F')
    days = {start + datetime.timedelta(days=i): {} for i in range(7)}
    try:
        for p in nws.periods(loc['lat'], loc['lon']):
            d = datetime.datetime.fromisoformat(p['startTime']).astimezone(tz).date()
            if d not in days:
                continue
            t = p.get('temperature')
            if unit == 'C' and t is not None:
                t = round((t - 32) * 5 / 9)
            if p.get('isDaytime'):
                days[d].update(hi=t, text=p.get('shortForecast', ''), icon=nws.sky_icon(p.get('shortForecast', '')),
                               pop=(p.get('probabilityOfPrecipitation') or {}).get('value') or 0)
            else:
                days[d]['lo'] = t
    except Exception as e:  # noqa: BLE001  Open-Meteo fills in
        print('week: NWS forecast', e, file=sys.stderr)
    if any('hi' not in v or 'lo' not in v for v in days.values()):
        url = 'https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode({
            'latitude': loc['lat'], 'longitude': loc['lon'], 'timezone': cfg['timezone'],
            'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'start_date': start.isoformat(), 'end_date': (start + datetime.timedelta(days=6)).isoformat()})
        dd = json.load(urllib.request.urlopen(url, timeout=20))['daily']
        for i, ds in enumerate(dd['time']):
            v = days.get(datetime.date.fromisoformat(ds))
            if v is None:
                continue
            if 'hi' not in v:
                entry = plan_data.WEATHER_CODES.get(dd['weather_code'][i], ('⛅', '', 'Changeable'))
                v.update(hi=_f(dd['temperature_2m_max'][i], unit), icon=entry[0], text=entry[2],
                         pop=dd['precipitation_probability_max'][i] or 0)
            if 'lo' not in v:
                v['lo'] = _f(dd['temperature_2m_min'][i], unit)
    return {d: v for d, v in days.items() if 'hi' in v}


def short_text(text):
    """NWS phrasing tidied for a narrow column: 'Chance Rain Showers' -> 'Chance rain showers'."""
    text = text.split(' then ')[0]
    return text[:1].upper() + text[1:].lower()


def clock_change(day, tz):
    a = datetime.datetime.combine(day - datetime.timedelta(days=1), datetime.time(12), tz).utcoffset()
    b = datetime.datetime.combine(day, datetime.time(12), tz).utcoffset()
    if b > a:
        return 'spring'
    return 'fall' if b < a else None


def build(cfg, now):
    tz = zoneinfo.ZoneInfo(cfg['timezone'])
    w = plan_data.words(cfg)
    start = week_start(now.date())
    dates = [start + datetime.timedelta(days=i) for i in range(7)]
    errors = []
    days = [{'date': d.isoformat(), 'weekday': WEEKDAYS[d.weekday()], 'day': d.day, 'pills': [], 'events': [],
             'weather': None} for d in dates]
    timed = {d: [] for d in dates}  # (start, end) datetimes, for the summary

    for c in cfg.get('calendars') or []:
        if not c.get('url') or c.get('role') == 'todos':
            continue
        try:
            cal = plan_data.fetch_calendar(c['url'])
        except Exception as e:  # noqa: BLE001  one broken calendar must not hide the others
            errors.append(f"Calendar {c.get('name')}: {e}")
            continue
        for d, day in zip(dates, days):
            events, _ = plan_data.calendar_entries(cal, c.get('color', 'green'), d, tz, w)
            for e in events:
                if e['start'] == w['all_day']:
                    day['pills'].append(e['title'])
                else:
                    day['events'].append({'sort': e['sort'], 'start': ampm(e['start']), 'title': e['title'],
                                          'color': e.get('color', 'green')})
                    begin = datetime.datetime.combine(d, datetime.time.fromisoformat(e['sort']), tz)
                    timed[d].append((begin, e['end_dt'] if e['end_dt'] and e['end_dt'] > begin else begin, e['title']))

    occasions = []
    for d, day in zip(dates, days):
        try:
            day['pills'] += [h['title'] for h in plan_data.holiday_entries(cfg, d, w)]
        except Exception:  # noqa: BLE001
            pass
        b = banner.birthday(cfg, d)
        if b:
            name = b.strip('🎂🎈 ').replace('Happy Birthday ', '').rstrip('!')
            day['pills'].insert(0, f'🎂 {name}')
            occasions.append(f'{name}’s birthday {WEEKDAYS[d.weekday()]}')
        greeting = banner.holidays(d.year).get(d)
        if greeting and not day['pills']:
            plain = greeting.split('!')[0].replace('Happy ', '').replace('Merry ', '').strip(' 🎉❤️☘️🐣🌷💐🇺🇸👔🎆🛠️🎃👻🦃🥧🎄🎁🥂✨')
            day['pills'].append(plain)
        if greeting:
            occasions.append(f"{greeting.strip(' 🎉❤️☘️🐣🌷💐🇺🇸👔🎆🛠️🎃👻🦃🥧🎄🎁🥂✨').split('!')[0]} {WEEKDAYS[d.weekday()]}")
        change = clock_change(d, tz)
        if change:
            day['pills'].insert(0, 'Clocks forward 1 hr' if change == 'spring' else 'Clocks back 1 hr')
        day['pills'] = list(dict.fromkeys(day['pills']))
        day['events'].sort(key=lambda e: e['sort'])

    wx = {}
    loc = cfg.get('weather') or {}
    if loc.get('enabled') and loc.get('lat') is not None:
        try:
            wx = weather(cfg, start, tz)
        except Exception as e:  # noqa: BLE001
            errors.append(f'Week weather: {e}')
        for d, day in zip(dates, days):
            if d in wx:
                v = wx[d]
                day['weather'] = {'icon': v['icon'], 'hi': v['hi'], 'lo': v.get('lo'), 'text': short_text(v['text']),
                                  'pop': v.get('pop') or 0}

    alerts = []
    if loc.get('lat') is not None and (cfg['weather'].get('stations') or 'K').startswith('K'):
        try:
            alerts = nws.alerts(loc['lat'], loc['lon'])
        except Exception:  # noqa: BLE001
            pass
    todos = {'open': 0, 'due': []}
    lists = (cfg.get('home_assistant') or {}).get('todo_lists') or []
    if lists and ha.configured(cfg):
        try:
            ids, _ = ha.resolve_todo_lists(lists, cfg)
            for items in ha.todo_items(ids, cfg).values():
                for i in items:
                    if not i['done']:
                        todos['open'] += 1
                        if i['due'] and start.isoformat() <= i['due'] <= dates[-1].isoformat():
                            todos['due'].append(i['summary'])
        except Exception as e:  # noqa: BLE001
            errors.append(f'To-do lists: {e}')

    data = {'start': start.isoformat(), 'end': dates[-1].isoformat(), 'lang': cfg.get('plan_lang', 'en'),
            'days': days, 'updated': now.strftime('%-I:%M %p'), 'errors': errors}
    data['summary'] = summary(dates, days, timed, wx, alerts, occasions, todos, tz)
    for day in days:
        for e in day['events']:
            e.pop('sort', None)
    if errors:
        print('week warnings:', errors, file=sys.stderr)
    return data


def summary(dates, days, timed, wx, alerts, occasions, todos, tz, limit=6):
    """Up to six one-line insights, most important first. Each rule speaks only when it applies."""
    out = []
    for a in alerts[:1]:
        ends = a.get('ends') or a.get('expires')
        until = ''
        if ends:
            e = datetime.datetime.fromisoformat(ends).astimezone(tz)
            until = f" until {e.strftime('%a %-I %p')}"
        out.append(('⚠️', a['event'] + until))
    for d in dates:
        change = clock_change(d, tz)
        if change:
            out.append(('🕰️', f"Clocks {'spring forward' if change == 'spring' else 'fall back'} "
                               f"{WEEKDAYS_LONG[d.weekday()]} 2 AM: {'lose' if change == 'spring' else 'gain'} an hour"))
    if occasions:
        out.append(('🎉', ' · '.join(occasions[:3])))
    early = [(s, d, t) for d in dates for s, _, t in timed[d] if s.time() < datetime.time(7)]
    if early:
        s, d, t = min(early, key=lambda x: x[0].time())
        out.append(('⏰', f"Early start {WEEKDAYS[d.weekday()]}: {t} at {s.strftime('%-I:%M %p')}"))
    busiest = max(dates, key=lambda d: len(timed[d]))
    if len(timed[busiest]) >= 4:
        first = min(s for s, _, _ in timed[busiest])
        last = max(e for _, e, _ in timed[busiest])
        out.append(('📅', f"Busiest day {WEEKDAYS[busiest.weekday()]}: {len(timed[busiest])} events, "
                           f"{first.strftime('%-I:%M %p')} to {last.strftime('%-I:%M %p')}"))
    if wx:
        wet = [(d, v) for d, v in sorted(wx.items()) if (v.get('pop') or 0) >= 40 or 'thunder' in v.get('text', '').lower()]
        warm = max(wx.items(), key=lambda kv: kv[1]['hi'])
        cool = min(wx.items(), key=lambda kv: kv[1]['hi'])
        bits = []
        if wet:
            worst = max(wet, key=lambda kv: kv[1].get('pop') or 0)
            bits.append(f"{short_text(worst[1]['text'])} {WEEKDAYS[worst[0].weekday()]} ({worst[1].get('pop') or 0}%)"
                        + (f" and {len(wet) - 1} more day{'s' if len(wet) > 2 else ''}" if len(wet) > 1 else ''))
        else:
            bits.append('Dry week')
        if warm[1]['hi'] >= 90:
            bits.append(f"hot {WEEKDAYS[warm[0].weekday()]} ({warm[1]['hi']}°)")
        else:
            bits.append(f"highs {cool[1]['hi']}–{warm[1]['hi']}°")
        out.append(('🌧️' if wet else '☀️', ' · '.join(bits)))
    free = [d for d in dates if not any(e.time() > datetime.time(17) or s.time() >= datetime.time(17)
                                        for s, e, _ in timed[d])]
    if free and len(free) < 7:
        out.append(('🌙', 'Free evenings: ' + ', '.join(WEEKDAYS[d.weekday()] for d in free)))
    elif len(free) == 7:
        out.append(('🌙', 'Every evening is free this week'))
    if todos['open']:
        due = todos['due']
        out.append(('✅', f"{todos['open']} open to-do{'s' if todos['open'] != 1 else ''}"
                          + (f" · due this week: {', '.join(due[:2])}" + (f' +{len(due) - 2}' if len(due) > 2 else '')
                             if due else '')))
    return out[:limit]


def demo(today):
    """Made-up week for previews and screenshots."""
    start = week_start(today)
    sample = [
        ('🌤️', 72, 56, 0, 'Mostly sunny', [], [('8:00 AM', 'Daycare drop-off', 'blue'), ('12:00 PM', 'Program review', 'orange'),
                                                ('6:30 PM', 'Swim lesson', 'green')]),
        ('☀️', 78, 58, 0, 'Sunny', [], [('6:10 AM', 'Flight to Denver', 'red'), ('8:00 AM', 'Daycare drop-off', 'blue'),
                                        ('7:00 PM', 'Book club', 'green')]),
        ('⛅', 74, 59, 10, 'Partly cloudy', [], [('8:00 AM', 'Daycare drop-off', 'blue'), ('9:30 AM', 'Pediatrician', 'green'),
                                                 ('1:00 PM', 'Panel prep', 'orange'), ('3:00 PM', 'Design review', 'orange'),
                                                 ('7:30 PM', 'Date night', 'green')]),
        ('☀️', 84, 61, 0, 'Sunny', [], [('8:00 AM', 'Daycare drop-off', 'blue'), ('5:00 PM', 'Hot tub service', 'grey')]),
        ('☁️', 70, 60, 30, 'Cloudy', [], [('8:00 AM', 'Daycare drop-off', 'blue'), ('6:00 PM', 'Dinner with friends', 'green')]),
        ('🌧️', 64, 55, 70, 'Rain likely', [], [('10:00 AM', 'Farmers market', 'green'), ('2:00 PM', 'Birthday party', 'green')]),
        ('🌤️', 68, 54, 10, 'Mostly sunny', ['Family day'], [('5:30 PM', 'Family dinner', 'green')]),
    ]
    days = []
    for i, (icon, hi, lo, pop, text, pills, events) in enumerate(sample):
        d = start + datetime.timedelta(days=i)
        days.append({'date': d.isoformat(), 'weekday': WEEKDAYS[i], 'day': d.day, 'pills': pills,
                     'events': [{'start': t, 'title': n, 'color': c} for t, n, c in events],
                     'weather': {'icon': icon, 'hi': hi, 'lo': lo, 'text': text, 'pop': pop}})
    return {'start': start.isoformat(), 'end': (start + datetime.timedelta(days=6)).isoformat(), 'lang': 'en',
            'days': days, 'updated': '5:00 PM', 'errors': [],
            'summary': [('⏰', 'Early start Tue: Flight to Denver at 6:10 AM'),
                        ('📅', 'Busiest day Wed: 5 events, 8:00 AM to 9:30 PM'),
                        ('🌧️', 'Rain likely Sat (70%) · highs 64–84°'),
                        ('🌙', 'Free evenings: Thu'),
                        ('✅', '4 open to-dos · due this week: Renew car registration')]}
