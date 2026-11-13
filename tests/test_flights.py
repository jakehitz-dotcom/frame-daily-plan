import datetime
import zoneinfo

import flights
import jobs
import radar
import render

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
UTC = datetime.timezone.utc
CFG = {'timezone': 'America/Los_Angeles', 'tv_ip': '192.168.1.50', 'home_assistant': {}, 'calendars': [],
       'flights': {'aeroapi_key': 'k'},
       'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4], 'refresh_minutes': 5}}
NOW = datetime.datetime(2026, 11, 8, 19, 30, tzinfo=TZ)


def fl(**k):
    base = {'ident_iata': 'UA1234', 'ident_icao': 'UAL1234', 'fa_flight_id': 'UAL1234-1-0',
            'origin': {'code_iata': 'LAX', 'code_icao': 'KLAX', 'timezone': 'America/Los_Angeles', 'city': 'Los Angeles'},
            'destination': {'code_iata': 'DEN', 'code_icao': 'KDEN', 'timezone': 'America/Denver', 'city': 'Denver'},
            'scheduled_out': '2026-11-09T02:05:00Z', 'estimated_out': '2026-11-09T02:05:00Z',
            'scheduled_in': '2026-11-09T04:30:00Z', 'estimated_in': '2026-11-09T04:30:00Z',
            'departure_delay': 0, 'arrival_delay': 0, 'gate_origin': '71A'}
    base.update(k)
    return base


def test_flight_numbers_from_google_calendar_titles():
    assert flights.find_ident('Flight to Denver (UA 1234)') == 'UA1234'
    assert flights.find_ident('Flight to New York (B6 23)') == 'B623'
    assert flights.find_ident('Flight to Seattle', 'Alaska Airlines AS 0331') == 'AS331'
    assert flights.find_ident('Dinner (party of 4)') is None
    assert flights.find_ident('Lunch with AB 12') is None          # no "flight": a flight number alone is not enough
    assert flights.pretty_ident('UA1234') == 'UA 1234'


def test_planner_lines():
    assert flights.planner_line(fl(), TZ)['text'] == 'On time  ·  departs 6:05 PM  ·  gate 71A'
    late = flights.planner_line(fl(departure_delay=3000, estimated_out='2026-11-09T02:55:00Z'), TZ)
    assert (late['text'], late['colour']) == ('Delayed to 6:55 PM  ·  gate 71A', 'amber')
    assert late['times'] == '18:05 (PST) – 21:30 (MST)'
    assert flights.planner_line(fl(cancelled=True), TZ)['text'] == 'Cancelled'
    air = flights.planner_line(fl(actual_out='2026-11-09T02:10:00Z', actual_off='2026-11-09T02:25:00Z',
                                  estimated_on='2026-11-09T04:20:00Z'), TZ)
    assert air['text'] == 'On time · in flight  ·  lands 9:20 PM MST'


def test_in_the_air_from_pushback_to_just_after_the_gate():
    f = fl(actual_out='2026-11-09T02:10:00Z')
    t = lambda h, m: datetime.datetime(2026, 11, 9, h, m, tzinfo=UTC)  # noqa: E731
    assert not flights.in_air(CFG, fl(), t(2, 30))
    assert flights.in_air(CFG, f, t(2, 30))
    landed = dict(f, actual_in='2026-11-09T04:25:00Z')
    assert flights.in_air(CFG, landed, t(4, 40)) and not flights.in_air(CFG, landed, t(4, 50))
    assert not flights.in_air(CFG, dict(f, cancelled=True), t(2, 30))


def test_the_matching_flight_is_the_one_nearest_the_calendar_time(monkeypatch):
    other = fl(fa_flight_id='other', scheduled_out='2026-11-10T02:05:00Z')
    monkeypatch.setattr(flights, '_api', lambda cfg, path, params=None, fresh=0: {'flights': [other, fl()]})
    start = datetime.datetime(2026, 11, 8, 18, 5, tzinfo=TZ)
    assert flights.flight_for(CFG, 'UA1234', start, NOW)['fa_flight_id'] == 'UAL1234-1-0'


def test_the_tracker_outranks_the_automatic_screens():
    t = NOW.timestamp()
    found = {'flight': fl(), 'calendar': {}}
    sev = {'active': True, 'signature': 'rain'}
    assert jobs._due(CFG, {'showing': 'severe'}, NOW, sev, bottle=400, flight=found) == 'flight'
    up = {'showing': 'flight', 'flight_id': 'UAL1234-1-0', 'shown_at': t}
    assert jobs._due(CFG, up, NOW, sev, flight=found) is None
    assert jobs._due(CFG, up, NOW + datetime.timedelta(minutes=3), sev, flight=found) == 'flight_refresh'
    assert jobs._due(CFG, dict(up, manual_until=t + 900, showing='recipe'), NOW, sev, flight=found) is None
    assert jobs._due(CFG, up, NOW, None) == 'restore'                  # landed: back to the artwork
    assert jobs._due(CFG, up, NOW, sev) == 'severe'                    # or to whatever the rules want


def test_great_circle_and_distance():
    pts = flights.great_circle((33.94, -118.41), (39.86, -104.67))
    assert abs(pts[0][1] + 118.41) < 1e-6 and abs(pts[-1][1] + 104.67) < 1e-6
    assert 855 < flights.miles((33.94, -118.41), (39.86, -104.67)) < 870
    pac = flights.great_circle((21.32, -157.92), (-33.95, 151.18))     # Honolulu to Sydney crosses 180°
    assert all(abs(b[1] - a[1]) < 10 for a, b in zip(pac, pac[1:]))


def test_tracker_screen_renders_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(radar, '_get', lambda *a, **k: (_ for _ in ()).throw(OSError('offline')))
    org = {'code': 'LAX', 'city': 'Los Angeles', 'tz': 'America/Los_Angeles', 'lat': 33.94, 'lon': -118.41}
    dst = {'code': 'DEN', 'city': 'Denver', 'tz': 'America/Denver', 'lat': 39.86, 'lon': -104.67}
    fd = {'ident': 'UA 1234', 'callsign': 'UAL1234', 'title': 'Flight', 'status': 'On time · in flight', 'colour': 'green',
          'aircraft': 'B39M', 'registration': '', 'operator': 'UA', 'origin': org, 'destination': dst,
          'dep_sched': '6:05 PM', 'dep_actual': '6:12 PM', 'takeoff': None, 'arr_sched': '9:30 PM MST',
          'arr_est': '9:24 PM MST', 'arr_home': '8:24 PM', 'arrived': False, 'dep_delay': 7, 'arr_delay': -6,
          'position': {'lat': 37.0, 'lon': -112.0, 'alt_ft': 36000, 'gs_mph': 548, 'tas_mph': None, 'heading': 52,
                       'vrate': 1200, 'source': 'airplanes.live'},
          'path': [], 'total_mi': 862, 'flown_mi': 500, 'progress': 58, 'minutes_left': 52, 'updated': '7:32 PM'}
    render.render_flight(fd, str(tmp_path / 'f.jpg'))
    assert (tmp_path / 'f.jpg').stat().st_size > 50_000


def test_nothing_is_looked_up_while_nobody_is_home(monkeypatch):
    import ha
    flights._cache.clear()
    states = {'device_tracker.life360_jake_hitzeroth': 'not_home', 'device_tracker.life360_mike_schmer': 'Work'}
    monkeypatch.setattr(ha, 'configured', lambda cfg: True)
    monkeypatch.setattr(ha, 'state', lambda e, cfg: {'state': states[e]})
    called = []
    monkeypatch.setattr(flights, 'calendar_flights', lambda cfg, now: called.append(1) or [])
    assert not flights.someone_home(CFG) and flights.active(CFG) is None and not called
    flights._cache.clear()
    states['device_tracker.life360_mike_schmer'] = 'home'
    assert flights.someone_home(CFG)
    flights.active(CFG)
    assert called
    flights._cache.clear()
    states.update({k: 'unavailable' for k in states})
    assert flights.someone_home(CFG)                     # Home Assistant can't say: assume someone is
    flights._cache.clear()


def test_positions_at_null_island_or_off_route_are_rejected(monkeypatch):
    monkeypatch.setattr(flights, 'airport', lambda cfg, ap: {'LAX': {'lat': 33.94, 'lon': -118.41},
                                                              'DEN': {'lat': 39.86, 'lon': -104.67}}[ap['code_iata']])
    assert flights.plausible(CFG, fl(), 37.0, -112.0)
    assert not flights.plausible(CFG, fl(), 0.0, 0.0)
    assert not flights.plausible(CFG, fl(), 51.5, -0.1)                  # London: not on LAX-DEN


def test_adsb_lookup_tries_atc_callsign_then_tail_number(monkeypatch):
    monkeypatch.setattr(flights, 'plausible', lambda cfg, f, lat, lon: lat is not None and abs(lat) > 1)
    asked = []

    class R:
        def __init__(self, ac):
            self.ac = ac

        def raise_for_status(self):
            pass

        def json(self):
            return {'ac': self.ac}

    def get(url, headers=None, timeout=None):
        asked.append(url.rsplit('/', 1)[-1])
        return R([{'lat': 45.9, 'lon': -111.4, 'gs': 300, 'alt_baro': 12000, 'track': 200}] if url.endswith('N637SY') else
                 [{'lat': 0, 'lon': 0}])
    monkeypatch.setattr(flights.requests, 'get', get)
    name, a = flights._adsb(CFG, fl(atc_ident='SKW5429', registration='N637SY'))
    assert asked == ['SKW5429', 'UAL1234', 'N637SY'] and name == 'adsb.lol' and a['lat'] == 45.9


def test_phase_from_adsb(monkeypatch):
    f = fl(atc_ident='SKW5429')
    for a, want in (({'flight': 'SKW5429 ', 'alt_baro': 'ground', 'gs': 0}, 'parked'),
                    ({'flight': 'SKW5429', 'alt_baro': 'ground', 'gs': 14}, 'taxiing'),
                    ({'flight': 'SKW5429', 'alt_baro': 12000, 'gs': 280}, 'airborne'),
                    ({'flight': 'SKW3305', 'alt_baro': 'ground', 'gs': 12}, None)):   # same tail, earlier leg
        monkeypatch.setattr(flights, '_adsb', lambda cfg, x, a=a: ('adsb.lol', a))
        assert flights.phase(CFG, f) == want


def test_moving_on_adsb_starts_the_tracker_before_flightaware_catches_up():
    t = datetime.datetime(2026, 11, 9, 2, 8, tzinfo=UTC)
    assert not flights.in_air(CFG, fl(), t) and flights.in_air(CFG, fl(), t, moving=True)
    landed = fl(actual_out='2026-11-09T02:10:00Z', actual_in='2026-11-09T04:25:00Z')
    assert not flights.in_air(CFG, landed, datetime.datetime(2026, 11, 9, 5, 0, tzinfo=UTC), moving=True)


def test_a_phase_change_needs_two_looks_then_forces_a_refresh(monkeypatch):
    seen = iter(['parked', 'taxiing', 'taxiing'])
    w = {'flight': fl(), 'calendar': {'ident': 'UA1234'}}
    monkeypatch.setattr(flights, 'watched_all', lambda cfg, now: [w])
    monkeypatch.setattr(flights, 'phase', lambda cfg, f: next(seen))
    refreshed = []
    monkeypatch.setattr(flights, 'refresh_now', lambda f: refreshed.append(1))
    monkeypatch.setattr(jobs, 'log', lambda m: None)
    memo = {}
    for _ in range(3):
        memo['phase_at'] = 0
        jobs.watch_flight(CFG, memo, NOW)
    assert refreshed == [1] and memo['phases']['UAL1234-1-0']['phase'] == 'taxiing' and memo['flight_force']

def test_a_watched_aircraft_in_the_air_is_found_and_tracked(monkeypatch):
    flights._tails.clear()
    flights._cache.clear()
    monkeypatch.setattr(flights, 'someone_home', lambda cfg: True)
    fixes = iter([{'lat': 33.9, 'lon': -118.2, 'alt_baro': 3000, 'gs': 105, 'track': 280, 't': 'C172'},
                  {'lat': 33.95, 'lon': -118.3, 'alt_baro': 4500, 'gs': 110, 'track': 285, 't': 'C172'},
                  {'lat': 33.97, 'lon': -118.4, 'alt_baro': 'ground', 'gs': 12},
                  {'lat': 33.97, 'lon': -118.4, 'alt_baro': 'ground', 'gs': 0}])
    monkeypatch.setattr(flights, '_adsb_reg', lambda tail: ('adsb.lol', next(fixes)))
    cfg = dict(CFG, weather={'lat': 33.773, 'lon': -118.36})
    t0 = 1_800_000_000
    found = flights.tail_active(cfg, t0)
    assert found['tail'] == 'N8333X' and found['flight']['fa_flight_id'] == 'tail:N8333X'
    flights.tail_active(cfg, t0 + 60)
    scr = flights.tail_screen(cfg, found)
    assert len(scr['path']) == 2 and scr['max_alt'] == 4500 and scr['from_home_dir'] in ('N', 'NE', 'NW')
    assert flights.tail_active(cfg, t0 + 120) is not None              # just landed: kept a few minutes
    assert flights.tail_active(cfg, t0 + 60 + 241) is None             # then off the screen
    flights._tails.clear()


def test_a_watched_aircraft_outranks_a_calendar_flight_on_screen():
    tail = {'kind': 'tail', 'flight': {'fa_flight_id': 'tail:N8333X'}, 'calendar': {'ident': 'N8333X'}, 'state': {}}
    up = {'showing': 'flight', 'flight_id': 'UAL1234-1-0', 'shown_at': NOW.timestamp()}
    assert jobs._due(CFG, up, NOW, None, flight=tail) == 'flight'       # switches from the airliner to the Cessna


def test_flightaware_is_asked_less_often_once_airborne(monkeypatch):
    asked = []
    airborne = fl(actual_out='2026-11-09T02:10:00Z', actual_off='2026-11-09T02:25:00Z')
    monkeypatch.setattr(flights, '_api', lambda cfg, path, params=None, fresh=0: asked.append(fresh) or {'flights': [airborne]})
    start = datetime.datetime(2026, 11, 8, 18, 5, tzinfo=TZ)
    flights._cruising.discard('UA1234')
    flights.flight_for(CFG, 'UA1234', start, NOW)
    flights.flight_for(CFG, 'UA1234', start, NOW)
    assert asked == [300, 1200]
    flights._cruising.discard('UA1234')



def test_tomorrows_flight_window_stays_within_flightawares_limit(monkeypatch):
    asked = []
    monkeypatch.setattr(flights, '_api', lambda cfg, path, params=None, fresh=0: asked.append(params) or {'flights': []})
    now = datetime.datetime(2026, 11, 13, 4, 0, tzinfo=TZ)
    flights.flight_for(CFG, 'UA4705', datetime.datetime(2026, 11, 14, 19, 0, tzinfo=TZ), now)
    end = datetime.datetime.fromisoformat(asked[0]['end'].replace('Z', '+00:00'))
    assert end <= now + datetime.timedelta(hours=48) and asked[0]['start'] < asked[0]['end']


def test_overlapping_flights_first_airborne_stays_until_it_lands(monkeypatch):
    t = lambda h, m: datetime.datetime(2026, 11, 13, h, m, tzinfo=UTC)  # noqa: E731
    a = fl(fa_flight_id='A', actual_out='2026-11-13T15:00:00Z', actual_off='2026-11-13T15:20:00Z')
    b = fl(fa_flight_id='B', ident_iata='AS331', actual_out='2026-11-13T15:30:00Z', actual_off='2026-11-13T15:45:00Z')
    pair = [({'ident': 'UA1234'}, a), ({'ident': 'AS331'}, b)]
    monkeypatch.setattr(flights, 'someone_home', lambda cfg: True)
    monkeypatch.setattr(flights, '_candidates', lambda cfg, now: pair)
    assert flights.active(CFG, t(16, 0))['flight']['fa_flight_id'] == 'A'       # both up: the first one up
    pair[0] = ({'ident': 'UA1234'}, dict(a, actual_on='2026-11-13T17:00:00Z'))
    assert flights.active(CFG, t(17, 5))['flight']['fa_flight_id'] == 'B'       # A landed: switch to B
    pair[1] = ({'ident': 'AS331'}, dict(b, actual_off=None))                     # B only taxiing, A just landed
    assert flights.active(CFG, t(17, 5))['flight']['fa_flight_id'] == 'B'


def test_a_flight_47_and_a_half_hours_away_is_still_in_the_window(monkeypatch):
    asked = []
    monkeypatch.setattr(flights, '_api', lambda cfg, path, params=None, fresh=0: asked.append(params) or {'flights': []})
    now = datetime.datetime(2026, 11, 13, 0, 15, tzinfo=TZ)
    dep = datetime.datetime(2026, 11, 14, 23, 45, tzinfo=TZ)                  # 47.5 h ahead
    flights.flight_for(CFG, 'UA4705', dep, now)
    end = datetime.datetime.fromisoformat(asked[0]['end'].replace('Z', '+00:00'))
    assert dep < end <= now + datetime.timedelta(hours=48)


def test_flight_times_in_each_airports_zone():
    den = fl(scheduled_out='2026-11-13T22:29:00Z', scheduled_in='2026-11-14T00:56:00Z')
    assert flights.local_span(den, TZ) == '14:29 (PST) – 17:56 (MST)'
    sfo = fl(destination={'code_iata': 'SFO', 'timezone': 'America/Los_Angeles'})
    assert flights.local_span(sfo, TZ) is None                     # all on Pacific time: the calendar's times stand
    assert flights.planner_line(den, TZ)['times'] == '14:29 (PST) – 17:56 (MST)'


def test_inbound_flight_running_late_is_flagged(monkeypatch):
    now = datetime.datetime(2026, 11, 8, 15, 0, tzinfo=TZ)
    mine = fl(inbound_fa_flight_id='UAL812-1-0')                       # departs 18:05 PST (02:05Z)
    prev = {'ident_iata': 'UA812', 'origin': {'code_iata': 'DEN'}, 'scheduled_in': '2026-11-09T01:00:00Z',
            'estimated_in': '2026-11-09T01:42:00Z'}                    # lands 17:42: 23 min before departure
    monkeypatch.setattr(flights, '_api', lambda cfg, path, params=None, fresh=0: {'flights': [prev]})
    inb = flights.inbound(CFG, mine, now)
    assert inb['gap'] == 23 and inb['late'] and inb['ident'] == 'UA 812'
    line = flights.planner_line(mine, TZ, inb)
    assert line['text'].startswith('Inbound delayed · lands 5:42 PM, 23 min before departure') and line['colour'] == 'amber'
    prev['estimated_in'] = '2026-11-09T00:55:00Z'                        # 70 min: plenty
    assert not flights.inbound(CFG, mine, now)['late']
    assert flights.planner_line(mine, TZ, flights.inbound(CFG, mine, now))['text'].startswith('On time')
    prev['estimated_in'] = '2026-11-09T02:20:00Z'                        # after our departure time
    assert flights.planner_line(mine, TZ, flights.inbound(CFG, mine, now))['colour'] == 'red'
    assert flights.inbound(CFG, dict(mine, actual_out='2026-11-09T02:10:00Z'), now) is None   # already left
    assert flights.inbound(CFG, mine, now - datetime.timedelta(hours=10)) is None             # too early to tell
