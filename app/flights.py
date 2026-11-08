"""Flights on the calendar: status for today's and tomorrow's flights, and a live tracker while one is
in the air.

Calendar: Google adds flights from Gmail as "Flight to Denver (UA 1234)"; any event with a flight number
in brackets (or "Flight ... UA1234") counts.

Data:
- status, times, gates, delays, airports: FlightAware AeroAPI (Personal tier; needs a key; about half a
  cent per status check, so a whole tracked flight costs well under $1 and fits the $5 monthly credit)
- live position, altitude, speeds: airplanes.live (free ADS-B, by callsign); AeroAPI's last position
  as a fallback
- flown path: AeroAPI track, refreshed every 15 minutes
"""
import datetime
import json
import math
import os
import re
import sys
import threading
import time
import zoneinfo

import requests

import config

AEROAPI = 'https://aeroapi.flightaware.com/aeroapi'
# Free, open ADS-B (no key). airplanes.live now refuses unregistered apps, so it is not used.
ADSB = [('adsb.lol', 'https://api.adsb.lol/v2/callsign/{}', 'https://api.adsb.lol/v2/reg/{}'),
        ('adsb.fi', 'https://opendata.adsb.fi/api/v2/callsign/{}', 'https://opendata.adsb.fi/api/v2/registration/{}')]
HEADERS = {'User-Agent': 'frame-daily-plan (github.com/jakehitz-dotcom/frame-daily-plan)'}
AIRPORTS = os.path.join(config.DATA, 'airports.json')
DEFAULTS = {'enabled': True, 'aeroapi_key': '', 'wake': True, 'after_arrival_minutes': 20,
            # Automatic look-ups only while one of these is home (nobody to see the TV otherwise)
            'presence': ['device_tracker.life360_jake_hitzeroth', 'device_tracker.life360_mike_schmer']}
# "(UA 1234)", "(UA1234)", "(B6 523)"; or "Flight … AS 1234" without brackets
IDENT_BRACKETS = re.compile(r'\(\s*([A-Z][A-Z0-9]|[A-Z0-9][A-Z])\s?(\d{1,4})\s*\)')
IDENT_FREE = re.compile(r'\b([A-Z][A-Z0-9]|[A-Z0-9][A-Z])\s?(\d{1,4})\b')
_cache = {}
_lock = threading.Lock()


def settings(cfg):
    return dict(DEFAULTS, **(cfg.get('flights') or {}))


def configured(cfg):
    s = settings(cfg)
    return bool(s['enabled'] and s['aeroapi_key'])


def someone_home(cfg):
    """True when any presence entity is 'home' in Home Assistant (checked once a minute). If Home
    Assistant cannot say, assume someone is: better a few cents than a missed flight."""
    import ha
    entities = settings(cfg).get('presence') or []
    if not entities or not ha.configured(cfg):
        return True

    def check():
        known = False
        for e in entities:
            try:
                st = ha.state(e, cfg).get('state')
            except Exception:  # noqa: BLE001
                continue
            if st == 'home':
                return True
            known = known or st not in (None, 'unknown', 'unavailable')
        return not known
    return _cached(('home',), 60, check)


def find_ident(title, *more):
    """'UA1234' from a calendar event, or None."""
    m = IDENT_BRACKETS.search(title or '')
    if not m and re.search(r'(?i)\bflight\b', ' '.join([title or ''] + [t or '' for t in more])):
        for text in [title] + list(more):
            m = IDENT_FREE.search(text or '')
            if m:
                break
    return f'{m.group(1)}{int(m.group(2))}' if m else None


def pretty_ident(ident):
    m = re.fullmatch(r'([A-Z0-9]{2,3}?)(\d+)', ident or '')
    return f'{m.group(1)} {m.group(2)}' if m else ident


