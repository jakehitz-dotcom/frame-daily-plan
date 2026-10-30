"""Radar map around the house: a dark map (Esri Dark Gray Canvas, no key needed; cached on disk since it
never changes) with place names on top, and the latest radar over both.

Radar sources, first that answers wins:
1. NOAA MRMS composite base reflectivity (mapservices.weather.noaa.gov), exported for the exact view
2. Iowa State (IEM) NEXRAD composite tiles
"""
import datetime
import io
import math
import os
import shutil

import requests
from PIL import Image, ImageFilter

import config

HEADERS = {'User-Agent': 'frame-daily-plan (github.com/jakehitz-dotcom/frame-daily-plan)'}
CACHE = os.path.join(config.DATA, 'map-tiles-esri')
OLD_CACHE = os.path.join(config.DATA, 'map-tiles')   # CARTO tiles, now watermarked "API key required"
TILE = 512            # @2x base map tiles
R = 20037508.342789244
MRMS = ('https://mapservices.weather.noaa.gov/eventdriven/rest/services/radar/radar_base_reflectivity/MapServer/'
        'export')
IEM = 'https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png'
ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{layer}/MapServer/tile/{z}/{y}/{x}'
BASE, LABELS = 'World_Dark_Gray_Base', 'World_Dark_Gray_Reference'


def world_px(lat, lon, zoom):
    n = TILE * 2 ** zoom
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def metres_per_px(lat, zoom):
    return 2 * math.pi * 6378137 * math.cos(math.radians(lat)) / (TILE * 2 ** zoom)


def _get(url, timeout=12):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    if not r.headers.get('Content-Type', 'image/').startswith('image/'):
        raise RuntimeError('not an image')
    return Image.open(io.BytesIO(r.content)).convert('RGBA')


def _tiles(layer, zoom, scale, x0, y0, w, h):
    """Esri 256 px tiles for a w x h view whose top-left is (x0, y0) in 512-px world pixels at `zoom`.
    scale 2: tiles from zoom+1 at full size (sharp); scale 1: tiles from zoom drawn 2x (bigger labels).
    Returns (image, tiles drawn)."""
    z = zoom + (1 if scale == 2 else 0)
    size = 256 if scale == 2 else 512                      # drawn size of one tile, in 512-px world pixels
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    got = 0
    for tx in range(int(x0 // size), int((x0 + w) // size) + 1):
        for ty in range(int(y0 // size), int((y0 + h) // size) + 1):
            path = os.path.join(CACHE, layer, f'{z}-{tx}-{ty}.png')
            try:
                if os.path.exists(path):
                    tile = Image.open(path).convert('RGBA')
                else:
                    tile = _get(ESRI.format(layer=layer, z=z, x=tx, y=ty))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    tile.save(path)
                img.alpha_composite(tile.resize((size, size), Image.LANCZOS), (round(tx * size - x0), round(ty * size - y0)))
                got += 1
            except Exception:  # noqa: BLE001  a missing tile leaves that patch dark
                continue
    return img, got


def base_map(lat, lon, zoom, w, h):
    """Dark map with place names centred on (lat, lon), w x h pixels; None if no tile could be had."""
    if os.path.isdir(OLD_CACHE):
        shutil.rmtree(OLD_CACHE, ignore_errors=True)   # watermarked CARTO tiles from before
    cx, cy = world_px(lat, lon, zoom)
    x0, y0 = cx - w / 2, cy - h / 2
    base, got = _tiles(BASE, zoom, 2, x0, y0, w, h)
    if not got:
        return None
    img = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    img.alpha_composite(base)
    # The canvas is very dark: lift it a little so the coast and roads read from across the room.
    img = Image.eval(img.convert('RGB'), lambda v: min(255, round(v * 1.25 + 6))).convert('RGBA')
    labels, _ = _tiles(LABELS, zoom, 1, x0, y0, w, h)
    img.alpha_composite(labels)
    return img


def radar_overlay(lat, lon, zoom, w, h):
    """(image, source) of the latest radar for the same view, or (None, None)."""
    cx, cy = world_px(lat, lon, zoom)
    n = TILE * 2 ** zoom

    def merc(px, py):
        return px / n * 2 * R - R, R - py / n * 2 * R
    xmin, ymax = merc(cx - w / 2, cy - h / 2)
    xmax, ymin = merc(cx + w / 2, cy + h / 2)
    try:
        url = (f'{MRMS}?bbox={xmin},{ymin},{xmax},{ymax}&bboxSR=3857&imageSR=3857&size={w // 4},{h // 4}'
               f'&format=png32&transparent=true&dpi=96&f=image')
        return _get(url, timeout=15), 'NOAA MRMS'
    except Exception:  # noqa: BLE001
        pass
    try:  # IEM tiles are 256 px: draw each at 2x to match the @2x base map grid
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        x0, y0 = cx - w / 2, cy - h / 2
        for tx in range(int(x0 // TILE), int((x0 + w) // TILE) + 1):
            for ty in range(int(y0 // TILE), int((y0 + h) // TILE) + 1):
                tile = _get(IEM.format(z=zoom, x=tx, y=ty), timeout=10).resize((TILE, TILE), Image.NEAREST)
                img.alpha_composite(tile, (round(tx * TILE - x0), round(ty * TILE - y0)))
        return img, 'IEM NEXRAD'
    except Exception:  # noqa: BLE001
        return None, None


def smooth(radar, w, h):
    """Radar is ~1 km per cell, which is 8 screen pixels here: blocky. Shrink, then enlarge with a smooth
    filter (premultiplied alpha, so edges fade instead of fringing) for the soft look of a TV weather map."""
    small = radar.convert('RGBa')
    if small.width > w // 3:
        small = small.resize((w // 4, h // 4), Image.BOX)
    return small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(3)).convert('RGBA')


def radar_map(lat, lon, w, h, zoom=9):
    """{'image': RGBA w x h, 'source', 'time', 'm_per_px'}: base map + radar, brightened a little."""
    base = base_map(lat, lon, zoom, w, h)
    if base is None:
        base = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    radar, source = radar_overlay(lat, lon, zoom, w, h)
    if radar is not None:
        radar = smooth(radar, w, h)
        a = radar.getchannel('A').point(lambda v: min(255, round(v * 0.92)))
        radar.putalpha(a)
        base.alpha_composite(radar)
    return {'image': base, 'source': source, 'time': datetime.datetime.now().astimezone() if source else None,
            'm_per_px': metres_per_px(lat, zoom), 'has_map': base is not None}
