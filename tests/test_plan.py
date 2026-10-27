import datetime
import os
import zoneinfo

import icalendar
from PIL import Image

import jobs
import plan_data
import render

HERE = os.path.dirname(__file__)
TZ = zoneinfo.ZoneInfo('Europe/London')
CFG = {'timezone': 'Europe/London', 'plan_lang': 'en', 'calendars': [], 'dim_past_events': True,
       'weather': {'enabled': False}, 'holidays': {'country': '', 'subdiv': ''}, 'background': 'default',
       'tv_ip': '192.168.1.50', 'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4]}}


def cal():
    with open(os.path.join(HERE, 'sample.ics'), 'rb') as f:
        return icalendar.Calendar.from_ical(f.read())


def test_calendar_entries():
    events, todos = plan_data.calendar_entries(cal(), 'green', datetime.date(2026, 10, 27), TZ, plan_data.WORDS['en'])
    titles = [(e['start'], e['title']) for e in sorted(events, key=lambda e: e['sort'])]
    assert ('All day', 'Project week') in titles
    assert ('07:15', 'Breakfast together') in titles  # whitespace collapsed
    assert ('16:00', 'Piano lesson') in titles      # weekly recurrence expanded
    assert todos == ['Tidy up your room']           # "To-do:" prefix moves it to the list


def test_todo_calendar_takes_every_entry():
    _, todos = plan_data.calendar_entries(cal(), 'orange', datetime.date(2026, 10, 27), TZ, plan_data.WORDS['en'], True)
    assert len(todos) == 4


def test_holidays_in_plan_language():
    cfg = dict(CFG, holidays={'country': 'DE', 'subdiv': 'BY'})
    xmas = datetime.date(2026, 12, 25)
    en = plan_data.holiday_entries(dict(cfg, plan_lang='en'), xmas, plan_data.WORDS['en'])
    de = plan_data.holiday_entries(dict(cfg, plan_lang='de'), xmas, plan_data.WORDS['de'])
    assert en[0]['start'] == 'Holiday' and 'Christmas' in en[0]['title']
    assert de[0]['start'] == 'Feiertag' and 'Weihnacht' in de[0]['title']


def test_schedule_rules():
    d = lambda h, m=0, day=27: datetime.datetime(2026, 10, day, h, m, tzinfo=TZ)  # 27th = Tuesday
    today = '2026-10-27'
    assert jobs._due(CFG, {}, d(6, 59)) is None
    assert jobs._due(CFG, {}, d(7)) == 'show'
    assert jobs._due(CFG, {'shown_on': today, 'restore_due': True}, d(8)) is None
    assert jobs._due(CFG, {'shown_on': today, 'restore_due': True}, d(10)) == 'restore'
    assert jobs._due(CFG, {'shown_on': today, 'restore_due': False}, d(15)) is None  # manual message stays
    assert jobs._due(CFG, {'restore_due': True}, d(6, day=28)) == 'restore'           # TV was off at 10
    assert jobs._due(CFG, {}, d(8, day=25)) is None                                   # Sunday not scheduled
    assert jobs._due(dict(CFG, tv_ip=''), {}, d(8)) is None                          # not set up yet


def test_refresh_during_window():
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    cfg = dict(CFG, schedule=dict(CFG['schedule'], refresh_minutes=5))
    plan = {'shown_on': '2026-10-27', 'showing': 'plan', 'shown_at': d(8).timestamp(), 'restore_due': True}
    assert jobs._due(cfg, plan, d(8, 4)) is None                                    # too soon
    assert jobs._due(cfg, plan, d(8, 5)) == 'refresh'
    assert jobs._due(cfg, dict(plan, showing='message'), d(8, 30)) is None           # a message stays up
    assert jobs._due(cfg, dict(plan, showing=None), d(8, 30)) is None                # "Back to artwork" pressed
    assert jobs._due(dict(cfg, schedule=dict(cfg['schedule'], refresh_minutes=0)), plan, d(9)) is None
    assert jobs._due(cfg, plan, d(10)) == 'restore'                                  # window over


def test_render_both_languages(tmp_path):
    now = datetime.datetime(2026, 10, 27, 8, 0, tzinfo=TZ)
    for lang in ('en', 'de'):
        out = tmp_path / f'demo-{lang}.jpg'
        render.render(plan_data.demo(CFG, now, lang), str(out))
        assert Image.open(out).size == (3840, 2160)


def test_text_and_fit(tmp_path):
    render.render_text('Grandma picks you up at 4 pm today', str(tmp_path / 't.jpg'))
    Image.new('RGB', (600, 1200), (200, 100, 50)).save(tmp_path / 'tall.jpg')
    for mode in ('crop', 'fit'):
        render.fit_artwork(str(tmp_path / 'tall.jpg'), str(tmp_path / f'{mode}.jpg'), mode)
        assert Image.open(tmp_path / f'{mode}.jpg').size == (3840, 2160)


def test_refresh_uploads_only_when_the_picture_changed(monkeypatch):
    sent = []

    class FakeArt:
        def get_current(self):
            return {'content_id': sent[-1] if sent else 'MY_ART'}

        def get_artmode(self):
            return 'on'

        def select_image(self, cid, show=True):
            pass

        def delete_list(self, ids):
            pass

    def send(a, data):
        sent.append(f'MY_F{len(sent)}')
        return sent[-1]
    monkeypatch.setattr(jobs.tv, 'connect', lambda ip: FakeArt())
    monkeypatch.setattr(jobs.tv, 'send_image', send)
    picture = {'bytes': b'plan v1'}

    def make(a, state):
        open(jobs.LAST_IMAGE, 'wb').write(picture['bytes'])
    cfg = dict(CFG, tv_ip='192.0.2.1')
    jobs.put_temporary(cfg, make, 'Plan', kind='plan')
    jobs.put_temporary(cfg, make, 'Plan updated', kind='plan', only_if_changed=True)
    assert sent == ['MY_F0']                                     # same picture: TV left alone
    picture['bytes'] = b'plan v2'                                # e.g. a new calendar entry
    state = jobs.put_temporary(cfg, make, 'Plan updated', kind='plan', only_if_changed=True)
    assert sent == ['MY_F0', 'MY_F1'] and state['plan_ids'] == ['MY_F1'] and state['restore_id'] == 'MY_ART'