# ── HTTP with a small time-based cache ──────────────────────────────────────────────────────────
def _cached(key, fresh, fn):
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < fresh:
        return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def _api(cfg, path, params=None, fresh=300):
    def get():
        r = requests.get(AEROAPI + path, params=params or {}, timeout=20,
                         headers=dict(HEADERS, **{'x-apikey': settings(cfg)['aeroapi_key']}))
        if r.status_code == 401:
            raise RuntimeError('FlightAware refused the API key')
        r.raise_for_status()
        return r.json()
    return _cached(('api', path, tuple(sorted((params or {}).items()))), fresh, get)


def _t(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None


# ── Calendar ────────────────────────────────────────────────────────────────────────────────────
def calendar_flights(cfg, now):
    """[{'ident', 'title', 'start'}] for flights on the calendars from yesterday to tomorrow
    (yesterday: a red-eye may still be in the air)."""
    import plan_data
    import recurring_ical_events
    tz = zoneinfo.ZoneInfo(cfg['timezone'])
    out = []
    for c in cfg.get('calendars') or []:
        if not c.get('url') or c.get('role') == 'todos':
            continue
        try:
            cal = _cached(('cal', c['url']), 600, lambda: plan_data.fetch_calendar(c['url']))
        except Exception as e:  # noqa: BLE001
            print('flights: calendar', c.get('name'), e, file=sys.stderr)
            continue
        start = datetime.datetime.combine(now.date() - datetime.timedelta(days=1), datetime.time(), tz)
        for ev in recurring_ical_events.of(cal).between(start, start + datetime.timedelta(days=3)):
            title = ' '.join(str(ev.get('SUMMARY', '')).split())
            ident = find_ident(title, str(ev.get('LOCATION', '')), str(ev.get('DESCRIPTION', '')))
            dt = ev['DTSTART'].dt
            if ident and isinstance(dt, datetime.datetime):
                out.append({'ident': ident, 'title': title, 'start': dt.astimezone(tz)})
    seen, unique = set(), []
    for f in sorted(out, key=lambda f: f['start']):
        if (f['ident'], f['start'].date()) not in seen:
            seen.add((f['ident'], f['start'].date()))
            unique.append(f)
    return unique


# ── FlightAware ─────────────────────────────────────────────────────────────────────────────────
def flight_for(cfg, ident, start, now=None):
    """The AeroAPI flight matching a calendar entry (scheduled departure closest to the event start).
    Checked every 30 minutes until 3 hours before departure, then every 5 minutes."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    soon = abs((start - now).total_seconds()) < 3 * 3600 or start < now
    day = start.astimezone(datetime.timezone.utc).date()
    params = {'start': (day - datetime.timedelta(days=1)).isoformat(),
              'end': (day + datetime.timedelta(days=2)).isoformat()}
    flights = _api(cfg, f'/flights/{ident}', params, fresh=300 if soon else 1800).get('flights') or []
    flights = [f for f in flights if f.get('scheduled_out') or f.get('scheduled_off')]
    if not flights:
        return None
    best = min(flights, key=lambda f: abs((_t(f.get('scheduled_out') or f['scheduled_off']) - start).total_seconds()))
    sched = _t(best.get('scheduled_out') or best['scheduled_off'])
    return best if abs((sched - start).total_seconds()) < 12 * 3600 else None


def airport(cfg, ap):
    """{'code', 'name', 'city', 'tz', 'lat', 'lon'} for an AeroAPI airport object; coordinates cached."""
    if not ap:
        return None
    code = ap.get('code_iata') or ap.get('code_icao') or ap.get('code')
    try:
        with open(AIRPORTS) as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    key = ap.get('code_icao') or ap.get('code')
    if key not in known:
        try:
            a = _api(cfg, f"/airports/{key}", fresh=10 ** 9)
            known[key] = {'lat': a.get('latitude'), 'lon': a.get('longitude')}
            os.makedirs(os.path.dirname(AIRPORTS), exist_ok=True)
            with open(AIRPORTS, 'w') as f:
                json.dump(known, f)
        except Exception as e:  # noqa: BLE001
            print('flights: airport', key, e, file=sys.stderr)
            known[key] = {}
    return {'code': code, 'name': ap.get('name') or code, 'city': ap.get('city') or '',
            'tz': ap.get('timezone') or 'UTC', **known.get(key, {})}


def status(fl, now=None):
    """(word, colour key, detail) e.g. ('Delayed', 'amber', 'departs 7:20 PM')."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    dep_delay = (fl.get('departure_delay') or 0) / 60
    arr_delay = (fl.get('arrival_delay') or 0) / 60
    if fl.get('cancelled'):
        return 'Cancelled', 'red'
    if fl.get('diverted'):
        return 'Diverted', 'red'
    if fl.get('actual_in') or fl.get('actual_on'):
        return 'Landed' if not fl.get('actual_in') else 'Arrived', 'blue'
    if fl.get('actual_off'):
        return ('Delayed' if arr_delay >= 15 else 'On time') + ' · in flight', 'amber' if arr_delay >= 15 else 'green'
    if fl.get('actual_out'):
        return 'Taxiing', 'cyan'
    if dep_delay >= 15:
        return 'Delayed', 'amber'
    return 'On time', 'green'


def _local(dt, tz_name):
    return dt.astimezone(zoneinfo.ZoneInfo(tz_name)) if dt else None


def fmt_time(dt, tz_name, home_tz=None):
    """'6:05 PM' in the airport's zone, with its abbreviation when it differs from home."""
    if not dt:
        return None
    t = _local(dt, tz_name)
    s = t.strftime('%-I:%M %p')
    if home_tz and t.utcoffset() != dt.astimezone(home_tz).utcoffset():
        s += ' ' + t.tzname()
    return s


def planner_line(fl, home_tz):
    """One line for the plan: 'On time · departs 6:05 PM · gate 71A' (times at the departure airport)."""
    word, colour = status(fl)
    org_tz = (fl.get('origin') or {}).get('timezone') or str(home_tz)
    dst_tz = (fl.get('destination') or {}).get('timezone') or str(home_tz)
    parts = [word]
    if word == 'Delayed':
        parts[0] = f"Delayed to {fmt_time(_t(fl.get('estimated_out')), org_tz, home_tz)}"
    elif word in ('On time',):
        parts.append('departs ' + fmt_time(_t(fl.get('estimated_out') or fl.get('scheduled_out')), org_tz, home_tz))
    elif word.endswith('in flight') or word == 'Taxiing':
        parts.append('lands ' + fmt_time(_t(fl.get('estimated_on') or fl.get('estimated_in') or fl.get('scheduled_in')),
                                         dst_tz, home_tz))
    elif word in ('Arrived', 'Landed'):
        parts.append(fmt_time(_t(fl.get('actual_in') or fl.get('actual_on')), dst_tz, home_tz))
    if fl.get('gate_origin') and word in ('On time', 'Delayed'):
        parts.append(f"gate {fl['gate_origin']}")
    return {'text': '  ·  '.join(p for p in parts if p), 'colour': colour}


def in_air(cfg, fl, now, moving=False):
    """True from pushback until a little after arriving at the gate (not when cancelled). `moving`: free
    ADS-B already sees this flight taxiing or flying, before FlightAware has recorded the pushback."""
    if not fl or fl.get('cancelled'):
        return False
    done = _t(fl.get('actual_in') or fl.get('actual_on'))
    if done and now >= done + datetime.timedelta(minutes=settings(cfg)['after_arrival_minutes']):
        return False
    started = _t(fl.get('actual_out') or fl.get('actual_off'))
    return bool(moving) or bool(started and started <= now)


def active(cfg, now=None, moving=()):
    """The calendar flight being flown right now (its AeroAPI flight plus the calendar entry), or None.
    moving: FlightAware ids that free ADS-B already sees on the move."""
    if not configured(cfg) or not someone_home(cfg):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for cf in calendar_flights(cfg, now):
        if not (cf['start'] - datetime.timedelta(hours=1) <= now <= cf['start'] + datetime.timedelta(hours=20)):
            continue
        try:
            fl = flight_for(cfg, cf['ident'], cf['start'], now)
        except Exception as e:  # noqa: BLE001
            print('flights:', cf['ident'], e, file=sys.stderr)
            continue
        if in_air(cfg, fl, now, fl.get('fa_flight_id') in moving if fl else False):
            return {'calendar': cf, 'flight': fl}
    return None


def watched(cfg, now=None):
    """The calendar flight worth watching for a change: from an hour before departure until shortly
    after it reaches the gate (cached FlightAware data only: no extra cost)."""
    if not configured(cfg) or not someone_home(cfg):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for cf in calendar_flights(cfg, now):
        if not (cf['start'] - datetime.timedelta(hours=1) <= now <= cf['start'] + datetime.timedelta(hours=20)):
            continue
        try:
            fl = flight_for(cfg, cf['ident'], cf['start'], now)
        except Exception:  # noqa: BLE001
            continue
        if not fl or fl.get('cancelled'):
            continue
        done = _t(fl.get('actual_in'))
        if done and now > done + datetime.timedelta(minutes=settings(cfg)['after_arrival_minutes']):
            continue
        return {'calendar': cf, 'flight': fl}
    return None


def phase(cfg, fl):
    """What free ADS-B says the aircraft is doing: 'parked', 'taxiing', 'airborne', or None (not seen).
    Costs nothing, so it can be checked every 30 seconds."""
    _, a = _adsb(cfg, fl)
    if not a:
        return None
    flying_as = (a.get('flight') or '').strip()
    if flying_as and flying_as not in {fl.get('atc_ident'), fl.get('ident_icao'), fl.get('ident')}:
        return None  # found by tail number but on another leg (e.g. still arriving from its last trip)
    if a.get('alt_baro') == 'ground' or (isinstance(a.get('alt_baro'), (int, float)) and a['alt_baro'] < 100
                                          and (a.get('gs') or 0) < 60):
        return 'taxiing' if (a.get('gs') or 0) >= 5 else 'parked'
    return 'airborne'


def refresh_now(fl):
    """Forget the cached FlightAware answers for this flight, so the next look is fresh (one call)."""
    with _lock:
        for key in list(_cache):
            if key[0] == 'api' and (f"/flights/{fl.get('ident_iata')}" in key[1] or str(fl.get('fa_flight_id')) in key[1]
                                    or any(f"/flights/{i}" == key[1] for i in (fl.get('ident'), fl.get('ident_icao')))):
                _cache.pop(key, None)


def next_flight(cfg, now=None):
    """The flight in the air, else the next calendar flight today or tomorrow (for Frame Remote)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    a = active(cfg, now)
    if a:
        return a
    for cf in calendar_flights(cfg, now):
        if cf['start'] > now - datetime.timedelta(hours=2):
            try:
                fl = flight_for(cfg, cf['ident'], cf['start'], now)
            except Exception:  # noqa: BLE001
                fl = None
            if fl:
                return {'calendar': cf, 'flight': fl}
    return None


# ── Live position ───────────────────────────────────────────────────────────────────────────────
def plausible(cfg, fl, lat, lon):
    """A real position near this route: not 0,0 ("null island", what an empty fix looks like), and within
    the route length plus 400 miles of the departure airport."""
    if lat is None or lon is None or (abs(lat) < 1 and abs(lon) < 1):
        return False
    org = airport(cfg, fl.get('origin')) or {}
    dst = airport(cfg, fl.get('destination')) or {}
    if org.get('lat') is None or dst.get('lat') is None:
        return True
    span = miles((org['lat'], org['lon']), (dst['lat'], dst['lon']))
    return miles((org['lat'], org['lon']), (lat, lon)) <= span + 400


def _adsb(cfg, fl):
    """Look the aircraft up by its radio callsign (a United Express flight flies as SkyWest "SKW…",
    not "UAL…"), its flight number, then its registration (tail number)."""
    callsigns = [c for c in dict.fromkeys([fl.get('atc_ident'), fl.get('ident_icao'), fl.get('ident')]) if c]
    reg = fl.get('registration')
    for name, by_cs, by_reg in ADSB:
        urls = [by_cs.format(c) for c in callsigns] + ([by_reg.format(reg)] if reg else [])
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                r.raise_for_status()
                for a in r.json().get('ac') or []:
                    if plausible(cfg, fl, a.get('lat'), a.get('lon')):
                        return name, a
            except Exception as e:  # noqa: BLE001  try the next way / the next service
                print('flights:', name, url.rsplit('/', 1)[-1], e, file=sys.stderr)
    return None, None


def live_position(cfg, fl):
    """{'lat', 'lon', 'alt_ft', 'gs_mph', 'tas_mph', 'heading', 'vrate', 'source', 'at'} or None."""
    name, a = _adsb(cfg, fl)
    if a:
        alt = a.get('alt_baro')
        return {'lat': a['lat'], 'lon': a['lon'], 'alt_ft': alt if isinstance(alt, (int, float)) else 0,
                'gs_mph': round(a['gs'] * 1.15078) if a.get('gs') is not None else None,
                'tas_mph': round(a['tas'] * 1.15078) if a.get('tas') is not None else None,
                'heading': a.get('track') or a.get('true_heading') or 0,
                'vrate': a.get('baro_rate') or a.get('geom_rate') or 0,
                'source': name, 'at': time.time() - (a.get('seen_pos') or 0)}
    try:
        p = _api(cfg, f"/flights/{fl['fa_flight_id']}/position", fresh=240).get('last_position') or {}
        if plausible(cfg, fl, p.get('latitude'), p.get('longitude')):
            return {'lat': p['latitude'], 'lon': p['longitude'], 'alt_ft': (p.get('altitude') or 0) * 100,
                    'gs_mph': round((p.get('groundspeed') or 0) * 1.15078), 'tas_mph': None,
                    'heading': p.get('heading') or 0,
                    'vrate': {'C': 1000, 'D': -1000}.get(p.get('altitude_change'), 0),
                    'source': 'FlightAware', 'at': _t(p.get('timestamp')).timestamp() if p.get('timestamp') else None}
    except Exception as e:  # noqa: BLE001
        print('flights: position', e, file=sys.stderr)
    return None


def track(cfg, fl):
    try:
        pts = _api(cfg, f"/flights/{fl['fa_flight_id']}/track", fresh=900).get('positions') or []
        return [(p['latitude'], p['longitude']) for p in pts if plausible(cfg, fl, p.get('latitude'), p.get('longitude'))]
    except Exception as e:  # noqa: BLE001
        print('flights: track', e, file=sys.stderr)
        return []


# ── Geometry ────────────────────────────────────────────────────────────────────────────────────
def great_circle(a, b, n=160):
    """Points along the great circle from a to b (lat, lon), longitudes kept continuous."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 +
                                math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    if d == 0:
        return [a, b]
    out, prev = [], None
    for i in range(n + 1):
        f = i / n
        A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = A * math.cos(la1) * math.cos(lo1) + B * math.cos(la2) * math.cos(lo2)
        y = A * math.cos(la1) * math.sin(lo1) + B * math.cos(la2) * math.sin(lo2)
        z = A * math.sin(la1) + B * math.sin(la2)
        lat, lon = math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))
        if prev is not None:
            while lon - prev > 180:
                lon -= 360
            while lon - prev < -180:
                lon += 360
        out.append((lat, lon))
        prev = lon
    return out


