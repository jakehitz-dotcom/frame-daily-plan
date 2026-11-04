import datetime
import time
import zoneinfo

import jobs
import recipe

TZ = zoneinfo.ZoneInfo('America/Los_Angeles')
CFG = {'timezone': 'America/Los_Angeles', 'tv_ip': '192.168.1.50', 'home_assistant': {},
       'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4], 'refresh_minutes': 5}}
NOW = datetime.datetime(2026, 11, 4, 13, 0, tzinfo=TZ)
SEV = {'active': True, 'signature': 'rain', 'reasons': []}


def test_a_remote_choice_holds_over_every_rule():
    t = NOW.timestamp()
    held = {'showing': None, 'manual_until': t + 1800}
    assert jobs._due(CFG, held, NOW, SEV, bottle=400) is None              # art held over rain and the bottle
    plan = {'showing': 'plan', 'manual_until': t + 1800, 'shown_at': t - 400}
    assert jobs._due(CFG, plan, NOW, SEV, bottle=400) == 'refresh'          # kept up to date
    later = NOW + datetime.timedelta(minutes=31)
    assert jobs._due(CFG, held, later, SEV) == 'severe'                     # then the rules decide again
    assert jobs._due(CFG, held, later, None, bottle=400) == 'bottle'


def test_a_shared_recipe_is_not_replaced_by_the_bottle_rule():
    t = NOW.timestamp()
    state = {'showing': 'recipe', 'until': t + 3600, 'manual_until': t + 3600}   # what show_recipe records
    assert jobs._due(CFG, state, NOW, None, bottle=400) is None


def test_current_mode_names():
    assert jobs.current_mode({'showing': 'plan', 'shown_view': 'week'}) == 'week'
    assert jobs.current_mode({'showing': 'plan'}) == 'plan'
    assert jobs.current_mode({'showing': 'severe'}) == 'weather'
    assert jobs.current_mode({}) == 'art'


def test_paprika_text_with_or_without_colons():
    with_colons = 'Soup\n\nIngredients:\n1 onion\n\nDirections:\nCook.\n'
    without = 'Soup\n\nINGREDIENTS\n1 onion\n2 carrots\n\nDIRECTIONS\n1. Chop.\n2. Simmer.\n'
    assert recipe.looks_like_recipe(with_colons) and recipe.looks_like_recipe(without)
    rec = recipe.parse(without)
    assert rec['ingredients'] == ['1 onion', '2 carrots'] and rec['steps'] == ['Chop.', 'Simmer.']
    assert not recipe.looks_like_recipe('Check out these ingredients and directions to the party')


def test_a_failed_share_is_logged_and_told_to_the_phone(monkeypatch):
    told, logged = [], []
    monkeypatch.setattr(jobs, 'tell_phone', lambda cfg, svc, msg, actions=True: told.append(msg))
    monkeypatch.setattr(jobs, 'log', lambda msg: logged.append(msg))
    monkeypatch.setattr(jobs.config, 'load', lambda: dict(CFG, recipe={'notify': 'mobile_app_pixel'}))
    jobs.on_share({'caller': 'com.paprika', 'text': 'just a note'})
    assert logged[0].startswith('Share received from paprika') and 'Recipe Text' in told[0]
    monkeypatch.setattr(jobs, 'show_recipe', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('the TV is asleep')))
    jobs.on_share({'caller': 'com.paprika', 'text': 'Soup\n\nIngredients:\n1 onion\n\nDirections:\nCook.\n'})
    assert 'the TV is asleep' in told[-1]


def test_screen_mode_records_the_hold(monkeypatch):
    jobs.save_state({'plan_ids': [], 'bottle_override': True, 'showing': 'plan'})
    monkeypatch.setattr(jobs.tv, 'awake', lambda ip: True)
    shown = []
    monkeypatch.setattr(jobs, 'show_plan', lambda cfg, **k: shown.append(k.get('view')))
    jobs.screen_mode(CFG, 'week')
    st = jobs.load_state()
    assert shown == ['week'] and st['manual_view'] == 'week' and st['manual_until'] > time.time() + 1700
    assert not st['bottle_override']
