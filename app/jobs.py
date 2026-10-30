"""The actions: preview, show the plan, bring the artwork back, and the daily scheduler."""
import collections
import datetime
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import zoneinfo

import config
import ha
import todo_track
import plan_data
import recipe
import radar
import render
import severe
import tv
import week

STATE_FILE = os.path.join(config.DATA, 'state.json')
LOG_FILE = os.path.join(config.DATA, 'frame-daily-plan.log')
PREVIEW = os.path.join(config.DATA, 'preview.jpg')
LAST_IMAGE = os.path.join(config.DATA, 'last.jpg')
RECIPE_FILE = os.path.join(config.DATA, 'recipe.json')
RECIPE_PHOTOS = os.path.join(config.DATA, 'recipe-photos')
ART_CACHE = os.path.join(config.DATA, 'art')
RETRY_AFTER = 2 * 60  # after an error talking to an awake TV
TICK = 15             # how often the scheduler looks; also how fast a TV waking up is noticed

lock = threading.Lock()  # one TV conversation at a time (scheduler and buttons)
recent = collections.deque(maxlen=200)


def log(msg):
    line = f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    print(line, flush=True)
    recent.append(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
        if os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + '.1')
    except OSError:
        pass


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'plan_ids': [], 'restore_id': None, 'shown_on': None, 'restore_due': False}


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def now_local(cfg):
    return datetime.datetime.now(zoneinfo.ZoneInfo(cfg['timezone']))


def learn_mac(cfg, state):
    """Remember the TV's MAC address while it is awake, so it can be woken later."""
    if not state.get('tv_mac'):
        mac = tv.mac_address(cfg['tv_ip'])
        if mac:
            state['tv_mac'] = mac
            log(f'TV MAC address {mac} saved (used to wake it)')


def require_tv(cfg):
    if not cfg.get('tv_ip'):
        raise RuntimeError('No TV IP address set')


def cached_artwork(cfg, state):
    if cfg.get('background') != 'artwork' or not state.get('restore_id'):
        return None
    path = os.path.join(ART_CACHE, f"{state['restore_id']}.jpg")
    return path if os.path.exists(path) else None


