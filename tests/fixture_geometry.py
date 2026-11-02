import math

T0 = 1791150000.0

LAT0, LON0 = 44.0, -80.0


def ll(x, y):
    return LAT0 + y / 60, LON0 + x / (60 * math.cos(math.radians(LAT0)))


class Track:
    """Kinematic path in local NM (heading 0 = north), stepped every 0.5 s."""

    def __init__(self, x=0.0, y=0.0, heading=90.0, altitude=3000.0, speed=100.0):
        self.x, self.y, self.heading, self.altitude, self.speed, self.t = x, y, heading, altitude, speed, 0.0
        self.states = [(0.0, x, y, heading, altitude, speed, 0.0)]

    def _step(self, turn, rate):
        self.heading += turn
        distance = self.speed * 0.5 / 3600
        self.x += distance * math.sin(math.radians(self.heading))
        self.y += distance * math.cos(math.radians(self.heading))
        self.altitude += rate * 0.5 / 60
        self.t += 0.5
        self.states.append((self.t, self.x, self.y, self.heading % 360, self.altitude, self.speed, rate))

    def straight(self, seconds, rate=0.0, speed=None):
        self.speed = speed or self.speed
        for _ in range(round(seconds / 0.5)):
            self._step(0, rate)
        return self

    def turn(self, degrees, per_second=3.0, rate=0.0, speed=None):
        self.speed = speed or self.speed
        steps = max(1, round(abs(degrees) / per_second / 0.5))
        for _ in range(steps):
            self._step(degrees / steps, rate)
        return self


def sample(track, period=10.0, wind=(0.0, 0.0), source="adsb_icao", gaps=(), **extra):
    """Poll like readsb: fresh positions every ``period`` seconds, rounded to 0.1 s."""
    out, t, index = [], 0.0, 0
    states = track.states
    while t <= states[-1][0]:
        while index + 1 < len(states) and states[index + 1][0] <= t:
            index += 1
        _, x, y, heading, altitude, speed, rate = states[index]
        x, y = x + wind[0] * t / 3600, y + wind[1] * t / 3600
        if not any(a <= t < b for a, b in gaps):
            vx = speed * math.sin(math.radians(heading)) + wind[0]
            vy = speed * math.cos(math.radians(heading)) + wind[1]
            lat, lon = ll(x, y)
            stamp = round(T0 + t, 1)
            out.append(
                {
                    "ts": stamp,
                    "position_at": stamp,
                    "lat": lat,
                    "lon": lon,
                    "gs": round(math.hypot(vx, vy), 1),
                    "track": round(math.degrees(math.atan2(vx, vy)) % 360, 1),
                    "alt_baro": round(altitude / 25) * 25,
                    "baro_rate": round(rate / 64) * 64,
                    "source": source,
                    "nic": 8,
                    "nac_p": 10,
                    **extra,
                }
            )
        t += period
    return out


def runway_at(x, y, heading=90, length=1.0):
    dx, dy = length / 2 * math.sin(math.radians(heading)), length / 2 * math.cos(math.radians(heading))
    a, b = ll(x - dx, y - dy), ll(x + dx, y + dy)
    return {
        "airport_ident": "SYNT",
        "airport_name": "Synthetic",
        "runway": "09/27",
        "ends": [[a[1], a[0]], [b[1], b[0]]],
    }
