"""Web UI for Frame Daily Plan: setup, status, actions and settings. Runs the scheduler too.

Optional protection: set FRAME_PLAN_PASSWORD; the browser then asks for it (any user name).
"""
import functools
import os
import sys
import threading
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also when started with python -I

from flask import Flask, Response, redirect, render_template, request, send_file, url_for
from waitress import serve

import holidays  # noqa: E402

import config  # noqa: E402
import i18n  # noqa: E402
import plan_data  # noqa: E402
import jobs  # noqa: E402
import tv  # noqa: E402
import ha  # noqa: E402

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024  # phone photos
PASSWORD = os.environ.get('FRAME_PLAN_PASSWORD', '')


def ui():
    return i18n.texts(config.load().get('ui_lang', 'de'))


def protected(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if PASSWORD and not (auth and auth.password == PASSWORD):
            return Response('Password required', 401, {'WWW-Authenticate': 'Basic realm="Frame Daily Plan"'})
        return view(*args, **kwargs)
    return wrapper


@app.get('/')
@protected
def index():
    cfg = config.load()
    lang = cfg.get('ui_lang', 'en')
    setup = {'tv': bool(cfg.get('tv_ip')), 'paired': os.path.exists(os.path.join(config.DATA, 'paired')),
             'place': cfg['weather'].get('lat') is not None, 'calendar': bool(cfg['calendars'])}
    return render_template('index.html', cfg=cfg, state=jobs.load_state(), log=list(jobs.recent)[-40:][::-1],
                           colors=config.COLORS, lang=lang, t=i18n.texts(lang), setup=setup,
                           setup_done=all(setup.values()), countries=sorted(holidays.list_supported_countries()),
                           has_preview=os.path.exists(jobs.PREVIEW), message=request.args.get('msg'))


@app.get('/discover')
@protected
def discover():
    return {'tvs': tv.discover()}


@app.get('/geocode')
@protected
def geocode():
    return {'results': plan_data.geocode(request.args.get('q', ''), config.load().get('ui_lang', 'en'))}


@app.post('/set-tv')
@protected
def set_tv():
    cfg = config.load()
    cfg['tv_ip'] = request.form.get('ip', '').strip()
    config.save(cfg)
    jobs.log(f"TV set to {cfg['tv_ip']}")
    return redirect(url_for('index', msg=ui()['saved']) + '#setup')


@app.post('/set-place')
@protected
def set_place():
    f = request.form
    cfg = config.load()
    cfg['weather'].update({'name': f.get('label', ''), 'lat': float(f['lat']), 'lon': float(f['lon']), 'enabled': True})
    try:
        zoneinfo.ZoneInfo(f.get('timezone', ''))
        cfg['timezone'] = f['timezone']
    except Exception:  # noqa: BLE001
        pass
    code = f.get('country_code', '').upper()
    if code in holidays.list_supported_countries() and not cfg['holidays'].get('country'):
        cfg['holidays'] = {'country': code, 'subdiv': ''}
    config.save(cfg)
    jobs.log(f"Place set to {cfg['weather']['name']}")
    return redirect(url_for('index', msg=ui()['saved']) + '#setup')


@app.get('/regions/<country>')
@protected
def regions(country):
    return {'regions': sorted(holidays.list_supported_countries().get(country.upper(), []))}


@app.get('/lang/<code>')
@protected
def set_lang(code):
    if code in i18n.TEXT:
        cfg = config.load()
        cfg['ui_lang'] = code
        config.save(cfg)
    return redirect(url_for('index'))


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/status')
@protected
def status():
    cfg = config.load()
    try:
        s = tv.status(cfg['tv_ip'])
        return {'ok': True, **s}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


@app.get('/diag')
@protected
def diag():
    return {'steps': tv.diagnose(config.load()['tv_ip'])}


@app.post('/pair')
@protected
def pair():
    # Triggers the TV's "allow this device?" prompt and waits up to two minutes for it.
    steps = tv.diagnose(config.load()['tv_ip'])
    t = ui()
    text = ' · '.join(f"{t['step_' + n]}: {'ok' if r == 'ok' else t['error'] + ' (' + d + ')'}" for n, r, d, _ in steps)
    ok = len(steps) == 3 and all(r == 'ok' for _, r, _, _ in steps)
    if ok:
        open(os.path.join(config.DATA, 'paired'), 'w').close()
        state = jobs.load_state()
        jobs.learn_mac(config.load(), state)
        jobs.save_state(state)
    jobs.log(f'Connect: {text}')
    return redirect(url_for('index', msg=f"{t['connected'] if ok else t['not_connected']} – {text}"))


@app.get('/preview.jpg')
@protected
def preview_image():
    return send_file(jobs.PREVIEW, mimetype='image/jpeg', max_age=0)


def run_action(key, fn):
    t = ui()
    try:
        fn()
        return redirect(url_for('index', msg=f"{t[key]}: {t['done']}"))
    except Exception as e:  # noqa: BLE001
        jobs.log(f"{i18n.TEXT['en'][key]} failed: {e}")
        return redirect(url_for('index', msg=f"{t[key]} {t['failed']}: {e}"))


@app.post('/preview')
@protected
def make_preview():
    return run_action('a_preview', lambda: jobs.preview(config.load()))


@app.post('/show')
@protected
def show_now():
    return run_action('a_show', lambda: jobs.show_plan(config.load()))


@app.post('/restore')
@protected
def restore_now():
    return run_action('a_restore', lambda: jobs.restore(config.load()))


@app.post('/upload')
@protected
def upload():
    f = request.files.get('image')
    if not f or not f.filename:
        return redirect(url_for('index', msg=ui()['pick_image']))
    src = os.path.join(config.DATA, 'upload-original')
    f.save(src)
    mode = request.form.get('mode', 'crop')
    try:
        return run_action('a_upload', lambda: jobs.upload_artwork(config.load(), src, mode))
    finally:
        try:
            os.remove(src)
        except OSError:
            pass


@app.post('/demo')
@protected
def demo():
    lang = request.form.get('lang', 'de')
    on_tv = request.form.get('target') == 'tv'
    return run_action('a_demo_tv' if on_tv else 'a_demo_preview', lambda: jobs.show_demo(config.load(), lang, on_tv))


@app.post('/text')
@protected
def text():
    return run_action('a_text', lambda: jobs.show_text(config.load(), request.form.get('text', ''),
                                                               request.form.get('theme', 'dark')))


@app.post('/restart')
@protected
def restart():
    # The container restarts itself (restart: unless-stopped); app/ is mounted, so code
    # copied into the folder takes effect without rebuilding.
    jobs.log('Restart requested')
    threading.Timer(1.0, os._exit, [0]).start()
    return redirect(url_for('index', msg=ui()['restarting']))


@app.post('/settings')
@protected
def save_settings():
    f = request.form
    cfg = config.load()
    cfg['tv_ip'] = f.get('tv_ip', '').strip()
    cfg['tv_mac'] = f.get('tv_mac', '').strip().lower()
    cfg['wake_tv'] = 'wake_tv' in f
    tz = f.get('timezone', '').strip()
    try:
        zoneinfo.ZoneInfo(tz)
        cfg['timezone'] = tz
    except Exception:  # noqa: BLE001  keep the old one if the name is not a real time zone
        pass
    cfg['plan_lang'] = f.get('plan_lang', 'en')
    cfg['schedule'] = {
        'enabled': 'schedule_enabled' in f,
        'start': f.get('start', '07:00'),
        'end': f.get('end', '10:00'),
        'days': [i for i in range(7) if f'day{i}' in f],
        'refresh_minutes': min(120, int(f['refresh_minutes'])) if f.get('refresh_minutes', '').strip().isdigit() else 5,
    }
    cfg['weather'] = dict(cfg['weather'], **{
        'enabled': 'weather_enabled' in f,
        'name': f.get('weather_name', '').strip(),
        'lat': float(f['lat']) if f.get('lat') else None,
        'lon': float(f['lon']) if f.get('lon') else None,
        'unit': 'C' if f.get('temp_unit') == 'C' else 'F',
    })
    cfg['holidays'] = {'country': f.get('hol_country', '').strip().upper(), 'subdiv': f.get('hol_subdiv', '').strip().upper()}
    cfg['dim_past_events'] = 'dim_past_events' in f
    cfg['background'] = f.get('background', 'artwork')
    if 'todo_hint' in f:
        cfg['todo_hint'] = f['todo_hint'].strip()
    calendars = []
    for i in range(int(f.get('cal_count', 0)) + 1):  # the extra row adds a new calendar
        url = f.get(f'cal_url{i}', '').strip()
        if url and f'cal_delete{i}' not in f:
            calendars.append({'name': f.get(f'cal_name{i}', '').strip() or 'Calendar', 'url': url,
                              'color': f.get(f'cal_color{i}', 'green'), 'role': f.get(f'cal_role{i}', 'events')})
    cfg['calendars'] = calendars
    _read_ha_fields(f, cfg)
    if cfg['schedule']['start'] >= cfg['schedule']['end']:
        return redirect(url_for('index', msg=ui()['end_after_start']))
    config.save(cfg)
    jobs.log('Settings saved')
    return redirect(url_for('index', msg=ui()['saved']))


def _read_ha_fields(f, cfg):
    """The token is never sent back to the browser, so an empty field keeps the saved one."""
    if 'ha_url' not in f:
        return
    cur = cfg.get('home_assistant') or {}
    url = f.get('ha_url', '').strip().rstrip('/')
    if url and '://' not in url:
        url = 'http://' + url
    cfg['home_assistant'] = dict(cur, url=url, token=f.get('ha_token', '').strip() or cur.get('token', ''))
    if 'ha_todo_lists' in f:
        cfg['home_assistant']['todo_lists'] = [e.strip() for e in f['ha_todo_lists'].replace(',', ' ').split()
                                               if e.strip().startswith('todo.')]


@app.post('/banner')
@protected
def set_banner():
    """Save (or clear) the banner and its options, render a preview, and update the TV if the
    plan is on it right now."""
    f = request.form
    cfg = config.load()
    clear = f.get('action') == 'clear'
    cfg['banner'] = {'text': '' if clear else f.get('banner_text', '').strip(),
                     'until': '' if clear else f.get('banner_until', '').strip()}
    cfg['greetings'] = 'greetings' in f
    cfg['christmas_countdown'] = 'christmas_countdown' in f
    config.save(cfg)
    jobs.log('Banner cleared' if clear or not cfg['banner']['text'] else
             f"Banner “{cfg['banner']['text'][:40]}”" + (f" until {cfg['banner']['until']}" if cfg['banner']['until'] else ''))
    try:
        jobs.preview(cfg)
    except Exception as e:  # noqa: BLE001
        jobs.log(f'Preview failed: {e}')
    if jobs.load_state().get('showing') == 'plan':
        threading.Thread(target=jobs.refresh_now, args=(cfg,), daemon=True).start()
    return redirect(url_for('index', msg=ui()['banner_saved']))


@app.post('/wake')
@protected
def wake_now():
    """Test Wake-on-LAN: put the TV to sleep (walk away), then press this."""
    cfg = config.load()
    state = jobs.load_state()
    mac = state.get('tv_mac') or cfg.get('tv_mac')
    if tv.awake(cfg['tv_ip']):
        jobs.learn_mac(cfg, state)
        jobs.save_state(state)
        text = 'the TV is already awake' + (f' (MAC {state["tv_mac"]} saved)' if state.get('tv_mac') else '')
    elif not mac:
        text = 'no MAC address yet: it is saved the next time the TV is awake, or enter it under TV & schedule'
    else:
        text = 'the TV woke up' if tv.wake(cfg['tv_ip'], mac) else \
            'no answer within a minute: check "Power On with Mobile" on the TV (Settings → General → Network → Expert Settings)'
    jobs.log(f'Wake TV: {text}')
    return redirect(url_for('index', msg=f'Wake TV: {text}'))


@app.post('/ha-test')
@protected
def ha_test():
    cfg = config.load()
    _read_ha_fields(request.form, cfg)
    config.save(cfg)
    try:
        text = ha.check(cfg)
    except Exception as e:  # noqa: BLE001
        text = f'{ui()["error"]} ({type(e).__name__}: {e})'[:300]
    jobs.log(f'Home Assistant: {text}')
    return redirect(url_for('index', msg=f'Home Assistant: {text}'))


def main():
    os.makedirs(config.DATA, exist_ok=True)
    jobs.log('Frame Daily Plan started')
    jobs.start_scheduler()
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8765')), threads=4)


if __name__ == '__main__':
    main()
