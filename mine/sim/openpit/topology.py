"""Pit geometry: an elliptical open pit with a spiral ramp, two working benches,
a primary crusher and a waste dump on the surface. Coordinates are metres
(x east, y north, z elevation relative to the pit rim)."""

import math
from dataclasses import dataclass

from .params import ORE, WASTE, Material, Site

PIT_A_M = 900.0
PIT_B_M = 600.0
WALL_SLOPE = 1.0
UPPER_BENCH_Z = -45.0
BENCH_SPUR_M = 300.0
# When a face reaches the end of its cut the shovel walks back and starts the next one.
CUT_LENGTH_M = {"face-upper": 500.0, "face-lower": 250.0}

Point = tuple[float, float, float]


def _lerp(a: Point, b: Point, f: float) -> Point:
    return tuple(round(a[k] + (b[k] - a[k]) * f, 2) for k in range(3))


def _bays(a: Point, b: Point, at: Point, n: int, gap: float = 24.0, off: float = -50.0) -> list[Point]:
    """A row of parking bays alongside the road a-b, centred on `at`."""
    length = math.dist(a[:2], b[:2])
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    return [(round(at[0] + ux * (i - (n - 1) / 2) * gap - uy * off, 2),
             round(at[1] + uy * (i - (n - 1) / 2) * gap + ux * off, 2), 0.0) for i in range(n)]


@dataclass(frozen=True)
class Route:
    name: str
    points: tuple[Point, ...]

    def segments(self):
        for a, b in zip(self.points, self.points[1:]):
            length = math.dist(a[:2], b[:2])
            if length > 0:
                yield length, (b[2] - a[2]) / length, a

    @property
    def length_m(self) -> float:
        return sum(length for length, _, _ in self.segments())

    def reversed(self) -> "Route":
        return Route(self.name + "-back", tuple(reversed(self.points)))


@dataclass(frozen=True)
class Face:
    name: str
    shovel: str
    material: Material
    destination: str
    position: Point


def _ellipse(theta: float, z: float) -> tuple[float, float]:
    shrink = -z * WALL_SLOPE
    return (PIT_A_M - shrink) * math.cos(theta), (PIT_B_M - shrink) * math.sin(theta)


def _ramp(grade: float, z_bottom: float, theta0: float = math.pi / 3, step: float = 0.02) -> list[Point]:
    """Spiral down the pit wall at a constant grade until z_bottom."""
    theta, z = theta0, 0.0
    x, y = _ellipse(theta, z)
    points = [(x, y, z)]
    while z > z_bottom:
        theta -= step
        nz = z
        for _ in range(5):
            nx, ny = _ellipse(theta, nz)
            nz = max(z_bottom, z - math.dist((x, y), (nx, ny)) * grade)
        x, y, z = *_ellipse(theta, nz), nz
        points.append((x, y, z))
    return points


def _towards_centre(p: Point, distance: float) -> Point:
    x, y, z = p
    norm = math.hypot(x, y)
    return x - x / norm * distance, y - y / norm * distance, z


def _along_bench(junction: Point, distance: float, inset: float) -> Point:
    """A point on the bench ring `distance` metres from the ramp junction,
    `inset` metres inside the ring (the inner half of the bench)."""
    x, y, z = junction
    shrink = -z * WALL_SLOPE
    a, b = PIT_A_M - shrink, PIT_B_M - shrink
    theta = math.atan2(y / b, x / a)
    run, px, py = 0.0, x, y
    while run < distance:
        theta += 0.005
        nx, ny = a * math.cos(theta), b * math.sin(theta)
        run += math.dist((px, py), (nx, ny))
        px, py = nx, ny
    return _towards_centre((px, py, z), inset)


def _at_depth(ramp: list[Point], z: float) -> int:
    return min(range(len(ramp)), key=lambda i: abs(ramp[i][2] - z))


