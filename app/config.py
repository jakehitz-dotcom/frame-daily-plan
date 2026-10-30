"""Settings, stored as JSON in the data volume and edited through the web UI."""
import copy
import json
import os

DATA = os.environ.get('FRAME_PLAN_DATA', '/data')
CONFIG_FILE = os.path.join(DATA, 'config.json')

DEFAULTS = {
    'tv_ip': '',
    'tv_mac': 'cc:20:ac:c6:57:f5',  # Wake-on-LAN (kitchen Frame, from Home Assistant); also learned while the TV is awake
    'wake_tv': True,   # wake a sleeping TV at the start of the plan window
    'timezone': os.environ.get('TZ') or 'UTC',
    'ui_lang': 'en',
    'plan_lang': 'en',
    # role "events" shows entries as appointments, "todos" puts every entry on the to-do list
    'calendars': [],
    # unit: 'F' or 'C'; stations: NWS stations for the sky right now, first fresh one wins (US only)
    'weather': {'enabled': True, 'name': '', 'lat': None, 'lon': None, 'unit': 'F', 'stations': 'KTOA, KHHR'},
    'holidays': {'country': '', 'subdiv': ''},
    'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4, 5, 6],
                 'refresh_minutes': 5},  # 0 = show once per morning
    'dim_past_events': True,
    # The week ahead (Mon-Sun) instead of the daily plan in this window; day: 0 = Monday ... 6 = Sunday
    'week_view': {'enabled': True, 'day': 6, 'start': '17:00', 'end': '21:00'},
    # read-only REST access, see ha.py; entities: which Home Assistant entities feed the plan ('' = not shown)
    'home_assistant': {'url': '', 'token': '', 'entities': {
        'thermostat': 'climate.den_thermostat',            # inside temperature + set temperature by mode
        'indoor_temp': '',                                  # a separate indoor sensor; empty = the thermostat's reading
        'hot_tub': 'sensor.back_patio_time_machine_current_temperature',
        'dishwasher_clean': 'input_boolean.dishwasher_clean',  # on = cycle finished, door not opened since
        'wind_7d': 'input_number.rolling_hills_max_wind_7_day',
        'rain_7d': 'input_number.rolling_hills_rain_7_day',
        'outdoor_temp': '',                     # an outdoor thermometer at the house; '' = the NWS grid cell
        'last_bottle': 'sensor.finn_bottle',   # Huckleberry: time of the last bottle
        'show_button': 'input_button.frame_planner_show',  # pressed (e.g. by Alexa): show the plan for a while
        'hide_button': 'input_button.frame_planner_hide',  # pressed: back to the artwork now
        'severe_button': 'input_button.frame_severe_show',  # pressed: severe weather monitoring screen
    }, 'show_minutes': 30,
        'todo_lists': ['todo.jakehitz_yahoo_com_to_do_list'],  # Home Assistant to-do lists (e.g. Google Tasks: todo.my_tasks) added to the to-do panel
    },
    # Banner across the top of the plan: a custom message wins over birthdays, which win over holidays.
    'banner': {'text': '', 'until': ''},  # until: last day shown (YYYY-MM-DD), '' = until cleared
    'birthdays': [{'date': '02-01', 'text': 'Happy Birthday Mike!'}, {'date': '07-26', 'text': 'Happy Birthday Jake!'},
                  {'date': '08-09', 'text': 'Happy Birthday Finn!'}],
    'greetings': True,             # birthdays and American holiday greetings
    'christmas_countdown': True,   # Dec 1-24
    # beside the To do title, '|' between its two lines; '' = none
    # Analog clock behind the day of the week; style: ring, disc or minimal
    'clock': {'enabled': True, 'style': 'ring', 'opacity': 0.28, 'ahead_minutes': 3},
    # Recipes shared from Paprika (Share → Recipe Text → Home Assistant); notify '' = the phone that shared
    'recipe': {'enabled': True, 'minutes': 60, 'notify': ''},
    # Severe weather monitoring: shown outside the planner windows while, in the next 24 h, rain > precip_in,
    # a gust > gust_mph, or any NWS alert; wakes the TV once per episode outside quiet hours
    'severe': {'enabled': True, 'precip_in': 0.3, 'gust_mph': 25, 'minutes': 30, 'wake': True,
               'quiet_start': '22:00', 'quiet_end': '06:00', 'radar_zoom': 9},
    'todo_hint': 'Alexa, add [task] to my to-do list | Alexa, mark [task] as complete',
    'background': 'artwork',  # "artwork": the picture the plan replaces, "default": bundled background
}

COLORS = ['green', 'blue', 'orange', 'red', 'grey']


# Earlier default texts, replaced by the current default when found unchanged in a saved config.
RETIRED = {'todo_hint': ['Alexa, add to my to-do list: [task] · or check it off in the app',
                         'Alexa, ask Easy Task to: add [task] OR complete [task]']}


def load():
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                for k, v in value.items():
                    if isinstance(v, dict) and isinstance(cfg[key].get(k), dict):
                        cfg[key][k].update(v)
                    else:
                        cfg[key][k] = v
            else:
                cfg[key] = value
    for key, old in RETIRED.items():
        if cfg.get(key) in old:
            cfg[key] = DEFAULTS[key]
    return cfg


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o600)  # calendar links are secrets
    os.replace(tmp, CONFIG_FILE)
