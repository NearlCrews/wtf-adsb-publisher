def aircraft(**kwargs):
    # N1722M is the US registration the N-number algorithm assigns to a12345.
    return {
        "hex": "a12345",
        "r": "N1722M",
        "t": "GLF6",
        "seen": 0,
        "seen_pos": 0,
        "lat": 42.0,
        "lon": -84.0,
        "gs": 600,
        "alt_baro": 41000,
        "messages": 100,
        "type": "adsb_icao",
        **kwargs,
    }
