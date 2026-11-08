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
    assert late == {'text': 'Delayed to 6:55 PM  ·  gate 71A', 'colour': 'amber'}
    assert flights.planner_line(fl(cancelled=True), TZ) == {'text': 'Cancelled', 'colour': 'red'}
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