def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


# ── Everything the tracker screen shows ─────────────────────────────────────────────────────────
def screen(cfg, found, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    home_tz = zoneinfo.ZoneInfo(cfg['timezone'])
    fl, cf = found['flight'], found['calendar']
    org, dst = airport(cfg, fl.get('origin')), airport(cfg, fl.get('destination'))
    pos = live_position(cfg, fl) if in_air(cfg, fl, now) else None
    if not pos and not fl.get('actual_off') and org and org.get('lat') is not None:
        pos = {'lat': org['lat'], 'lon': org['lon'], 'alt_ft': 0, 'gs_mph': None, 'tas_mph': None, 'heading':
               0, 'vrate': 0, 'source': 'at the gate', 'at': None, 'ground': True}
    path = track(cfg, fl) if fl.get('actual_off') else []
    word, colour = status(fl, now)
    dep_s, arr_s = _t(fl.get('scheduled_out')), _t(fl.get('scheduled_in'))
    dep_a = _t(fl.get('actual_out')) or None
    off_a = _t(fl.get('actual_off'))
    arr_e = _t(fl.get('actual_in')) or _t(fl.get('estimated_in')) or arr_s
    total = remaining = flown = None
    if org and dst and org.get('lat') is not None and dst.get('lat') is not None:
        total = miles((org['lat'], org['lon']), (dst['lat'], dst['lon']))
        here = (pos['lat'], pos['lon']) if pos else (path[-1] if path else None)
        if fl.get('actual_in') or fl.get('actual_on'):
            remaining = 0
        elif here:
            remaining = min(total, miles(here, (dst['lat'], dst['lon'])))
        elif not off_a:
            remaining = total
        flown = None if remaining is None else max(0, total - remaining)
    progress = fl.get('progress_percent')
    if progress is None and total and remaining is not None:
        progress = round(100 * (1 - remaining / total))
    left = (arr_e - now).total_seconds() / 60 if arr_e and not fl.get('actual_in') else None
    return {
        'ident': pretty_ident(fl.get('ident_iata') or cf['ident']), 'callsign': fl.get('ident_icao') or '',
        'title': cf['title'], 'status': word, 'colour': colour, 'aircraft': fl.get('aircraft_type') or '',
        'registration': fl.get('registration') or '', 'operator': fl.get('operator_iata') or fl.get('operator') or '',
        'origin': org, 'destination': dst,
        'dep_sched': fmt_time(dep_s, org['tz'] if org else 'UTC', home_tz),
        'dep_actual': fmt_time(dep_a, org['tz'] if org else 'UTC', home_tz),
        'takeoff': fmt_time(off_a, org['tz'] if org else 'UTC', home_tz),
        'arr_sched': fmt_time(arr_s, dst['tz'] if dst else 'UTC', home_tz),
        'arr_est': fmt_time(arr_e, dst['tz'] if dst else 'UTC', home_tz),
        'arr_home': arr_e.astimezone(home_tz).strftime('%-I:%M %p') if arr_e and dst and
        _local(arr_e, dst['tz']).utcoffset() != arr_e.astimezone(home_tz).utcoffset() else None,
        'arrived': bool(fl.get('actual_in') or fl.get('actual_on')),
        'gate_origin': fl.get('gate_origin'), 'terminal_origin': fl.get('terminal_origin'),
        'gate_destination': fl.get('gate_destination'), 'terminal_destination': fl.get('terminal_destination'),
        'baggage': fl.get('baggage_claim'),
        'dep_delay': round((fl.get('departure_delay') or 0) / 60), 'arr_delay': round((fl.get('arrival_delay') or 0) / 60),
        'position': pos, 'path': path, 'total_mi': round(total) if total else None,
        'remaining_mi': round(remaining) if remaining is not None else None,
        'flown_mi': round(flown) if flown is not None else None, 'progress': progress,
        'minutes_left': round(left) if left is not None and left > 0 else None,
        'filed_alt': (fl.get('filed_altitude') or 0) * 100 or None,
        'updated': now.astimezone(home_tz).strftime('%-I:%M %p'),
    }
