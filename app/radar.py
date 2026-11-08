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
from PIL import Image, ImageChops, ImageFilter

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
    """(dark map, place-name layer) centred on (lat, lon), w x h pixels; (None, None) if offline.
    The names come separately so they can go on top of the radar."""
    if os.path.isdir(OLD_CACHE):
        shutil.rmtree(OLD_CACHE, ignore_errors=True)   # watermarked CARTO tiles from before
    cx, cy = world_px(lat, lon, zoom)
    x0, y0 = cx - w / 2, cy - h / 2
    base, got = _tiles(BASE, zoom, 2, x0, y0, w, h)
    if not got:
        return None, None
    img = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    img.alpha_composite(base)
    labels, _ = _tiles(LABELS, zoom, 1, x0, y0, w, h)
    return img, labels


def _otsu(hist):
    total = sum(hist)
    sum_all = sum(i * c for i, c in enumerate(hist))
    best, t, w0, s0 = -1, 0, 0, 0
    for i, c in enumerate(hist):
        w0 += c
        s0 += i * c
        if not w0 or w0 == total:
            continue
        m0, m1 = s0 / w0, (sum_all - s0) / (total - w0)
        between = w0 * (total - w0) * (m0 - m1) ** 2
        if between > best:
            best, t, sep = between, i, m1 - m0
    return t, (sep if best >= 0 else 0)


def coastline(base, key=None):
    """The shoreline as a thin line layer, found in the map itself (water and land are two shades), so
    it lines up exactly. Roads and specks are filtered out; None when the view has no clear coast."""
    path = os.path.join(CACHE, f'coast-{key}.png') if key else None
    if path and os.path.exists(path):
        return Image.open(path).convert('L')
    grey = base.convert('L').filter(ImageFilter.MedianFilter(5))
    t, sep = _otsu(grey.histogram())
    if sep < 4:
        return None  # one shade only: all land (or all sea)
    mask = grey.point(lambda v: 255 if v > t else 0)
    for f in (ImageFilter.MinFilter(7), ImageFilter.MaxFilter(7), ImageFilter.MaxFilter(9), ImageFilter.MinFilter(9)):
        mask = mask.filter(f)  # open (drop roads), then close (drop ponds and pools)
    water = mask.histogram()[0] / (mask.width * mask.height)
    edge = ImageChops.difference(mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3)))
    share = (edge.width * edge.height - edge.histogram()[0]) / (edge.width * edge.height)
    if not 0.02 < water < 0.98 or share > 0.03:
        return None  # not a clean land/sea split: better no line than a scribble
    if path:
        os.makedirs(CACHE, exist_ok=True)
        edge.save(path)
    return edge


def _outlined(colour, shape, glow):
    """A layer in colour shaped like `shape` (an L mask), on a soft dark outline that keeps it
    readable over bright radar colours."""
    out = Image.new('RGBA', shape.size, (0, 0, 0, 0))
    halo = shape.filter(ImageFilter.MaxFilter(glow)).filter(ImageFilter.GaussianBlur(2))
    out.paste((4, 8, 14, 255), mask=halo.point(lambda v: round(v * 0.7)))
    out.paste(colour, mask=shape)
    return out


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
    """{'image': RGBA w x h, 'source', 'time', 'm_per_px'}. Drawn bottom to top: map, radar, then the
    coastline and place names, so the geography stays readable even under a sky full of rain."""
    base, labels = base_map(lat, lon, zoom, w, h)
    has_map = base is not None
    if base is None:
        base = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    coast = coastline(base, f'{lat:.3f}_{lon:.3f}_{zoom}_{w}x{h}') if has_map else None
    # The canvas is very dark: lift it a little so roads read from across the room.
    img = Image.eval(base.convert('RGB'), lambda v: min(255, round(v * 1.25 + 6))).convert('RGBA')
    radar, source = radar_overlay(lat, lon, zoom, w, h)
    if radar is not None:
        radar = smooth(radar, w, h)
        radar.putalpha(radar.getchannel('A').point(lambda v: min(255, round(v * 0.85))))
        img.alpha_composite(radar)
    if coast is not None:
        img.alpha_composite(_outlined((150, 205, 235, 235), coast.filter(ImageFilter.MaxFilter(3)), 9))
    if labels is not None:
        img.alpha_composite(_outlined((0, 0, 0, 0), labels.getchannel('A'), 7))  # dark halo only
        img.alpha_composite(labels)
    return {'image': img, 'source': source, 'time': datetime.datetime.now().astimezone() if source else None,
            'm_per_px': metres_per_px(lat, zoom), 'has_map': has_map}


def fit_zoom(points, w, h, pad=0.14, zmin=3, zmax=10):
    """The closest zoom at which all (lat, lon) points fit in w x h with a margin."""
    for z in range(zmax, zmin - 1, -1):
        xs, ys = zip(*(world_px(lat, lon, z) for lat, lon in points))
        if max(xs) - min(xs) <= w * (1 - 2 * pad) and max(ys) - min(ys) <= h * (1 - 2 * pad):
            return z
    return zmin


def map_view(points, w, h, pad=0.14):
    """A dark map framing all the points: {'base', 'labels', 'coast', 'zoom', 'project'(lat, lon) -> (x, y)}.
    Bottom to top the caller draws base, its own lines, coast, then labels."""
    zoom = fit_zoom(points, w, h, pad)
    xs, ys = zip(*(world_px(lat, lon, zoom) for lat, lon in points))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    x0, y0 = cx - w / 2, cy - h / 2
    base, got = _tiles(BASE, zoom, 2, x0, y0, w, h)
    img = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    labels = coast = None
    if got:
        img.alpha_composite(base)
        coast = coastline(img, f'v{zoom}_{round(cx)}_{round(cy)}_{w}x{h}')
        labels, _ = _tiles(LABELS, zoom, 1, x0, y0, w, h)
        img = Image.eval(img.convert('RGB'), lambda v: min(255, round(v * 1.25 + 6))).convert('RGBA')

    def project(lat, lon):
        x, y = world_px(lat, lon, zoom)
        return x - x0, y - y0
    return {'base': img, 'labels': labels, 'coast': coast, 'zoom': zoom, 'project': project, 'has_map': bool(got)}


def overlay_geography(img, view):
    """Coastline and place names over whatever was drawn on the map."""
    if view.get('coast') is not None:
        img.alpha_composite(_outlined((150, 205, 235, 200), view['coast'].filter(ImageFilter.MaxFilter(3)), 9))
    if view.get('labels') is not None:
        img.alpha_composite(_outlined((0, 0, 0, 0), view['labels'].getchannel('A'), 7))
        img.alpha_composite(view['labels'])
    return img
