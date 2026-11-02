"""Readable publishers for the official hosts the site cites.

Weekly and type research accept sources only from these domains, and story facts
name the publisher of a cited type certificate data sheet with them. A leaf
module, so weekly_research and story_facts share one table without importing
each other.
"""

# Official manufacturer, operator, museum, and safety-agency sites, reviewed by hand.
PUBLISHERS = {
    "af.mil": "U.S. Air Force",
    "navy.mil": "U.S. Navy",
    "marines.mil": "U.S. Marine Corps",
    "army.mil": "U.S. Army",
    "uscg.mil": "U.S. Coast Guard",
    "ng.mil": "National Guard",
    "defense.gov": "U.S. Department of Defense",
    "faa.gov": "Federal Aviation Administration",
    "easa.europa.eu": "European Union Aviation Safety Agency",
    "nasa.gov": "NASA",
    "noaa.gov": "National Oceanic and Atmospheric Administration",
    "si.edu": "Smithsonian Institution",
    "boeing.com": "Boeing",
    "airbus.com": "Airbus",
    "lockheedmartin.com": "Lockheed Martin",
    "northropgrumman.com": "Northrop Grumman",
    "bellflight.com": "Bell",
    "sikorsky.com": "Sikorsky",
    "cessna.com": "Cessna",
    "textronaviation.com": "Textron Aviation",
    "txtav.com": "Textron Aviation",
    "gulfstream.com": "Gulfstream Aerospace",
    "bombardier.com": "Bombardier",
    "dassault-aviation.com": "Dassault Aviation",
    "dassaultfalcon.com": "Dassault Falcon Jet",
    "embraer.com": "Embraer",
    "leonardo.com": "Leonardo",
    "pilatus-aircraft.com": "Pilatus Aircraft",
    "dehavilland.com": "De Havilland Aircraft of Canada",
    "atr-aircraft.com": "ATR",
    "daher.com": "Daher",
    "cirrusaircraft.com": "Cirrus Aircraft",
    "hondajet.com": "Honda Aircraft Company",
    "robinsonheli.com": "Robinson Helicopter Company",
    "saab.com": "Saab",
    "ga-asi.com": "General Atomics Aeronautical Systems",
    "antonov.com": "Antonov",
    "diamondaircraft.com": "Diamond Aircraft",
    "piper.com": "Piper Aircraft",
    "goodyearblimp.com": "Goodyear",
    "canada.ca": "Government of Canada",
    "raf.mod.uk": "Royal Air Force",
    "royalnavy.mod.uk": "Royal Navy",
    "airforce.gov.au": "Royal Australian Air Force",
    "emfa.pt": "Portuguese Air Force",
    "icao.int": "International Civil Aviation Organization",
    "eaa.org": "Experimental Aircraft Association",
    "museumofflight.org": "The Museum of Flight",
    "nationalmuseum.af.mil": "National Museum of the U.S. Air Force",
    "rafmuseum.org.uk": "Royal Air Force Museum",
    "iwm.org.uk": "Imperial War Museums",
    "ntsb.gov": "National Transportation Safety Board",
}
# Longest first, so a host under a listed subdomain takes that entry over its parent's.
BY_LENGTH = sorted(PUBLISHERS, key=len, reverse=True)


def domain(host):
    """The listed domain a host is or sits under, or None."""
    host = (host or "").lower()
    return next((listed for listed in BY_LENGTH if host == listed or host.endswith("." + listed)), None)


def publisher(host):
    """The readable publisher of a host under a listed domain, or None."""
    listed = domain(host)
    return None if listed is None else PUBLISHERS[listed]
