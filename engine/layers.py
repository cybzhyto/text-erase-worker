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
