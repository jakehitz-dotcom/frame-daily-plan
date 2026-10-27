"""The actions: preview, show the plan, bring the artwork back, and the daily scheduler."""
import collections
import datetime
import hashlib
import json
import os
import threading
import time
import traceback
import zoneinfo

import config
import ha
import plan_data
import render
import tv
import week

STATE_FILE = os.path.join(config.DATA, 'state.json')
LOG_FILE = os.path.join(config.DATA, 'frame-daily-plan.log')
PREVIEW = os.path.join(config.DATA, 'preview.jpg')
LAST_IMAGE = os.path.join(config.DATA, 'last.jpg')
ART_CACHE = os.path.join(config.DATA, 'art')
RETRY_AFTER = 2 * 60  # after an error talking to an awake TV
TICK = 15             # how often the scheduler looks; also how fast a TV waking up is noticed
WAKE_EVERY = 10 * 60  # at most one wake-up attempt per 10 minutes

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


def _due(cfg, state, now):
    if not cfg.get('tv_ip'):
        return None  # not set up yet
    if state.get('hide_pending'):
        return 'restore'  # asked to hide while the TV slept: do it the moment it wakes
    every = int(cfg['schedule'].get('refresh_minutes', 5) or 0) * 60
    refresh = 'refresh' if every and state.get('showing') == 'plan' and now.timestamp() - state.get('shown_at', 0) >= every else None
    view = view_for(cfg, now)
    if view:
        if state.get('shown_on') != now.date().isoformat() or state.get('shown_view', 'day') != view:
            return 'show'  # also when Sunday's morning plan gives way to the week ahead
        return refresh  # a message, demo or "Back to artwork" in between is left alone
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
    action = _due(cfg, state, now)
    if not action or time.time() < memo.get('retry_at', 0):
        return None
    woke = False
    if not tv.awake(cfg['tv_ip']):
        mac = state.get('tv_mac') or cfg.get('tv_mac')
        if action == 'show' and cfg.get('wake_tv', True) and mac and time.time() - memo.get('woke_at', 0) >= WAKE_EVERY:
            memo['woke_at'] = time.time()
            log('Waking the TV for the plan')
            woke = tv.wake(cfg['tv_ip'], mac)
            if not woke:
                log('The TV did not wake up (is "Power On with Mobile" on?). The plan goes up as soon as it wakes.')
        if not woke:
            if not memo.get('asleep_logged'):
                memo['asleep_logged'] = True
                if action != 'show' or not mac:
                    what = {'show': 'the plan', 'refresh': 'the plan update', 'restore': 'putting the artwork back'}[action]
                    log(f'TV asleep: {what} goes ahead as soon as it wakes')
            return None
    memo['asleep_logged'] = False
    if action == 'restore':
        restore(cfg)
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
    """Back to the artwork now (a voice request). A sleeping TV is not woken: the artwork goes back
    the moment it wakes, before anyone sees the plan."""
    require_tv(cfg)
    if tv.awake(cfg['tv_ip']):
        restore(cfg)
        return
    with lock:
        state = load_state()
        state.update(hide_pending=True, showing=None, until=None)
        save_state(state)
    log('TV asleep: the artwork goes back as soon as it wakes')


def watch_home_assistant():
    """Poll the show and hide buttons every few seconds; a press (a button's state is the time it was
    pressed) shows the plan or brings the artwork back. The first reading after a start is only
    remembered, never acted on."""
    last, failing = {}, False
    while True:
        try:
            cfg = config.load()
            ents = (cfg.get('home_assistant') or {}).get('entities') or {}
            if ha.configured(cfg) and cfg.get('tv_ip'):
                for key in ('show_button', 'hide_button'):
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
