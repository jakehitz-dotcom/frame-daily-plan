import datetime

import banner
import config
import web

D = datetime.date


def cfg(**over):
    c = config.load()
    c.update(over)
    return c


def test_floating_holidays_land_on_the_right_days_2026():
    h = banner.holidays(2026)
    assert 'Martin Luther King' in h[D(2026, 1, 19)]
    assert 'Presidents' in h[D(2026, 2, 16)]
    assert 'Easter' in h[D(2026, 4, 5)]
    assert 'Mother' in h[D(2026, 5, 10)]
    assert 'Memorial Day' in h[D(2026, 5, 25)]
    assert 'Father' in h[D(2026, 6, 21)]
    assert 'Labor Day' in h[D(2026, 9, 7)]
    assert 'Thanksgiving' in h[D(2026, 11, 26)]
    assert 'Valentine' in h[D(2026, 2, 14)] and 'Halloween' in h[D(2026, 10, 31)]


def test_birthdays_every_year():
    for year in (2026, 2027, 2030):
        assert banner.for_day(cfg(), D(year, 2, 1))[0]['text'] == '🎂 Happy Birthday Mike! 🎈'
        assert banner.for_day(cfg(), D(year, 7, 26))[0]['text'] == '🎂 Happy Birthday Jake! 🎈'
        assert banner.for_day(cfg(), D(year, 8, 9))[0]['text'] == '🎂 Happy Birthday Finn! 🎈'


def test_priority_custom_then_birthday_then_holiday():
    mine = cfg(banner={'text': 'Welcome home, Grandma!', 'until': ''})
    assert banner.for_day(mine, D(2026, 2, 1))[0] == {'text': 'Welcome home, Grandma!', 'source': 'custom'}
    assert banner.for_day(cfg(), D(2026, 7, 4))[0]['source'] == 'holiday'
    assert banner.for_day(cfg(), D(2026, 3, 3)) == (None, None)            # an ordinary day: no banner
    assert banner.for_day(cfg(greetings=False), D(2026, 7, 4)) == (None, None)


def test_custom_banner_expires_after_its_last_day():
    mine = cfg(banner={'text': 'Grandma visiting', 'until': '2026-11-02'})
    assert banner.for_day(mine, D(2026, 11, 2))[0]['source'] == 'custom'
    assert banner.for_day(mine, D(2026, 11, 3)) == (None, None)


def test_christmas_countdown_dec_1_to_24_alongside_other_banners():
    assert banner.for_day(cfg(), D(2026, 11, 30)) == (None, None)
    assert banner.for_day(cfg(), D(2026, 12, 1)) == (None, 24)
    assert banner.for_day(cfg(), D(2026, 12, 24))[1] == 1                 # with "Merry Christmas Eve!"
    text, days = banner.for_day(cfg(), D(2026, 12, 25))
    assert text['text'] == '🎄 Merry Christmas! 🎁' and days is None
    assert banner.for_day(cfg(christmas_countdown=False), D(2026, 12, 10)) == (None, None)


def test_render_with_banner_and_countdown(tmp_path):
    import plan_data
    import render
    d = plan_data.demo(config.load(), datetime.datetime(2026, 12, 12, 8), 'en')
    d['banner'], d['countdown'] = {'text': 'Welcome home, Grandma! 🎉', 'source': 'custom'}, 13
    render.render(d, str(tmp_path / 'b.jpg'))
    assert (tmp_path / 'b.jpg').stat().st_size > 100_000


def test_banner_form_saves_and_clears(monkeypatch):
    monkeypatch.setattr(web.jobs, 'preview', lambda c: None)
    c = web.app.test_client()
    c.post('/banner', data={'action': 'save', 'banner_text': 'Go Dodgers!', 'banner_until': '2026-11-01',
                            'greetings': 'on', 'christmas_countdown': 'on'})
    assert config.load()['banner'] == {'text': 'Go Dodgers!', 'until': '2026-11-01'}
    c.post('/banner', data={'action': 'clear', 'greetings': 'on'})
    saved = config.load()
    assert saved['banner'] == {'text': '', 'until': ''} and saved['greetings'] and not saved['christmas_countdown']
