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


def _sleepy_tv(monkeypatch, awake_now, wakes):
    """A Frame that sleeps between motion: awake_now[0] flips when it wakes."""
    calls = {'wake': 0, 'show': [], 'restore': 0}
    monkeypatch.setattr(jobs.tv, 'awake', lambda ip: awake_now[0])

    def wake(ip, mac, wait=60):
        calls['wake'] += 1
        awake_now[0] = wakes
        return wakes
    monkeypatch.setattr(jobs.tv, 'wake', wake)
    monkeypatch.setattr(jobs, 'show_plan', lambda cfg, refresh=False, woke=False: calls['show'].append((refresh, woke)))
    monkeypatch.setattr(jobs, 'restore', lambda cfg: calls.__setitem__('restore', calls['restore'] + 1))
    return calls


def test_plan_window_starts_with_the_tv_asleep_wakes_it(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    calls = _sleepy_tv(monkeypatch, [False], wakes=True)
    assert jobs.tick(dict(CFG, wake_tv=True), d(7), {}) == 'show'
    assert calls['wake'] == 1 and calls['show'] == [(False, True)]   # staged on a woken TV


def test_tv_that_will_not_wake_gets_the_plan_when_motion_wakes_it(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=False)
    memo = {}
    assert jobs.tick(dict(CFG, wake_tv=True), d(7), memo) is None
    assert jobs.tick(dict(CFG, wake_tv=True), d(7, 1), memo) is None
    assert calls['wake'] == 1                                         # not hammered every tick
    awake[0] = True                                                   # someone walks in
    assert jobs.tick(dict(CFG, wake_tv=True), d(7, 40), memo) == 'show' and calls['show'] == [(False, False)]


def test_refresh_waits_for_motion_and_never_wakes_the_tv(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    cfg = dict(CFG, wake_tv=True, schedule=dict(CFG['schedule'], refresh_minutes=5))
    jobs.save_state({'plan_ids': ['MY_F1'], 'tv_mac': 'aa:bb:cc:dd:ee:ff', 'shown_on': '2026-10-27',
                     'showing': 'plan', 'shown_at': d(7).timestamp(), 'restore_due': True})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=True)
    assert jobs.tick(cfg, d(7, 30), {}) is None and calls['wake'] == 0
    awake[0] = True
    assert jobs.tick(cfg, d(7, 31), {}) == 'refresh' and calls['show'] == [(True, False)]


def test_window_end_restores_on_the_next_natural_wake(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    jobs.save_state({'plan_ids': ['MY_F1'], 'tv_mac': 'aa:bb:cc:dd:ee:ff', 'shown_on': '2026-10-27', 'restore_due': True})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=True)
    assert jobs.tick(dict(CFG, wake_tv=True), d(10), {}) is None and calls['wake'] == 0
    awake[0] = True
    assert jobs.tick(dict(CFG, wake_tv=True), d(12), {}) == 'restore'


def test_voice_request_shows_for_30_minutes_then_restores():
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    cfg = dict(CFG, schedule=dict(CFG['schedule'], refresh_minutes=5))
    st = {'shown_on': '2026-10-27', 'showing': 'plan', 'shown_at': d(19).timestamp(),
          'until': d(19, 30).timestamp(), 'restore_due': False}
    assert jobs._due(cfg, st, d(19, 2)) is None            # evening request, fresh
    assert jobs._due(cfg, st, d(19, 6)) == 'refresh'       # kept up to date meanwhile
    assert jobs._due(cfg, st, d(19, 30)) == 'restore'      # time is up
    late = dict(st, shown_at=d(9, 50).timestamp(), until=d(10, 20).timestamp(), restore_due=True)
    assert jobs._due(cfg, late, d(10, 5)) == 'refresh'     # asked at 9:50: the 30 minutes outlast the window
    assert jobs._due(cfg, late, d(10, 20)) == 'restore'


def test_show_button_press_is_acted_on_once(monkeypatch):
    presses = iter(['2026-10-27T08:00:00', '2026-10-27T08:00:00', '2026-10-27T19:04:11', '2026-10-27T19:04:11'] * 2)
    shown = []
    monkeypatch.setattr(jobs.ha, 'configured', lambda cfg: True)
    monkeypatch.setattr(jobs.ha, 'state', lambda entity, cfg: {'state': next(presses)})
    monkeypatch.setattr(jobs, 'show_for', lambda cfg, minutes: shown.append(minutes))
    monkeypatch.setattr(jobs.config, 'load', lambda: dict(CFG, home_assistant={
        'url': 'http://ha', 'token': 't', 'show_minutes': 30, 'entities': {'show_button': 'input_button.frame_planner_show'}}))
    monkeypatch.setattr(jobs, 'hide', lambda cfg: shown.append('hide'))
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 4:
            raise KeyboardInterrupt
    monkeypatch.setattr(jobs.time, 'sleep', fake_sleep)
    try:
        jobs.watch_home_assistant()
    except KeyboardInterrupt:
        pass
    assert shown == [30]                                     # start-up reading ignored, one press, one showing


def test_show_for_wakes_a_sleeping_tv(monkeypatch):
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=True)
    got = {}
    monkeypatch.setattr(jobs, 'show_plan', lambda cfg, refresh=False, woke=False, until=None: got.update(woke=woke, until=until))
    before = jobs.time.time()
    jobs.show_for(dict(CFG, tv_ip='192.0.2.1'), 30)
    assert calls['wake'] == 1 and got['woke'] and 29 * 60 < got['until'] - before <= 30 * 60 + 1