def artwork_background(cfg, a, content_id):
    """The plan sits on the artwork it replaces, so the wall keeps its colours."""
    if cfg.get('background') != 'artwork' or not content_id:
        return None
    path = os.path.join(ART_CACHE, f'{content_id}.jpg')
    if os.path.exists(path):
        return path  # already have it (e.g. a picture uploaded here): no need to ask the TV
    try:
        data = tv.get_thumbnail(a, content_id)  # a small thumbnail is plenty for a blurred background
        os.makedirs(ART_CACHE, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    except Exception as e:  # noqa: BLE001
        log(f'No thumbnail for {content_id} ({e}), using the default background')
    return path if os.path.exists(path) else None


def preview_week(cfg):
    """The week ahead as it would look now, without touching the TV."""
    data = week.build(cfg, now_local(cfg))
    render.render_week(data, PREVIEW, cached_artwork(cfg, load_state()))
    log('Week preview created' + (f' · notes: {"; ".join(data["errors"])}' if data['errors'] else ''))
    return data


def preview(cfg):
    """Render without touching the TV (uses the last known artwork as background)."""
    data = plan_data.build(cfg, now_local(cfg))
    render.render(data, PREVIEW, cached_artwork(cfg, load_state()))
    log('Preview created' + (f' · notes: {"; ".join(data["errors"])}' if data['errors'] else '')
        + (f' · to-dos: {len(data["todos"])}' if data['todos'] else ''))
    return data


def delete_plans(a, state, keep=None):
    old = [i for i in state['plan_ids'] if i != keep]
    if old:
        try:
            a.delete_list(old)
            log(f'Deleted old plans: {", ".join(old)}')
            old = []
        except Exception as e:  # noqa: BLE001  retried next time
            log(f'Could not delete old plans ({e}), will retry')
    state['plan_ids'] = old + ([keep] if keep else [])


def put_temporary(cfg, make_image, what, kind='other', only_if_changed=False, woke=False, until=None):
    """Upload something temporary (plan, demo, message), show it, and tidy up.

    make_image(a, state) writes LAST_IMAGE. Shown inside the schedule window it is removed
    when the window ends; shown outside it, it stays until "Back to artwork".
    kind 'plan' is kept up to date by the scheduler; only_if_changed skips the upload when the
    new picture is identical to the one on the TV (most 5-minute refreshes).
    """
    require_tv(cfg)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        learn_mac(cfg, state)
        if woke and a.get_artmode() != 'on':
            a.set_artmode('on')  # we woke an empty room's TV: Art Mode, never a TV programme
        current = (a.get_current() or {}).get('content_id')
        if current and current not in state['plan_ids']:
            state['restore_id'] = current  # the family's normal artwork
        notes = make_image(a, state)
        with open(LAST_IMAGE, 'rb') as f:
            image = f.read()
        digest = hashlib.sha256(image).hexdigest()
        state['shown_at'] = time.time()
        if only_if_changed and digest == state.get('image_hash') and current in state['plan_ids']:
            save_state(state)
            return state  # nothing changed and the plan is still on screen: leave the TV alone
        cid = tv.send_image(a, image)
        state['plan_ids'].append(cid)
        state['image_hash'] = digest
        state['showing'] = kind
        if not only_if_changed:
            state['until'] = until  # a timed showing (voice request); anything else shown replaces it
        state['restore_due'] = in_window(cfg, now_local(cfg))
        save_state(state)  # record the upload before anything else can fail
        show = a.get_artmode() == 'on'  # never interrupt a running TV programme
        a.select_image(cid, show=show)
        delete_plans(a, state, keep=cid)
        save_state(state)
        log(f'{what} {cid} ' + ('shown' if show else 'queued (appears when Art Mode comes on)')
            + (f' · notes: {"; ".join(notes)}' if notes else ''))
        return state


def show_plan(cfg, refresh=False, woke=False, until=None):
    """The current view: the week ahead in its Sunday-evening window, otherwise the daily plan."""
    now = now_local(cfg)
    view = view_for(cfg, now) or 'day'

    def make(a, state):
        bg = artwork_background(cfg, a, state.get('restore_id'))
        if view == 'week':
            data = week.build(cfg, now)
            render.render_week(data, LAST_IMAGE, bg)
        else:
            data = plan_data.build(cfg, now)
            render.render(data, LAST_IMAGE, bg)
        return data['errors']
    what = 'Week ahead' if view == 'week' else 'Plan'
    state = put_temporary(cfg, make, f'{what} updated' if refresh else what, kind='plan', only_if_changed=refresh,
                          woke=woke, until=until)
    if in_window(cfg, now):  # a manual plan at 06:30 must not stop the 07:00 one
        state['shown_on'] = now.date().isoformat()
        state['shown_view'] = view
        save_state(state)


def refresh_now(cfg):
    """Redraw the plan on the TV now (e.g. after a banner change), if it is awake; never wakes it."""
    try:
        if tv.awake(cfg['tv_ip']):
            show_plan(cfg, refresh=True)
    except Exception as e:  # noqa: BLE001
        log(f'Could not update the plan on the TV ({type(e).__name__}: {e})')


def show_demo(cfg, lang='en', on_tv=True):
    """Made-up plan (no real data) for sharing; shown like a plan."""
    data = plan_data.demo(cfg, now_local(cfg), lang)
    if not on_tv:
        render.render(data, PREVIEW, cached_artwork(cfg, load_state()))
        return

    def make(a, state):
        render.render(data, LAST_IMAGE, artwork_background(cfg, a, state.get('restore_id')))
    put_temporary(cfg, make, f'Demo plan ({lang})', kind='demo')


def show_text(cfg, text, theme='dark'):
    """A message on a plain background, temporary like a plan."""
    if not text.strip():
        raise RuntimeError('No text entered')

    def make(a, state):
        render.render_text(text, LAST_IMAGE, theme)
    put_temporary(cfg, make, f'Message “{text.strip()[:40]}”', kind='message')


def restore(cfg):
    require_tv(cfg)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        learn_mac(cfg, state)
        if state.get('restore_id'):
            a.select_image(state['restore_id'], show=a.get_artmode() == 'on')
            log(f'Artwork {state["restore_id"]} restored')
        delete_plans(a, state)
        state['restore_due'] = False
        state['until'] = None
        state['hide_pending'] = False
        clear_recipe_notification(cfg, state)
        state['showing'] = None  # "Back to artwork" also stops today's refreshes
        save_state(state)


def upload_artwork(cfg, src_path, mode='crop'):
    """Put the family's own picture on the Frame. It is kept (never auto-deleted)
    and becomes the artwork the plan returns to."""
    require_tv(cfg)
    fitted = os.path.join(config.DATA, 'upload-fitted.jpg')
    render.fit_artwork(src_path, fitted, mode)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        learn_mac(cfg, state)
        with open(fitted, 'rb') as f:
            cid = tv.send_image(a, f.read())
        show = a.get_artmode() == 'on'
        a.select_image(cid, show=show)
        state['restore_id'] = cid
        state['showing'] = None
        state.setdefault('artworks', []).append(cid)
        save_state(state)
        os.makedirs(ART_CACHE, exist_ok=True)
        os.replace(fitted, os.path.join(ART_CACHE, f'{cid}.jpg'))  # also the plan background from now on
        log(f'Picture {cid} uploaded and ' + ('shown' if show else 'queued') + ' (kept permanently)')
        return cid


def in_day_window(cfg, now):
    sch = cfg['schedule']
    return (bool(sch.get('enabled')) and now.weekday() in sch.get('days', [])
            and sch['start'] <= now.strftime('%H:%M') < sch['end'])


def in_week_window(cfg, now):
    wv = cfg.get('week_view') or {}
    return (bool(wv.get('enabled')) and now.weekday() == int(wv.get('day', 6))
            and wv.get('start', '17:00') <= now.strftime('%H:%M') < wv.get('end', '21:00'))


def view_for(cfg, now):
    """'week' in the week-ahead window (it wins if the two overlap), 'day' in the daily window, else None."""
    if in_week_window(cfg, now):
        return 'week'
    return 'day' if in_day_window(cfg, now) else None


def in_window(cfg, now):
    return view_for(cfg, now) is not None


def _due(cfg, state, now, sev=None):
    if not cfg.get('tv_ip'):
        return None  # not set up yet
    if state.get('hide_pending'):
        return 'restore'  # asked to hide while the TV slept: do it the moment it wakes
    if state.get('showing') == 'recipe':  # holds the screen; then the plan (in its window) or the artwork
        if (state.get('until') or 0) > now.timestamp():
            return None
        return 'show' if view_for(cfg, now) else 'restore'
    every = int(cfg['schedule'].get('refresh_minutes', 5) or 0) * 60
    stale = every and now.timestamp() - state.get('shown_at', 0) >= every
    if state.get('showing') == 'severe' and (state.get('until') or 0) > now.timestamp():
        return 'severe_refresh' if stale else None  # asked for by voice: kept up to date until its time is up
    refresh = 'refresh' if every and state.get('showing') == 'plan' and now.timestamp() - state.get('shown_at', 0) >= every else None
    view = view_for(cfg, now)
    if view:
        if state.get('shown_on') != now.date().isoformat() or state.get('shown_view', 'day') != view:
            return 'show'  # also when Sunday's morning plan gives way to the week ahead
        if state.get('showing') == 'severe':
            return 'show'  # the voice-requested monitoring time is up: back to the plan
        return refresh  # a message, demo or "Back to artwork" in between is left alone
    # Outside the planner's windows: severe weather monitoring while its triggers hold (unless dismissed).
    if sev and sev.get('active') and state.get('severe_dismissed') != sev.get('signature'):
        if state.get('showing') != 'severe':
            return 'severe'
        return 'severe_refresh' if stale else None
    if state.get('showing') == 'severe':
        return 'restore'  # triggers cleared (or hidden): back to the artwork
    until = state.get('until') or 0
    if until > now.timestamp():
        return refresh  # asked for by voice: kept up to date until its time is up
    # After the window or a timed showing (or later, if the TV was asleep): bring the artwork back once.
    return 'restore' if state.get('restore_due') or until else None


def tick(cfg, now, memo):
    """One scheduler step. A Frame that sleeps between motion is the normal case:
    - the plan is due but the TV sleeps: wake it (Wake-on-LAN) so the plan is already the selected
      artwork when the first person walks in, then let the TV's motion timer put it back to sleep;
    - a refresh is due but the TV sleeps: wait quietly; the refresh stays due, so it runs within
      one tick of the TV waking up (motion), on top of the plan it is already showing;
    - the window has ended: put the artwork back the next time the TV is awake.
    memo keeps what is only worth knowing between ticks (last wake attempt, asleep already logged)."""
    state = load_state()
    sev = None
    if severe.settings(cfg)['enabled'] and (cfg.get('weather') or {}).get('lat') is not None:
        try:
            sev = severe.status(cfg)
        except Exception as e:  # noqa: BLE001
            print('severe weather check:', e, file=sys.stderr)
        if sev is not None and not sev.get('active') and state.get('severe_dismissed'):
            state['severe_dismissed'] = None  # conditions cleared: the next episode shows again
            save_state(state)
    action = _due(cfg, state, now, sev)
    if not action or time.time() < memo.get('retry_at', 0):
        return None
    woke = False
    if not tv.awake(cfg['tv_ip']):
        mac = state.get('tv_mac') or cfg.get('tv_mac')
        window = f'{now.date().isoformat()}:{view_for(cfg, now)}'
        if action == 'severe':  # once per severe weather episode, and not in the night's quiet hours
            ss = severe.settings(cfg)
            window = f"severe:{now.date().isoformat()}:{(sev or {}).get('signature')}" \
                if ss['wake'] and not severe.in_quiet_hours(cfg, now) else None
        # The TV is woken once per window, at its start, and never again in it: refreshes, the end of
        # the window and a failed wake-up all wait for motion to wake the TV. Recorded in the state file,
        # so restarting the app cannot cause a second wake-up.
        if action in ('show', 'severe') and window and cfg.get('wake_tv', True) and mac \
                and state.get('woke_for') != window:
            state['woke_for'] = window
            save_state(state)
            log('Waking the TV for the plan (once for this window)')
            woke = tv.wake(cfg['tv_ip'], mac)
            if not woke:
                log('The TV did not wake up (is "Power On with Mobile" on?). No more wake-ups in this window: '
                    'the plan goes up when motion wakes the TV.')
        if not woke:
            if not memo.get('asleep_logged'):
                memo['asleep_logged'] = True
                if action != 'show' or not mac:
                    what = {'show': 'the plan', 'refresh': 'the plan update', 'restore': 'putting the artwork back',
                            'severe': 'severe weather monitoring'}.get(action, 'the update')
                    log(f'TV asleep: {what} goes ahead as soon as it wakes')
            return None
    memo['asleep_logged'] = False
    if action == 'restore':
        restore(cfg)
    elif action in ('severe', 'severe_refresh'):
        show_severe(cfg, refresh=action == 'severe_refresh', woke=woke)
    else:
        show_plan(cfg, refresh=action == 'refresh', woke=woke)
    return action


def show_for(cfg, minutes):
    """Show the plan now for `minutes` (a voice request): wake the TV if needed, then the scheduler
    keeps it fresh and puts the artwork back when the time is up (or at the end of the window)."""
    require_tv(cfg)
    woke = False
    if not tv.awake(cfg['tv_ip']):
        mac = load_state().get('tv_mac') or cfg.get('tv_mac')
        woke = bool(mac) and tv.wake(cfg['tv_ip'], mac)
        if not woke:
            raise RuntimeError('the TV is asleep and did not wake up')
    show_plan(cfg, woke=woke, until=time.time() + minutes * 60)


def hide(cfg):
    """Back to the artwork now (a voice request). Also dismisses severe weather monitoring. A sleeping TV is not woken: the artwork goes back
    the moment it wakes, before anyone sees the plan."""
    require_tv(cfg)
    dismiss_severe(cfg)
    if tv.awake(cfg['tv_ip']):
        restore(cfg)
        return
    with lock:
        state = load_state()
        state.update(hide_pending=True, showing=None, until=None)
        save_state(state)
    log('TV asleep: the artwork goes back as soon as it wakes')


# ── Severe weather monitoring ─────────────────────────────────────────────────────────────────
RADAR_SIZE = (2012, 1152)   # the map's inner size on the screen


def build_severe(cfg, now=None):
    """Everything the monitoring screen shows, as of now."""
    tz = zoneinfo.ZoneInfo(cfg['timezone'])
    now = now or now_local(cfg)
    st = severe.status(cfg)
    g = severe.refresh_times(st.get('gathered') or severe.gather(cfg, now), now)
    ev = severe.evaluate(cfg, g)
    cur, om = {}, g.get('current_om') or {}
    try:  # the planner's own numbers, so both screens agree
        w = plan_data.words(cfg)
        data = {'weather': plan_data.weather(cfg, now.date(), w), 'errors': []}
        plan_data.local_weather(cfg, data, now, tz, w)
        wx = data['weather']
        cur = {'now': wx.get('now', round(om['temperature_2m']) if om.get('temperature_2m') is not None else None),
               'max': wx.get('max'), 'min': wx.get('min'), 'icon': wx.get('icon'), 'text': wx.get('text')}
    except Exception as e:  # noqa: BLE001
        log(f'Severe weather: current conditions from the forecast only ({e})')
        if om.get('temperature_2m') is not None:
            cur = {'now': round(om['temperature_2m'])}
    if om.get('apparent_temperature') is not None and om.get('temperature_2m') is not None and cur.get('now') is not None:
        cur['feels'] = round(cur['now'] + om['apparent_temperature'] - om['temperature_2m'])
    if g['hours']:
        cur['pop'] = g['hours'][0]['pop']
    if om.get('wind_speed_10m') is not None:
        dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        d = om.get('wind_direction_10m')
        cur['wind_text'] = f"{round(om['wind_speed_10m'])} G {round(om.get('wind_gusts_10m') or 0)} mph" + \
            (f' {dirs[round(d / 45) % 8]}' if d is not None else '')
    rd = {}
    try:
        rd = radar.radar_map(cfg['weather']['lat'], cfg['weather']['lon'], *RADAR_SIZE,
                             zoom=int(severe.settings(cfg).get('radar_zoom', 9)))
    except Exception as e:  # noqa: BLE001
        log(f'Radar unavailable ({e})')
    return {'settings': severe.settings(cfg), 'active': ev['active'], 'reasons': ev['reasons'],
            'place': (cfg.get('weather') or {}).get('name', '').split(',')[0] or 'Home',
            'updated': now.strftime('%-I:%M %p'), 'alerts': g['alerts'], 'hours': g['hours'], 'eta': g['eta'],
            'rain_24': ev['rain_24'], 'gust_max': ev['gust_max'], 'gust_at': ev['gust_at'], 'current': cur,
            'radar': rd}


def show_severe(cfg, refresh=False, woke=False, until=None):
    def make(a, state):
        render.render_severe(build_severe(cfg), LAST_IMAGE)
    put_temporary(cfg, make, 'Severe weather monitoring updated' if refresh else 'Severe weather monitoring',
                  kind='severe', only_if_changed=refresh, woke=woke, until=until)


def show_severe_for(cfg, minutes=None):
    """Asked for by voice: the monitoring screen now, for a while, whatever the triggers say."""
    require_tv(cfg)
    minutes = minutes or int(severe.settings(cfg)['minutes'])
    woke = False
    if not tv.awake(cfg['tv_ip']):
        mac = load_state().get('tv_mac') or cfg.get('tv_mac')
        woke = bool(mac) and tv.wake(cfg['tv_ip'], mac)
        if not woke:
            raise RuntimeError('the TV is asleep and did not wake up')
    show_severe(cfg, woke=woke, until=time.time() + minutes * 60)


def preview_severe(cfg):
    render.render_severe(build_severe(cfg), PREVIEW)
    log('Severe weather preview created')


def dismiss_severe(cfg):
    """Hiding the monitoring screen keeps it away until the triggers change (e.g. a new alert)."""
    try:
        sev = severe.status(cfg)
    except Exception:  # noqa: BLE001
        return
    if sev.get('active'):
        with lock:
            state = load_state()
            state['severe_dismissed'] = sev.get('signature')
            save_state(state)


# ── Recipes shared from Paprika ─────────────────────────────────────────────────────────────────
RECIPE_ACTIONS = {'FRAME_RECIPE_MORE': '+30 min', 'FRAME_RECIPE_HIDE': 'Hide'}


def _recipe_cfg(cfg):
    return dict({'enabled': True, 'minutes': 60, 'notify': ''}, **(cfg.get('recipe') or {}))


def _recipe_photo(rec):
    import hashlib
    src = rec.get('image') or rec.get('source') or ''
    if not src:
        return None
    path = os.path.join(RECIPE_PHOTOS, hashlib.sha1(src.encode()).hexdigest()[:16] + '.jpg')
    if os.path.exists(path):
        return path
    if rec.get('image'):  # the recipe's own photo, from the page's recipe data
        return recipe.fetch_image(rec['image'], path) or recipe.fetch_photo(rec.get('source') or '', path)
    return recipe.fetch_photo(src, path)


def show_recipe(cfg, rec, minutes=None, notify=None, until=None):
    """Full-screen recipe on the TV until a time (minutes from now); wakes a sleeping TV, since
    someone just asked for it. Afterwards the plan (in its window) or the artwork comes back."""
    require_tv(cfg)
    minutes = minutes or _recipe_cfg(cfg)['minutes']
    until = until or time.time() + minutes * 60
    woke = False
    if not tv.awake(cfg['tv_ip']):
        mac = load_state().get('tv_mac') or cfg.get('tv_mac')
        woke = bool(mac) and tv.wake(cfg['tv_ip'], mac)
        if not woke:
            raise RuntimeError('the TV is asleep and did not wake up')
    photo = _recipe_photo(rec)
    until_text = datetime.datetime.fromtimestamp(until, zoneinfo.ZoneInfo(cfg['timezone'])).strftime('%-I:%M %p')

    def make(a, state):
        render.render_recipe(rec, LAST_IMAGE, artwork_background(cfg, a, state.get('restore_id')), photo, until_text)
    put_temporary(cfg, make, f"Recipe “{rec.get('name', '')[:40]}” until {until_text}", kind='recipe', woke=woke,
                  until=until)
    with open(RECIPE_FILE, 'w') as f:
        json.dump(rec, f)
    with lock:
        state = load_state()
        state['recipe_notify'] = notify or state.get('recipe_notify')
        save_state(state)
    tell_phone(cfg, state.get('recipe_notify'), f"{rec.get('name', 'Recipe')} is on the Frame until {until_text}")


def preview_recipe(cfg, rec):
    render.render_recipe(rec, PREVIEW, cached_artwork(cfg, load_state()), _recipe_photo(rec), 'example')
    log(f"Recipe preview “{rec.get('name', '')[:40]}”")


def extend_recipe(cfg, minutes=30):
    state = load_state()
    if state.get('showing') != 'recipe' or not os.path.exists(RECIPE_FILE):
        return
    with open(RECIPE_FILE) as f:
        rec = json.load(f)
    show_recipe(cfg, rec, until=max(state.get('until') or 0, time.time()) + minutes * 60)


def tell_phone(cfg, service, message):
    if not service or not ha.configured(cfg):
        return
    try:
        ha.notify(service, message, cfg, title='Frame', data={
            'tag': 'frame_recipe', 'actions': [{'action': k, 'title': v} for k, v in RECIPE_ACTIONS.items()]})
    except Exception as e:  # noqa: BLE001  the recipe still shows
        log(f'Could not notify the phone ({e})')


def clear_recipe_notification(cfg, state):
    service = state.pop('recipe_notify', None)
    if service and ha.configured(cfg):
        try:
            ha.notify(service, 'clear_notification', cfg, data={'tag': 'frame_recipe'})
        except Exception:  # noqa: BLE001
            pass


def on_share(data):
    """mobile_app.share from the Home Assistant phone app: Paprika's 'Recipe Text', or a link to a
    recipe page (Allrecipes, NYT Cooking, most recipe sites), goes on the TV."""
    cfg = config.load()
    rcfg = _recipe_cfg(cfg)
    text, caller = data.get('text') or '', data.get('caller') or ''
    if not rcfg['enabled']:
        return
    if recipe.looks_like_recipe(text):
        rec = recipe.parse(text)
    else:
        url = data.get('url') or recipe.first_url(text)
        if not url:
            if 'paprika' in caller:
                log('Shared from Paprika, but not as Recipe Text: choose Share → Recipe Text')
            return
        try:
            rec = recipe.from_url(url)
        except Exception as e:  # noqa: BLE001  not a recipe page (or the site refused): say why, show nothing
            log(f'Shared link not shown as a recipe ({e}): {url[:80]}')
            return
    service = rcfg['notify'] or None
    if not service:
        try:
            service = ha.notify_service_for(data.get('device_id'), cfg)
        except Exception:  # noqa: BLE001
            service = None
    log(f"Recipe shared: {rec['name']}")
    show_recipe(cfg, rec, rcfg['minutes'], service)


def on_notification_action(data):
    action = data.get('action')
    if action not in RECIPE_ACTIONS:
        return
    cfg = config.load()
    if action == 'FRAME_RECIPE_MORE':
        log('Recipe: 30 more minutes')
        extend_recipe(cfg)
    else:
        log('Recipe: hide requested from the phone')
        hide(cfg)


def listen_home_assistant():
    ha.listen(config.load, {'mobile_app.share': on_share, 'mobile_app_notification_action': on_notification_action},
              log=log)


def watch_home_assistant():
    """Poll the show and hide buttons every few seconds; a press (a button's state is the time it was
    pressed) shows the plan or brings the artwork back. The first reading after a start is only
    remembered, never acted on."""
    last, failing, tracked_at = {}, False, 0.0
    while True:
        try:
            cfg = config.load()
            if time.time() - tracked_at >= 60:  # notice tasks checked off on lists that drop them (Alexa)
                tracked_at = time.time()
                try:
                    for summary in todo_track.poll(cfg):
                        log(f'Task completed: {summary}')
                except Exception as e:  # noqa: BLE001
                    print('todo tracking:', e, file=sys.stderr)
            ents = (cfg.get('home_assistant') or {}).get('entities') or {}
            if ha.configured(cfg) and cfg.get('tv_ip'):
                for key in ('show_button', 'hide_button', 'severe_button'):
                    entity = ents.get(key)
                    if not entity:
                        continue
                    pressed = ha.state(entity, cfg).get('state')
                    if key in last and pressed != last[key] and pressed not in ('unknown', 'unavailable', None):
                        try:
                            if key == 'show_button':
                                minutes = int(cfg['home_assistant'].get('show_minutes', 30))
                                log(f'Plan requested from Home Assistant: showing it for {minutes} minutes')
                                show_for(cfg, minutes)
                            elif key == 'severe_button':
                                log('Severe weather display requested from Home Assistant')
                                show_severe_for(cfg)
                            else:
                                log('Hide requested from Home Assistant: back to the artwork')
                                hide(cfg)
                        except Exception as e:  # noqa: BLE001
                            log(f'Could not do that ({type(e).__name__}: {e})')
                    last[key] = pressed
                failing = False
        except Exception as e:  # noqa: BLE001  Home Assistant restarting: say so once
            if not failing:
                log(f'Home Assistant not reachable for the show/hide buttons ({e}); still trying')
            failing = True
        time.sleep(3)


def scheduler():
    memo = {}
    while True:
        try:
            cfg = config.load()
            tick(cfg, now_local(cfg), memo)
        except Exception as e:  # noqa: BLE001  e.g. the TV dropped off mid-upload: try again shortly
            log(f'Error ({type(e).__name__}: {e}), retrying in {RETRY_AFTER // 60} minutes')
            traceback.print_exc()
            memo['retry_at'] = time.time() + RETRY_AFTER
        time.sleep(TICK)


def start_scheduler():
    threading.Thread(target=scheduler, name='scheduler', daemon=True).start()
    threading.Thread(target=watch_home_assistant, name='ha-watch', daemon=True).start()
    threading.Thread(target=listen_home_assistant, name='ha-events', daemon=True).start()
