import datetime
import json
import os
import zoneinfo

import config
import jobs
import recipe
import render
import web

HERE = os.path.dirname(__file__)
TEXT = open(os.path.join(HERE, 'data', 'paprika_red_curry.txt'), encoding='utf-8').read()
LA = zoneinfo.ZoneInfo('America/Los_Angeles')


def test_parse_real_paprika_recipe_text():
    r = recipe.parse(TEXT)
    assert r['name'] == 'Red Curry Chicken and Rice Bowls'
    assert r['meta'] == {'prep': '10 min', 'cook': '50 min', 'total': '1 hr', 'servings': '4'}
    assert len(r['ingredients']) == 11 and r['ingredients'][0] == '1½ cups long-grain white rice'
    assert len(r['steps']) == 4 and r['steps'][3].startswith('Thinly slice the chicken')
    assert r['source'].startswith('https://cooking.nytimes.com/recipes/765911183')
    assert not any('Calories' in s for s in r['steps'])                 # nutrition is left out


def test_quantities_and_groups():
    assert recipe.split_quantity('2 tablespoons to ⅓ cup Thai red curry paste, to taste') == (
        '2 tablespoons to ⅓ cup', 'Thai red curry paste, to taste')
    assert recipe.split_quantity('Salt and pepper') == ('', 'Salt and pepper')
    r = recipe.parse('Pie\n\nIngredients:\nFor the crust:\n1 cup flour\n\nInstructions:\n1. Mix.\n2) Bake.\n')
    assert r['ingredients'] == [{'heading': 'For the crust'}, '1 cup flour'] and r['steps'] == ['Mix.', 'Bake.']
    assert recipe.looks_like_recipe(TEXT) and not recipe.looks_like_recipe('https://example.com')


def test_render_recipe_with_and_without_photo(tmp_path):
    r = recipe.parse(TEXT)
    render.render_recipe(r, str(tmp_path / 'a.jpg'), until='1:05 PM')
    long = dict(r, steps=r['steps'] * 4)                                 # forces two columns
    render.render_recipe(long, str(tmp_path / 'b.jpg'), until='1:05 PM')
    assert (tmp_path / 'a.jpg').stat().st_size > 100_000 and (tmp_path / 'b.jpg').stat().st_size > 100_000


def test_recipe_holds_the_screen_then_plan_or_artwork():
    cfg = config.load()
    cfg.update(tv_ip='192.0.2.1', timezone='America/Los_Angeles',
               schedule={'enabled': True, 'start': '07:00', 'end': '10:00', 'days': list(range(7)), 'refresh_minutes': 5})
    at = lambda h, m=0: datetime.datetime(2026, 10, 29, h, m, tzinfo=LA)  # noqa: E731
    st = {'showing': 'recipe', 'until': at(9, 30).timestamp(), 'shown_on': '2026-10-28', 'restore_due': True}
    assert jobs._due(cfg, st, at(8)) is None                             # morning plan waits for the recipe
    assert jobs._due(cfg, st, at(9, 30)) == 'show'                      # then the plan, inside its window
    st = dict(st, until=at(18).timestamp())
    assert jobs._due(cfg, st, at(17)) is None and jobs._due(cfg, st, at(18)) == 'restore'


def test_share_from_paprika_shows_it_and_notifies_that_phone(monkeypatch):
    shown, notes = [], []
    monkeypatch.setattr(jobs, 'show_recipe', lambda cfg, rec, minutes=None, notify=None, until=None:
                        shown.append((rec['name'], minutes, notify)))
    monkeypatch.setattr(jobs.ha, 'notify_service_for', lambda device_id, cfg=None: 'mobile_app_pixel_10_pro')
    jobs.on_share({'caller': 'android-app://com.hindsightlabs.paprika.android.v3', 'device_id': '65f60c5ff3838dea',
                   'text': TEXT})
    assert shown == [('Red Curry Chicken and Rice Bowls', 60, 'mobile_app_pixel_10_pro')]
    jobs.on_share({'caller': 'android-app://com.hindsightlabs.paprika.android.v3', 'text': 'https://example.com'})
    jobs.on_share({'caller': 'android-app://com.android.chrome', 'text': 'a web page'})
    assert len(shown) == 1                                               # source links and other apps ignored


def test_phone_buttons(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, 'extend_recipe', lambda cfg, minutes=30: calls.append('more'))
    monkeypatch.setattr(jobs, 'hide', lambda cfg: calls.append('hide'))
    for a in ('FRAME_RECIPE_MORE', 'FRAME_RECIPE_HIDE', 'SOMETHING_ELSE'):
        jobs.on_notification_action({'action': a})
    assert calls == ['more', 'hide']


def test_notify_service_for_the_sharing_phone(monkeypatch):
    import ha
    devices = [{'name': 'Pixel 10 Pro', 'name_by_user': None, 'identifiers': [['mobile_app', '65f60c5ff3838dea']]},
               {'name': 'Michael’s iPhone', 'name_by_user': None, 'identifiers': [['mobile_app', 'abc']]}]
    monkeypatch.setattr(ha, 'ws_call', lambda msg, cfg=None: devices)
    assert ha.notify_service_for('65f60c5ff3838dea') == 'mobile_app_pixel_10_pro'
    assert ha.notify_service_for('abc') == 'mobile_app_michaels_iphone'
    assert ha.notify_service_for('nope') is None


def test_web_paste_preview(monkeypatch):
    got = []
    monkeypatch.setattr(web.jobs, 'preview_recipe', lambda cfg, rec: got.append(rec['name']))
    r = web.app.test_client().post('/recipe', data={'recipe_text': TEXT, 'action': 'preview'})
    assert r.status_code == 302 and got == ['Red Curry Chicken and Rice Bowls']
    r = web.app.test_client().post('/recipe', data={'recipe_text': 'hello', 'action': 'show'})
    assert 'No recipe' in r.headers['Location'] or 'recipe' in r.headers['Location'].lower()
