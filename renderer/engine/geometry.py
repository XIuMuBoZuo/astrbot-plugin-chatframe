"""Quad geometry and inverse perspective mapping, independent of the interface."""
from __future__ import annotations
import math

Point = tuple[float, float]

def rectangle(x: float, y: float, width: float, height: float) -> list[Point]:
    return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]

def center(quad) -> Point:
    return sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4

def valid_quad(quad, min_area: float = 16) -> bool:
    if len(quad) != 4 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in quad):
        return False
    crosses = []
    for i in range(4):
        a, b, c = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        crosses.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
    area = abs(sum(quad[i][0]*quad[(i+1)%4][1]-quad[(i+1)%4][0]*quad[i][1] for i in range(4))) / 2
    return all(c > 1e-5 for c in crosses) and area >= min_area

def contains(quad, point) -> bool:
    x, y = point
    return all((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]) >= -1e-6
               for a, b in zip(quad, quad[1:]+quad[:1]))

def translate(quad, dx, dy):
    return [(x+dx, y+dy) for x, y in quad]

def scale(quad, factor, origin=None):
    cx, cy = origin or center(quad)
    return [(cx+(x-cx)*factor, cy+(y-cy)*factor) for x, y in quad]

def rotate(quad, degrees, origin=None):
    cx, cy = origin or center(quad)
    a = math.radians(degrees); co, si = math.cos(a), math.sin(a)
    return [(cx+(x-cx)*co-(y-cy)*si, cy+(x-cx)*si+(y-cy)*co) for x, y in quad]

def angle(quad):
    return math.degrees(math.atan2(quad[1][1]-quad[0][1], quad[1][0]-quad[0][0]))

def bounds(quad):
    return min(p[0] for p in quad), min(p[1] for p in quad), max(p[0] for p in quad), max(p[1] for p in quad)

def intersects(first,second):
    """Positive-area intersection of two convex quads, using separating axes."""
    for quad in (first,second):
        for a,b in zip(quad,quad[1:]+quad[:1]):
            nx,ny=a[1]-b[1],b[0]-a[0]
            p=[x*nx+y*ny for x,y in first]; q=[x*nx+y*ny for x,y in second]
            if max(p)<=min(q) or max(q)<=min(p): return False
    return True

def _solve(matrix, values):
    rows = [list(map(float, row))+[float(v)] for row, v in zip(matrix, values)]
    n = len(rows)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-10:
            raise ValueError('四角位置过于接近，无法进行透视变换')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        div = rows[col][col]
        rows[col] = [v/div for v in rows[col]]
        for r in range(n):
            if r == col: continue
            mul = rows[r][col]
            rows[r] = [a-mul*b for a, b in zip(rows[r], rows[col])]
    return tuple(row[-1] for row in rows)

def perspective_coefficients(destination, width, height):
    """Pillow coefficients mapping output pixels back into the source rectangle."""
    if not valid_quad(destination, min_area=1e-8):
        raise ValueError('四角必须组成不交叉的凸四边形')
    matrix, values = [], []
    for (x, y), (u, v) in zip(destination, rectangle(0, 0, width, height)):
        matrix.append([x, y, 1, 0, 0, 0, -u*x, -u*y]); values.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v*x, -v*y]); values.append(v)
    return _solve(matrix, values)