class Pit:
    def __init__(self, site: Site):
        self.lower_z = site.lower_bench_z
        ramp = _ramp(site.ramp_grade, self.lower_z)
        self.ramp = [(round(x, 2), round(y, 2), round(z, 3)) for x, y, z in ramp]
        exit_ = self.ramp[0]
        upper_idx = _at_depth(self.ramp, UPPER_BENCH_Z)
        self.upper_junction = self.ramp[upper_idx]
        self.lower_junction = self.ramp[-1]

        self.crusher: Point = (exit_[0] + 1150.0, exit_[1] + 400.0, 0.0)
        self.waste_dump: Point = (exit_[0] - 600.0, exit_[1] + 450.0, 0.0)
        self.reserve_dump: Point = (exit_[0] - 1250.0, exit_[1] + 800.0, 0.0)
        self.workshop: Point = (self.crusher[0] - 250.0, self.crusher[1] - 220.0, 0.0)
        self.stockpile: Point = (self.crusher[0] + 260.0, self.crusher[1] + 80.0, 0.0)
        self.plant: Point = (self.crusher[0] + 560.0, self.crusher[1] + 20.0, 0.0)
        # Park-up beside the service road to the workshop: trucks start and end
        # the shift here, each in its own bay.
        junction_plant = (exit_[0] + 400.0, exit_[1] + 300.0, 0.0)
        self.parking: Point = _lerp(junction_plant, self.workshop, 0.7)
        self.fuel: Point = _lerp(junction_plant, self.workshop, 0.35)
        self.fuel_station: Point = _bays(junction_plant, self.workshop, self.fuel, 1, off=-45.0)[0]
        self.bays = _bays(junction_plant, self.workshop, self.parking, site.trucks)
        self.bay_exits = _bays(junction_plant, self.workshop, self.parking, site.trucks, off=0.0)
        self.points = {"crusher": self.crusher, "dump": self.waste_dump, "dump-2": self.reserve_dump,
                       "workshop": self.workshop, "parking": self.parking, "fuel": self.fuel}
        self.faces = {
            "face-upper": Face("face-upper", "shovel-01", WASTE, "dump", self.face_position("face-upper", 0.0)),
            "face-lower": Face("face-lower", "shovel-02", ORE, "crusher", self.face_position("face-lower", 0.0)),
        }
        junction_dump = (exit_[0] - 150.0, exit_[1] + 300.0, 0.0)
        self.surface = {
            "crusher": [exit_, junction_plant, self.crusher],
            "dump": [exit_, junction_dump, self.waste_dump],
            "dump-2": [exit_, (exit_[0] + 100.0, exit_[1] + 700.0, 0.0),
                       (exit_[0] - 700.0, exit_[1] + 900.0, 0.0), self.reserve_dump],
            "workshop": [exit_, junction_plant, self.fuel, self.parking, self.workshop],
            "parking": [exit_, junction_plant, self.fuel, self.parking],
            "fuel": [exit_, junction_plant, self.fuel],
        }
        self.roads = {
            "road-ramp": self.ramp,
            "road-crusher": self.surface["crusher"],
            "road-dump": self.surface["dump"],
            "road-dump-2": self.surface["dump-2"],
            "road-service": [junction_plant, self.fuel, self.parking, self.workshop],
        }
        self._down = {}
        for face in self.faces.values():
            junction_idx = upper_idx if face.name == "face-upper" else len(self.ramp) - 1
            self._down[face.name] = [face.position] + list(reversed(self.ramp[: junction_idx + 1]))
        self.haul = {face: self.path(face, f.destination) for face, f in self.faces.items()}

    def face_position(self, face: str, advance_m: float) -> Point:
        """Where the shovel digs after the face has advanced `advance_m` metres:
        along the bench away from the ramp (upper) or across the floor (lower)."""
        if face == "face-upper":
            return _along_bench(self.upper_junction, BENCH_SPUR_M + advance_m % CUT_LENGTH_M[face], 40.0)
        return _towards_centre(self.lower_junction, BENCH_SPUR_M + advance_m % CUT_LENGTH_M[face])

    def advance(self, face: str, advance_m: float) -> None:
        """Move the face; later trips start and end at the new position."""
        f = self.faces[face]
        pos = self.face_position(face, advance_m)
        self.faces[face] = Face(f.name, f.shovel, f.material, f.destination, pos)
        self._down[face][0] = pos

    def path(self, face: str, dest: str) -> Route:
        """Loaded route from a face to a dump point over the ramp and the surface road."""
        return Route(f"{face}->{dest}", tuple(self._down[face] + self.surface[dest][1:]))

    def between(self, origin: str, face: str) -> Route:
        """Empty trip from one face to another inside the pit, turning where
        their ramps meet."""
        da, db = self._down[origin], self._down[face]
        k = 1
        while k < min(len(da), len(db)) and da[-k - 1] == db[-k - 1]:
            k += 1
        return Route(f"{origin}~>{face}", tuple(da[:len(da) - k + 1] + list(reversed(db[:len(db) - k]))))

    def loaded_route(self, face: str, dest: str | None = None) -> Route:
        return self.path(face, dest or self.faces[face].destination)

    def transfer(self, origin: str, dest: str) -> Route:
        """Surface trip between two surface points, turning at the last point
        their roads share (the pit exit, a junction, or a point on the service road)."""
        a, b = self.surface[origin], self.surface[dest]
        k = 1
        while k < min(len(a), len(b)) and a[k] == b[k]:
            k += 1
        pts = list(reversed(a[k - 1:])) + b[k:]
        return Route(f"{origin}=>{dest}", tuple(pts) if len(pts) > 1 else (a[-1], b[-1]))

    def empty_route(self, face: str, origin: str) -> Route:
        """Route from a dump point (crusher, dump or reserve dump) back to a face."""
        return self.path(face, origin).reversed()

    def describe(self) -> dict:
        return {
            "pit": {"a_m": PIT_A_M, "b_m": PIT_B_M, "wall_slope": WALL_SLOPE,
                    "benches_z": [UPPER_BENCH_Z, self.lower_z]},
            "ramp": self.ramp,
            "crusher": self.crusher,
            "waste_dump": self.waste_dump,
            "reserve_dump": self.reserve_dump,
            "workshop": self.workshop,
            "parking": self.parking,
            "fuel": self.fuel,
            "fuel_station": self.fuel_station,
            "bays": self.bays,
            "stockpile": self.stockpile,
            "plant": self.plant,
            "faces": {n: {"shovel": f.shovel, "material": f.material.name,
                          "destination": f.destination, "position": f.position,
                          "cut": [self.face_position(n, s) for s in range(0, int(CUT_LENGTH_M[n]), 20)]}
                      for n, f in self.faces.items()},
            "routes": {r.name: r.points for r in [*self.haul.values(), self.path("face-upper", "dump-2")]},
            "roads": self.roads,
        }
