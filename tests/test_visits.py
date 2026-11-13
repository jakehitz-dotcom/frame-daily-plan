import datetime
import zoneinfo

import icalendar

import banner

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
D = datetime.date


def cal(*events):
    c = icalendar.Calendar()
    for i, (title, start, end) in enumerate(events):
        e = icalendar.Event()
        e.add('uid', f'{i}@test')
        e.add('summary', title)
        e.add('dtstart', start)
        e.add('dtend', end)
        c.add_component(e)
    return c


def test_visitor_names():
    assert banner.visitor_name('Cheryl Visit') == 'Cheryl'
    assert banner.visitor_name("Cheryl's visit") == 'Cheryl'
    assert banner.visitor_name('Visit from Mom & Dad') == 'Mom & Dad'
    assert banner.visitor_name('Bob visiting') == 'Bob'
    assert banner.visitor_name('Dentist') is None


def test_welcome_on_the_first_day_and_farewell_on_the_last():
    c = cal(('Cheryl Visit', D(2026, 11, 13), D(2026, 11, 17)))        # all-day, Nov 13–16
    assert banner.visits([c], D(2026, 11, 13), TZ) == (['Cheryl'], [])
    assert banner.visits([c], D(2026, 11, 14), TZ) == ([], [])
    assert banner.visits([c], D(2026, 11, 16), TZ) == ([], ['Cheryl'])
    one_day = cal(('Cheryl Visit', D(2026, 11, 13), D(2026, 11, 14)))
    assert banner.visits([one_day], D(2026, 11, 13), TZ) == ([], [])  # a single day is not a stay
    timed = cal(('Bob visiting', datetime.datetime(2026, 11, 20, 15, 0, tzinfo=TZ),
                 datetime.datetime(2026, 11, 23, 11, 0, tzinfo=TZ)))
    assert banner.visits([timed], D(2026, 11, 23), TZ) == ([], ['Bob'])


def test_the_visit_joins_a_holiday_greeting():
    b, _ = banner.for_day({}, D(2026, 11, 26), banner.visit_line(['Cheryl'], []))
    assert b['text'] == '👋 Welcome Cheryl!  ·  🦃 Happy Thanksgiving! 🥧'
    b, _ = banner.for_day({}, D(2026, 11, 13), banner.visit_line([], ['Cheryl', 'Bob']))
    assert b['text'] == 'Farewell Cheryl & Bob! 💛'
