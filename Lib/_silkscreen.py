"""Trim straight overlay strokes against pad openings without moving contours."""
import math

UNIT_MM = 2.54e-6
CLEARANCE_MM = 0.127
ROUNDING_MARGIN_MM = 0.01
MASK_EXPANSION_MM = 0.075


def _rectangle_interval(x, y, dx, dy, hx, hy):
    lo, hi = 0.0, 1.0
    for p, d, half in ((x, dx, hx), (y, dy, hy)):
        if abs(d) < 1e-12:
            if abs(p) > half:
                return []
        else:
            a, b = sorted(((-half-p)/d, (half-p)/d))
            lo, hi = max(lo, a), min(hi, b)
    return [(lo, hi)] if lo <= hi else []


def _circle_interval(x, y, dx, dy, radius):
    a = dx*dx + dy*dy
    b = 2*(x*dx + y*dy)
    c = x*x + y*y - radius*radius
    discriminant = b*b - 4*a*c
    if discriminant < 0:
        return []
    root = math.sqrt(discriminant)
    lo, hi = max(0, (-b-root)/(2*a)), min(1, (-b+root)/(2*a))
    return [(lo, hi)] if lo <= hi else []


def trim_segments(pads, start, end, width, layer=33):
    """Return remaining segments, in mm; pads use Altium's internal units."""
    if layer not in (33, 34):
        return [(start, end)]
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return []
    intervals = []
    for pad in pads:
        if pad.layer not in (74, 1 if layer == 33 else 32):
            continue
        angle = math.radians(pad.rotation)
        co, si = math.cos(angle), math.sin(angle)
        px, py = start[0]-pad.x*UNIT_MM, start[1]-pad.y*UNIT_MM
        x, y = px*co+py*si, -px*si+py*co
        vx, vy = dx*co+dy*si, -dx*si+dy*co
        w = max(pad.width, pad.hole_size)*UNIT_MM
        h = max(pad.height, pad.hole_size)*UNIT_MM
        mask = (max(MASK_EXPANSION_MM, pad.soldermask_expansion_manual*UNIT_MM)
                if pad.soldermask_expansion_mode != 2 else 0)
        gap = CLEARANCE_MM + ROUNDING_MARGIN_MM + width/2 + mask
        if abs(x) > w/2+gap+length or abs(y) > h/2+gap+length:
            continue
        if pad.shape == 2:
            intervals += _rectangle_interval(x, y, vx, vy, w/2+gap, h/2)
            intervals += _rectangle_interval(x, y, vx, vy, w/2, h/2+gap)
            for cx in (-w/2, w/2):
                for cy in (-h/2, h/2):
                    intervals += _circle_interval(x-cx, y-cy, vx, vy, gap)
        elif pad.shape == 1:
            # Round/oval pad: a rectangle with semicircular ends.
            if h > w:
                x, y, vx, vy, w, h = y, x, vy, vx, h, w
            half, radius = (w-h)/2, h/2+gap
            intervals += _rectangle_interval(x, y, vx, vy, half, radius)
            for cx in (-half, half):
                intervals += _circle_interval(x-cx, y, vx, vy, radius)
        else:
            raise ValueError(f'Unsupported pad shape: {pad.shape}')
    if not intervals:
        return [(start, end)]
    remaining, cursor = [], 0.0
    for lo, hi in sorted(intervals):
        if lo > cursor:
            remaining.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < 1:
        remaining.append((cursor, 1.0))
    return [((start[0]+dx*a, start[1]+dy*a),
             (start[0]+dx*b, start[1]+dy*b))
            for a, b in remaining if (b-a)*length >= max(0.15, width)]