def test_todo_overflow_rules():
    import render
    render.L = render.LABELS['en']
    five = 405                                                           # the real panel: five full rows
    o = lambda n: [f'open {i}' for i in range(n)]  # noqa: E731
    c = lambda n: [f'done {i}' for i in range(n)]  # noqa: E731
    assert render.TODO_FULL == 60 and 5 * render.todo_row(60) <= five
    assert render.plan_todos(o(3), c(2), five) == (60, o(3), c(2), None)                      # 1. all fit
    assert render.plan_todos(o(3), c(4), five) == (60, o(3), c(1), '(3 additional completed tasks)')  # 2.
    assert render.plan_todos([], c(7), five) == (60, [], c(4), '(3 additional completed tasks)')     # latest
    size, shown, done, foot = render.plan_todos(o(6), [], five)                              # 3. shrink
    assert render.TODO_SMALLEST <= size < 60 and shown == o(6) and foot is None
    size, shown, done, foot = render.plan_todos(o(12), c(2), five)                           # 4. count
    assert size == render.TODO_SMALLEST and foot == f'({12 - len(shown)} more to do · 2 completed not shown)'



def test_hide_while_asleep_restores_on_wake_even_inside_the_window(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    jobs.save_state({'plan_ids': ['MY_F1'], 'shown_on': '2026-10-27', 'showing': 'plan', 'shown_at': d(7).timestamp(),
                     'restore_due': True, 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=True)
    jobs.hide(dict(CFG, tv_ip='192.0.2.1'))
    assert jobs.load_state()['hide_pending'] and calls['wake'] == 0          # not woken just to hide
    assert jobs.tick(dict(CFG, wake_tv=True), d(8), {}) is None              # still asleep
    awake[0] = True
    assert jobs.tick(dict(CFG, wake_tv=True), d(8, 5), {}) == 'restore'      # first thing on waking


def test_hide_button_press_is_acted_on(monkeypatch):
    seq = {'input_button.frame_planner_show': iter(['a'] * 3), 'input_button.frame_planner_hide': iter(['x', 'x', 'y'])}
    done = []
    monkeypatch.setattr(jobs.ha, 'configured', lambda cfg: True)
    monkeypatch.setattr(jobs.ha, 'state', lambda entity, cfg: {'state': next(seq[entity])})
    monkeypatch.setattr(jobs, 'show_for', lambda cfg, minutes: done.append('show'))
    monkeypatch.setattr(jobs, 'hide', lambda cfg: done.append('hide'))
    monkeypatch.setattr(jobs.config, 'load', lambda: dict(CFG, home_assistant={'url': 'http://ha', 'token': 't', 'entities': {
        'show_button': 'input_button.frame_planner_show', 'hide_button': 'input_button.frame_planner_hide'}}))
    ticks = []

    def fake_sleep(s):
        ticks.append(s)
        if len(ticks) == 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(jobs.time, 'sleep', fake_sleep)
    try:
        jobs.watch_home_assistant()
    except KeyboardInterrupt:
        pass
    assert done == ['hide']


def test_one_wake_attempt_per_window_even_if_it_fails_or_the_app_restarts(monkeypatch):
    d = lambda h, m=0: datetime.datetime(2026, 10, 27, h, m, tzinfo=TZ)
    jobs.save_state({'plan_ids': [], 'tv_mac': 'aa:bb:cc:dd:ee:ff'})
    awake = [False]
    calls = _sleepy_tv(monkeypatch, awake, wakes=False)
    cfg = dict(CFG, wake_tv=True)
    for minute in range(0, 180, 1):                      # every tick of the whole 7-10 AM window
        jobs.tick(cfg, d(7 + minute // 60, minute % 60), {})   # fresh memo each time = app restarts
    assert calls['wake'] == 1 and calls['show'] == []
    awake[0] = True                                      # motion wakes it at 8:40
    assert jobs.tick(cfg, d(8, 40), {}) == 'show' and calls['show'] == [(False, False)]
    awake[0] = False                                     # back asleep; tomorrow is a new window
    jobs.save_state(dict(jobs.load_state(), shown_on='2026-10-27'))
    jobs.tick(cfg, datetime.datetime(2026, 10, 28, 7, 0, tzinfo=TZ), {})
    assert calls['wake'] == 2


def test_todo_hint_always_two_lines_when_it_has_two_parts():
    import render
    from PIL import Image, ImageDraw
    render.L = render.LABELS['en']
    d = ImageDraw.Draw(Image.new('RGB', (8, 8)))
    want = ['Alexa, add [task] to my to-do list', 'Alexa, mark [task] as complete']
    assert render.hint_lines(d, 'Alexa, add [task] to my to-do list | Alexa, mark [task] as complete', 650) == want
    assert render.hint_lines(d, 'Alexa, add [task] to my to-do list Alexa, mark [task] as complete', 650) == want
    assert render.hint_lines(d, 'Alexa, add [task] to my to-do list. Alexa, mark [task] as complete', 650) == [
        'Alexa, add [task] to my to-do list.', 'Alexa, mark [task] as complete']
    long = 'Say add followed by the task to put it on the list, or mark it done in the app when finished'
    assert len(render.hint_lines(d, long, 650)) == 2


def test_clock_three_minutes_ahead_and_updated_stamp(tmp_path):
    import zoneinfo
    import render
    cfg = dict(CFG, calendars=[], weather={'enabled': False}, home_assistant={'url': '', 'token': ''})
    data = plan_data.build(cfg, datetime.datetime(2026, 10, 28, 11, 31, tzinfo=zoneinfo.ZoneInfo('Europe/London')))
    assert data['clock'] == '11:34' and data['updated'] == '11:31 AM'
    off = plan_data.build(dict(cfg, clock={'enabled': False}), datetime.datetime(2026, 10, 28, 11, 31,
                                                                              tzinfo=zoneinfo.ZoneInfo('Europe/London')))
    assert 'clock' not in off
    for style in render.CLOCK_STYLES:
        icon = render.clock_icon(200, 11, 34, style)
        assert icon.size == (200, 200) and 0 < icon.getextrema()[3][1] <= round(255 * 0.28) + 1  # see-through
