import datetime
import io
import json
import zoneinfo

import icalendar

import config
import jobs
import nws
import plan_data
import render
import week

LA = zoneinfo.ZoneInfo('America/Los_Angeles')
D = datetime.date

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/Los_Angeles:20261103T061000
DTEND;TZID=America/Los_Angeles:20261103T090000
SUMMARY:Flight to Denver
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=America/Los_Angeles:20261102T080000
DTEND;TZID=America/Los_Angeles:20261102T081500
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Daycare drop-off
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;TZID=America/Los_Angeles:20261104T093000
DTEND;TZID=America/Los_Angeles:20261104T103000
SUMMARY:Pediatrician
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=America/Los_Angeles:20261104T130000
DTEND;TZID=America/Los_Angeles:20261104T140000
SUMMARY:Panel prep
END:VEVENT
BEGIN:VEVENT
UID:5
DTSTART;TZID=America/Los_Angeles:20261104T193000
DTEND;TZID=America/Los_Angeles:20261104T213000
SUMMARY:Date night
END:VEVENT
BEGIN:VEVENT
UID:6
DTSTART;VALUE=DATE:20261103
DTEND;VALUE=DATE:20261104
SUMMARY:Election Day
END:VEVENT
BEGIN:VEVENT
UID:7
DTSTART;TZID=America/Los_Angeles:20261102T183000
DTEND;TZID=America/Los_Angeles:20261102T193000
SUMMARY:Swim lesson
END:VEVENT
END:VCALENDAR
"""


def period(start, day, short, temp, pop=None):
    return {'startTime': start, 'isDaytime': day, 'shortForecast': short, 'temperature': temp, 'temperatureUnit': 'F',
            'probabilityOfPrecipitation': {'value': pop}}


NWS = [period('2026-11-01T18:00:00-08:00', False, 'Clear', 55)]
for i, (short, hi, lo, pop) in enumerate([('Sunny', 75, 57, None), ('Sunny', 78, 58, None), ('Partly Sunny', 74, 59, 10),
                                          ('Sunny', 84, 61, None), ('Cloudy', 70, 60, 30), ('Rain Likely', 64, 55, 70)]):
    day = D(2026, 11, 2) + datetime.timedelta(days=i)
    NWS += [period(f'{day}T06:00:00-08:00', True, short, hi, pop), period(f'{day}T18:00:00-08:00', False, 'Clear', lo)]
METEO = {'daily': {'time': [str(D(2026, 11, 2) + datetime.timedelta(days=i)) for i in range(7)],
                   'weather_code': [0, 0, 2, 0, 3, 63, 1], 'temperature_2m_max': [24, 25, 23, 29, 21, 18, 20.5],
                   'temperature_2m_min': [14, 14, 15, 16, 15, 13, 12], 'precipitation_probability_max': [0, 0, 10, 0, 30, 70, 10]}}


def cfg():
    c = config.load()
    c['timezone'] = 'America/Los_Angeles'
    c['calendars'] = [{'name': 'Family', 'url': 'http://cal', 'color': 'blue', 'role': 'events'}]
    c['weather'].update(enabled=True, lat=33.77, lon=-118.36)
    c['holidays'] = {'country': '', 'subdiv': ''}
    c['home_assistant'] = dict(c['home_assistant'], url='', token='')
    return c


def fakes(monkeypatch):
    monkeypatch.setattr(plan_data, 'fetch_calendar', lambda url: icalendar.Calendar.from_ical(ICS))
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: NWS)
    monkeypatch.setattr(nws, 'alerts', lambda lat, lon: [])
    monkeypatch.setattr(week.urllib.request, 'urlopen', lambda url, timeout: io.BytesIO(json.dumps(METEO).encode()))


def test_week_starts_on_the_monday_after_today():
    assert week.week_start(D(2026, 11, 1)) == D(2026, 11, 2)    # Sunday evening: tomorrow
    assert week.week_start(D(2026, 11, 4)) == D(2026, 11, 9)    # mid-week preview: next Monday
    assert week.week_start(D(2026, 11, 2)) == D(2026, 11, 9)    # Monday: the following one


def test_week_build_days_weather_and_summary(monkeypatch):
    fakes(monkeypatch)
    data = week.build(cfg(), datetime.datetime(2026, 11, 1, 17, 0, tzinfo=LA))
    days = data['days']
    assert [d['weekday'] for d in days] == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    assert days[0]['weather'] == {'icon': '☀️', 'hi': 75, 'lo': 57, 'text': 'Sunny', 'pop': 0}       # NWS
    assert days[6]['weather']['hi'] == 69 and days[6]['weather']['lo'] == 54                        # Open-Meteo fills Sunday
    assert days[1]['pills'] == ['Election Day'] and days[1]['events'][0] == {
        'start': '6:10 AM', 'title': 'Flight to Denver', 'color': 'blue'}
    assert len(days[2]['events']) == 4 and days[5]['events'] == []
    s = dict((t.split(':')[0].split(' ')[0], t) for _, t in data['summary'])
    texts = [t for _, t in data['summary']]
    assert 'Early start Tue: Flight to Denver at 6:10 AM' in texts
    assert 'Busiest day Wed: 4 events, 8:00 AM to 9:30 PM' in texts
    assert 'Rain likely Sat (70%) · highs 64–84°' in texts
    assert any(t.startswith('Free evenings: Tue, Thu, Fri, Sat, Sun') for t in texts)
    assert len(texts) <= 6 and s


def test_clock_change_week_spring_2026(monkeypatch):
    fakes(monkeypatch)
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: [])
    meteo = {'daily': dict(METEO['daily'], time=[str(D(2026, 3, 2) + datetime.timedelta(days=i)) for i in range(7)])}
    monkeypatch.setattr(week.urllib.request, 'urlopen', lambda url, timeout: io.BytesIO(json.dumps(meteo).encode()))
    data = week.build(cfg(), datetime.datetime(2026, 3, 1, 17, tzinfo=LA))
    assert data['days'][6]['pills'][0] == 'Clocks forward 1 hr'                   # Sunday March 8
    assert ('🕰️', 'Clocks spring forward Sunday 2 AM: lose an hour') in data['summary']


def test_birthday_in_the_week_becomes_a_pill_and_a_summary_line(monkeypatch):
    fakes(monkeypatch)
    meteo = {'daily': dict(METEO['daily'], time=[str(D(2027, 7, 26) + datetime.timedelta(days=i)) for i in range(7)])}
    monkeypatch.setattr(week.urllib.request, 'urlopen', lambda url, timeout: io.BytesIO(json.dumps(meteo).encode()))
    monkeypatch.setattr(nws, 'periods', lambda lat, lon: [])
    data = week.build(cfg(), datetime.datetime(2027, 7, 25, 17, tzinfo=LA))      # week of Mon Jul 26, 2027
    assert data['days'][0]['pills'][0] == '🎂 Jake'
    assert any('Jake’s birthday Mon' in t for _, t in data['summary'])


def test_sunday_evening_switches_to_the_week_and_the_toggle_turns_it_off():
    c = dict(cfg(), tv_ip='192.0.2.1', schedule={'enabled': True, 'start': '07:00', 'end': '10:00',
                                                    'days': list(range(7)), 'refresh_minutes': 5})
    sun = lambda h, m=0: datetime.datetime(2026, 11, 1, h, m, tzinfo=LA)  # noqa: E731
    morning = {'shown_on': '2026-11-01', 'shown_view': 'day', 'showing': None, 'restore_due': False}
    assert jobs.view_for(c, sun(8)) == 'day' and jobs.view_for(c, sun(17)) == 'week'
    assert jobs._due(c, morning, sun(17)) == 'show'                              # the week replaces the morning plan
    shown = dict(morning, shown_view='week', showing='plan', shown_at=sun(17).timestamp(), restore_due=True)
    assert jobs._due(c, shown, sun(17, 3)) is None and jobs._due(c, shown, sun(17, 6)) == 'refresh'
    assert jobs._due(c, shown, sun(21)) == 'restore'                             # 9 PM: artwork back
    off = dict(c, week_view={'enabled': False, 'day': 6, 'start': '17:00', 'end': '21:00'})
    assert jobs.view_for(off, sun(17)) is None and jobs._due(off, morning, sun(17)) is None
    assert jobs.view_for(c, datetime.datetime(2026, 11, 2, 17, tzinfo=LA)) is None   # only on Sundays


def test_render_week_demo(tmp_path):
    render.render_week(week.demo(D(2026, 11, 1)), str(tmp_path / 'w.jpg'))
    assert (tmp_path / 'w.jpg').stat().st_size > 100_000
