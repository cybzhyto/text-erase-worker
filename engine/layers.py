"""What a render body's layers show: shared by auto_zones.py (zones keep off kept source pixels) and build_math.py."""


def opaque_span(shape):
    """A linear-gradient alpha mask -> (axis, lo %, hi %) of its opaque part; axis 'x' when it runs left to right."""
    stops = shape.get('fill_color')
    if not isinstance(stops, list): return None
    on = [float(str(s['offset']).rstrip('%')) for s in stops if str(s.get('color', '')).replace(' ', '').endswith(',1)')]
    if not on: return None
    horiz = str(shape.get('fill_x0', '')) != str(shape.get('fill_x1', '')) and shape.get('fill_x0') is not None
    return ('x' if horiz else 'y', min(on), max(on))


def orig_rect(e):
    """Where a kept «orig …» layer shows the source's own pixels: [x0, y0, x1, y1] % of the frame (gradient masks)."""
    r = {'x': [0.0, 100.0], 'y': [0.0, 100.0]}
    def walk(el):
        for s in el.get('elements', []):
            if s.get('type') == 'shape' and s.get('mask_mode') == 'alpha':
                sp = opaque_span(s)
                if sp: r[sp[0]] = [max(r[sp[0]][0], sp[1]), min(r[sp[0]][1], sp[2])]
            if s.get('type') == 'composition': walk(s)
    walk(e)
    return [r['x'][0], r['y'][0], r['x'][1], r['y'][1]]


def orig_span(e, a, b):
    """The kept «orig …» layer e cut to [a, b) s (absolute times inside it): a copy that starts at a, shows the same source
    frames e showed then (each video's trim_start moves by a - e.time) and ends at b. The Build makes every child start
    with its layer and run as long; for any other layout (a child that starts later or runs shorter, a sped-up video)
    the answer is None and the caller does without the layer."""
    t, dur = float(e['time']), float(e['duration'])
    o, d = round(a - t, 3), round(b - a, 3)
    if not (0 <= o and d > 0 and a + d <= t + dur + 1e-6): return None
    def walk(el):
        out = dict(el)
        if el is not e:
            if float(el.get('time') or 0) != 0: return None
            if 'duration' in el:
                if abs(float(el['duration']) - dur) > 0.002: return None
                out['duration'] = d
        if el.get('type') == 'video':
            if el.get('speed') not in (None, 1, '100%'): return None
            out['trim_start'] = round(float(el.get('trim_start') or 0) + o, 3)
            if 'trim_duration' in el: out['trim_duration'] = d
        if 'elements' in el:
            kids = [walk(c) for c in el['elements']]
            if None in kids: return None
            out['elements'] = kids
        return out
    r = walk(e)
    if r is not None: r.update(time=round(a, 3), duration=d)
    return r
