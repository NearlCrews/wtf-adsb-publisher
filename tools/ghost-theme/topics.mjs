// Site copy for the topic landings: search-style headings, meta descriptions,
// evergreen intros with sources, field-guide links, and related topics. Keyed by
// the topic slugs in src/wtf_adsb_publisher/data/topics.json. Written and reviewed as
// public prose; detection thresholds quoted here must follow the detectors.

import { SITE_DESCRIPTION } from "./site-settings.mjs";

const AIM = (section, title) => ({
  publisher: "Federal Aviation Administration",
  title: `Aeronautical Information Manual: ${title}`,
  url: `https://www.faa.gov/air_traffic/publications/atpubs/aim_html/${section}.html`,
});
const SOURCES = {
  doc8643: {
    publisher: "ICAO",
    title: "Aircraft type designators (Doc 8643)",
    url: "https://www.icao.int/operational-safety/doc-8643-aircraft-type-designators/search",
  },
  mictronics: {
    publisher: "Mictronics",
    title: "Aircraft database",
    url: "https://github.com/Mictronics/aircraft-database",
  },
  tar1090db: {
    publisher: "tar1090-db project",
    title: "tar1090-db aircraft database",
    url: "https://github.com/wiedehopf/tar1090-db",
  },
  tar1090: {
    publisher: "tar1090 project",
    title: "tar1090, including the ICAO address allocation table",
    url: "https://github.com/wiedehopf/tar1090",
  },
  planeAlert: {
    publisher: "Plane Alert DB contributors",
    title: "Plane Alert DB",
    url: "https://github.com/sdr-enthusiasts/plane-alert-db",
  },
  wikidata: {
    publisher: "Wikidata",
    title: "Wikidata (CC0 1.0)",
    url: "https://www.wikidata.org/",
  },
  odbl: {
    publisher: "Open Data Commons",
    title: "Open Database License 1.0",
    url: "https://opendatacommons.org/licenses/odbl/1-0/",
  },
  dbcl: {
    publisher: "Open Data Commons",
    title: "Database Contents License 1.0",
    url: "https://opendatacommons.org/licenses/dbcl/1-0/",
  },
  part101: {
    publisher: "eCFR",
    title:
      "14 CFR Part 101: moored balloons, kites, amateur rockets, and unmanned free balloons",
    url: "https://www.ecfr.gov/current/title-14/chapter-I/subchapter-F/part-101",
  },
  adsb: {
    publisher: "Federal Aviation Administration",
    title: "ADS-B ins and outs",
    url: "https://www.faa.gov/air_traffic/technology/equipadsb/capabilities/ins_outs",
  },
  formation: {
    publisher: "eCFR",
    title: "14 CFR 91.111: operating near other aircraft",
    url: "https://www.ecfr.gov/current/title-14/section-91.111",
  },
  mlat: {
    publisher: "FlightAware",
    title: "How multilateration works",
    url: "https://www.flightaware.com/adsb/mlat/",
  },
  patterns: {
    publisher: "Flightradar24",
    title: "Interesting patterns on Flightradar24",
    url: "https://www.flightradar24.com/blog/aviation-explainer-series/interesting-patterns-on-flightradar24/",
  },
  lidarSpec: {
    publisher: "U.S. Geological Survey",
    title:
      "Lidar base specification, version 1.3 (Techniques and Methods 11-B4)",
    url: "https://pubs.usgs.gov/publication/tm11B4",
  },
  magneticSurvey: {
    publisher: "U.S. Geological Survey",
    title:
      "Airborne magnetic survey, Iron Mountain-Chatham region, central Upper Peninsula, Michigan, 2018",
    url: "https://doi.org/10.5066/P91EF3CI",
  },
  trafficPattern: {
    publisher: "Federal Aviation Administration",
    title: "Airplane Flying Handbook: airport traffic patterns",
    url: "https://www.faa.gov/sites/faa.gov/files/regulations_policies/handbooks_manuals/aviation/airplane_handbook/09_afh_ch8.pdf",
  },
  helicopter: {
    publisher: "Federal Aviation Administration",
    title: "Helicopter Flying Handbook",
    url: "https://www.faa.gov/regulations_policies/handbooks_manuals/aviation/helicopter_flying_handbook",
  },
  glider: {
    publisher: "Federal Aviation Administration",
    title: "Glider Flying Handbook",
    url: "https://www.faa.gov/regulations_policies/handbooks_manuals/aviation/glider_handbook",
  },
  phak: {
    publisher: "Federal Aviation Administration",
    title: "Pilot's Handbook of Aeronautical Knowledge",
    url: "https://www.faa.gov/regulations_policies/handbooks_manuals/aviation/phak",
  },
  readsb: {
    publisher: "readsb project",
    title: "readsb JSON field reference",
    url: "https://github.com/wiedehopf/readsb/blob/dev/README-json.md",
  },
  errors: {
    publisher: "Flightradar24",
    title: "Common errors on Flightradar24",
    url: "https://www.flightradar24.com/blog/inside-flightradar24/common-errors-on-flightradar24/",
  },
  nbcap: {
    publisher: "Federal Aviation Administration",
    title: "Order JO 7110.66H: National Beacon Code Allocation Plan",
    url: "https://www.faa.gov/documentLibrary/media/Order/2024-04-04_Order_JO_7110.66H_National_Beacon_Code_Allocation_Plan_(NBCAP)_FINAL.pdf",
  },
  aimTransponder: AIM("chap4_section_1", "services available to pilots"),
  aimEmergency: AIM(
    "chap6_section_2",
    "emergency services available to pilots",
  ),
  aimHolding: AIM("chap5_section_3", "en route procedures, including holding"),
  aimTcas: AIM(
    "chap4_section_4",
    "ATC clearances and aircraft separation, including TCAS",
  ),
  aimGps: AIM("chap1_section_1", "navigation aids, including GPS"),
  tcds: {
    publisher: "Federal Aviation Administration",
    title: "Type certificate data sheets",
    url: "https://drs.faa.gov/browse/TCDSMODEL/doctypeDetails",
  },
  openMeteo: {
    publisher: "Open-Meteo",
    title: "Weather data by Open-Meteo.com (CC BY 4.0)",
    url: "https://open-meteo.com/",
  },
};

export const topicCopy = {
  "notable-aircraft": {
    title: "Notable aircraft: rare types and military jets",
    heading: "Notable aircraft: rare types and military jets in the journal",
    description:
      "Large freighters, military transports, and other aircraft types that seldom appear in these records, with the data behind each match.",
    intro: [
      "Most aircraft in these records are airliners, business jets, and small private planes. This topic collects the others: large airliners and freighters such as the Airbus A380 and the Boeing 747 Dreamlifter, military transports and tankers, government jets, and types the records rarely contain.",
      "Every ADS-B transmitter broadcasts a 24-bit ICAO address. The site looks that address up in an aircraft database to find the registration, the ICAO type designator, and a description, then checks the type against a curated list of notable types and variants. A notable type gets a story at most once every 14 days, whichever airframe flies it. A type recorded on 24 or more of the last 60 days of coverage counts as routine and gets at most one identity story every 30 days, which leaves room for rarer visitors.",
      "A database match describes the aircraft that holds the address now. It cannot prove that the same airframe flew on the day of the story, and it says nothing about who was aboard, where the flight was going, or why. Military aircraft are usually listed by serial number instead of a civil registration, so some of these stories identify the aircraft that way. The field guide explains how a type match works and which parts of a story come from the database.",
    ],
    sources: [SOURCES.doc8643, SOURCES.mictronics, SOURCES.tar1090db],
    guide: ["type-match", "ads-b"],
    related: ["first-sightings", "vintage-aircraft", "watchlisted-aircraft"],
  },
  "first-sightings": {
    title: "First sightings of aircraft types and countries",
    heading:
      "First sightings: aircraft types and countries new to these records",
    description:
      "The first time an aircraft type, or an address block assigned to a country, appears in these records, with the dates behind the claim.",
    intro: [
      "A first sighting is the first time something appears in this site's own records: an aircraft type designator never seen before, or an aircraft whose ICAO address comes from a block assigned to a country that has not shown up here. The claim is local and dated. It says the records had not contained the type or the country before that day, and how often the type flies elsewhere is a question these records cannot answer.",
      "The check waits for at least 90 days with an hour or more of recorded traffic before it trusts a first. Group designators such as ZZZZ, used when a type has no code of its own, never count, and neither do addresses that begin with a tilde, which mark tracks without a real ICAO address. An address that appeared under a different type on more than two earlier days points to a database change, so it does not count as a new type. Country firsts start only after a 30-day warm-up.",
      "A type can pass overhead during a gap in coverage and leave no record, so a first sighting dates the first record, which may come after the first visit. The country comes from the ICAO address allocation table. It tells you which state assigned the address and nothing about who operates the aircraft today or where it is based.",
    ],
    sources: [SOURCES.tar1090, SOURCES.doc8643],
    guide: ["type-match", "ads-b"],
    related: ["notable-aircraft", "vintage-aircraft"],
  },
  "vintage-aircraft": {
    title: "Vintage aircraft: airframes 80 years and older",
    heading: "Vintage aircraft: decades-old airframes still flying",
    description:
      "Douglas DC-3s, wartime trainers, and other aircraft at least 80 years old by their database records, recorded in flight.",
    intro: [
      "Some aircraft in these records were built during or before the Second World War. Douglas DC-3s, wartime trainers such as the North American T-6 Texan, and biplanes like the Waco UPF-7 still fly, many of them kept airworthy by museums, collectors, and restoration groups, and their transponders show up next to the airliners overhead.",
      "The site reads the year of manufacture from the aircraft database. An airframe must be at least 80 years old on the day it was observed, and the bar rises for types that appear often: a type recorded on one or two of the last 60 days of coverage needs 83 years, one recorded on three to six days needs 85, and any other type needs 87. A year earlier than the earliest plausible year for the type is treated as a database error and ignored. The same airframes return to the same fields, so an airframe waits 90 days after a story before its next one, and older airframes and rarer types rank first.",
      'A database year can be wrong. A restored aircraft can carry the identity of an older airframe, a registration can move from one aircraft to another, and some entries are simply mistaken, so stories give an age "by its database record". The photos on these pages show the type, and they are not pictures of the aircraft in the story unless the caption says so.',
    ],
    sources: [SOURCES.tar1090db, SOURCES.mictronics],
    guide: ["type-match"],
    related: ["notable-aircraft", "first-sightings"],
  },
  "watchlisted-aircraft": {
    title: "Watchlisted aircraft from Plane Alert DB",
    heading: "Watchlisted aircraft: Plane Alert DB entries in the journal",
    description:
      "Aircraft listed in Plane Alert DB, a community list of aircraft that spotters follow, with the list category and the limits of what an entry shows.",
    intro: [
      "Plane Alert DB is a community-maintained list of aircraft that spotters like to follow, including government and military aircraft, historic airframes, and distinctive liveries. Each entry has a category. When an aircraft from the list appears in these records, the site can publish a story that names the category and credits the list.",
      "Matching uses the aircraft's broadcast ICAO address against a pinned copy of the list, so every story can be traced to the version that produced it, and the registration in the aircraft database must match the list entry's. When the two registrations differ, or the database and the address disagree about the aircraft's identity, the site withholds the match. The page shows the neutral category, the list's attribution, and its licenses. Some entries name a person or a company. A story can repeat that name as the list's label, credited to Plane Alert DB, and the name is no evidence of who owns the aircraft, who flew it, or who was aboard.",
      "A list entry records why someone found an aircraft interesting. It does not show who was aboard, what the flight was for, or where it went, and the site does not guess. A listed business jet can carry its owner, a charter customer, a maintenance crew, or nobody but the pilots on a positioning flight. The aircraft database and the list can also be out of date, so each story says which source supplied which detail and links to both.",
    ],
    sources: [SOURCES.planeAlert, SOURCES.odbl, SOURCES.dbcl, SOURCES.wikidata],
    guide: ["type-match", "ads-b"],
    related: ["notable-aircraft"],
  },
  "high-altitude-balloons": {
    title: "High-altitude balloons above 50,000 ft",
    heading: "High-altitude balloons: slow drifts far above airliner traffic",
    description:
      "Balloons broadcasting ADS-B from 50,000 ft and higher while moving slowly over the ground, and how the site tells them apart from data glitches.",
    intro: [
      "Research and commercial balloons can climb above 60,000 ft and stay aloft for hours or days. Many carry ADS-B transmitters, so they appear in flight-tracking data with an altitude far above the airliners and a ground speed closer to a car's than a jet's. Their tracks wander with the wind instead of following airways.",
      "The site looks for an aircraft that stays at 50,000 ft or higher for at least ten minutes, with a median ground speed of 80 knots or less and an altitude that changes slowly. It then wants corroboration: a geometric altitude that agrees with the pressure altitude, a lighter-than-air emitter category, a balloon type code, a callsign with a balloon prefix, or a flight that lasts half an hour or more.",
      "Balloon data has its own glitches. One report can show a ground speed above 1,000 knots between neighbors near 20, and the story labels such a spike as a data error. The data does not say who launched a balloon, what it carried, or where it came down. Unmanned free balloons fly under their own FAA rules, described in the regulation listed below.",
    ],
    sources: [SOURCES.part101, SOURCES.adsb],
    guide: ["geometric-altitude", "ground-speed"],
    related: ["speed-and-altitude", "ads-b-glitches"],
  },
  "formation-flights": {
    title: "Formation flights: aircraft flying together",
    heading: "Formation flights: aircraft flying together in the data",
    description:
      "Aircraft holding close, matching tracks at altitude: formation-like pairs found in broadcast positions, with their spacing and duration.",
    intro: [
      "Military aircraft, airshow teams, and photo flights sometimes fly in formation, a few hundred feet or less apart and matching speed and heading. When both aircraft broadcast their own ADS-B positions, the pair shows up in the data as two tracks that move together.",
      "The site publishes a pair only when both aircraft broadcast their own ADS-B positions. Positions estimated by multilateration are too uncertain at this spacing, and a pair that relies on positions relayed by ground stations is held for review. The two must stay within 0.5 nautical miles and 400 ft of each other, with tracks within 20 degrees and ground speeds within 25 knots, for at least two minutes. They must be at 3,000 ft or higher, or at 1,500 ft and well away from a runway, so that traffic around an airport does not count. A tanker type in the pair, or a glider climbing behind a towplane, adds a tag that says what the shape looks like.",
      "Two aircraft close together on a map are not always flying in formation. One transponder can appear under two addresses, and parallel approaches can look similar for a minute or two. The data cannot show whether the crews arranged to fly together, and a tanker in the pair does not prove that any fuel was transferred. Federal rules allow formation flight only by arrangement with the pilot in command of each aircraft.",
    ],
    sources: [SOURCES.formation, SOURCES.mlat],
    guide: ["ads-b", "mlat"],
    related: ["flight-shapes", "holding-patterns"],
  },
  "survey-flights": {
    title: "Survey flights: aircraft flying parallel lines",
    heading: "Survey flights: aircraft flying parallel lines",
    description:
      "Aircraft flying long, evenly spaced parallel lines, the pattern used for aerial photography, mapping, and other survey work.",
    intro: [
      "Aerial photography, mapping, and geophysical survey flights often cover an area in long, straight, evenly spaced lines, turning at each end to fly back alongside the previous line. On a flight-tracking map the result looks like a mowed lawn and is one of the easiest flight patterns to recognize. Lidar mapping flights overlap the strips they scan, and the USGS specification treats an unscanned gap between strips as grounds for rejecting a project. A 2018 USGS magnetic survey of Michigan's central Upper Peninsula flew lines about 500 ft apart and 260 ft above the ground, closer than the spacing this site looks for.",
      "The site looks for at least six alternating straight legs, each at least 3 nautical miles long and within 10 degrees of a common axis, flown over at least 30 minutes. Successive lines must step consistently to one side by 0.2 to 2 nautical miles, and the altitude must stay within a 300 ft band. Shorter sets of back-and-forth passes are recorded as parallel passes, which add context to other stories and do not get stories of their own.",
      "The shape matches survey work, but training flights, searches, and patrols can fly similar lines, and the data does not say which one it was. The track also shows only the part of the flight these records captured, so a survey can extend well beyond the lines drawn on the map, and a gap in the data can make two lines look like one.",
    ],
    sources: [SOURCES.lidarSpec, SOURCES.magneticSurvey, SOURCES.patterns],
    guide: ["ads-b"],
    related: ["flight-shapes", "holding-patterns"],
  },
  "holding-patterns": {
    title: "Holding stacks: aircraft holding together",
    heading: "Holding patterns: aircraft stacked in racetrack holds",
    description:
      "Three or more aircraft holding at the same time, stacked at different altitudes while they wait for a clearance.",
    intro: [
      "When an airport or route is busy, or weather closes a runway, air traffic control can tell aircraft to hold: to fly a racetrack pattern over a fixed point until they are cleared onward. A standard hold uses right turns, and its inbound leg takes about a minute at or below 14,000 ft and a minute and a half above that.",
      "The site finds a hold when a track circles for at least ten minutes and closes three or more loops. A single aircraft's hold only adds context to another story about it. When three or more aircraft circle at the same time within 10 nautical miles of one another, at least 500 ft apart vertically, and at 5,000 ft or higher, the site treats them as a stack and tells one story about the group, listing the other aircraft and their altitudes.",
      "A racetrack shape does not show why an aircraft held. The reason can be traffic, weather, a closed runway, or a training exercise, and controllers' instructions are not part of this data. Loops close to an airport can also be practice circuits, which the site labels separately so they do not crowd out the rarer holds. Gaps in reception can hide part of a hold, so the number of loops describes the saved positions.",
    ],
    sources: [SOURCES.aimHolding],
    guide: ["ads-b"],
    related: ["flight-shapes", "formation-flights"],
  },
  hovering: {
    title: "Hovering helicopters in ADS-B data",
    heading: "Hovering: helicopters holding still in the air",
    description:
      "Helicopters that stayed nearly stationary in the air, checked against movement before and after so parked aircraft are not counted.",
    intro: [
      "Helicopters can stay in one place in the air, which almost no other aircraft can do. Medical, police, news, and utility crews hover to work on the ground below, and pilots practice it as a basic skill. In the data a hover shows up as a helicopter whose ground speed falls to almost nothing while its position stays put.",
      "The site looks for a rotorcraft reporting a ground speed of 8 knots or less over a short stretch while its altitude changes by less than 200 ft. A helicopter sitting on a pad with its transponder on can report the same thing, so the hover must have forward flight before and after it in the same stretch of track, or a clear climb or descent. Without that evidence the story is held back, and so is a hover longer than ten minutes, because a long stop on a pad and a real hover can look alike.",
      "A hover marks where a helicopter stopped. The data cannot show what its crew was doing there, whether a rescue, a patrol, or a news shot, and a low hover over a field can be a landing that the records missed. Positions from multilateration are estimates with a wider margin of error, so a hover built from them is never published.",
    ],
    sources: [SOURCES.helicopter],
    guide: ["ground-speed", "mlat"],
    related: ["flight-shapes"],
  },
  soaring: {
    title: "Soaring gliders climbing in thermals",
    heading: "Soaring: gliders climbing in circles",
    description:
      "Gliders climbing in tight circles inside rising air, found from their tracks, climb rates, and speeds.",
    intro: [
      "Most gliders have no engine for sustained flight, so they climb by finding rising air. On sunny days, columns of warm air called thermals lift a glider that circles tightly inside them, often at several hundred feet per minute. The pilot then glides toward the next thermal, trading the height for distance.",
      "The site looks for verified circling by an aircraft that looks like a glider: an emitter category for gliders, the 1202 squawk code that gliders in the United States use when they are not talking to air traffic control, or a glider type code. It wants a gain of at least 500 ft at 200 to 1,000 ft per minute, with a median ground speed of 40 to 80 knots, so that a powered aircraft circling at a steady height does not count.",
      "Other aircraft circle too, so the site needs the glider evidence, and a towplane climbing with a glider is filed as a formation instead. The data records the climb. The rising air itself is inferred from the track, since nothing in the broadcast measures it, and a glider that turns in lift without gaining height leaves no story at all.",
    ],
    sources: [SOURCES.glider, SOURCES.aimTransponder],
    guide: ["squawk", "ground-speed"],
    related: ["flight-shapes", "formation-flights"],
  },
  "flight-shapes": {
    title: "Spiral descents and other flight shapes",
    heading: "Flight shapes: spiral descents and circling tracks",
    description:
      "Aircraft that circled down through 1,500 ft or more, measured from saved positions, with what a flight shape can and cannot tell you.",
    intro: [
      "Most flights go more or less straight from one place to another. Some tracks circle instead, and the circles that become stories here lose height as they turn: an aircraft spiraling down over one area. Pilots practice steep spirals in training, and skydiving aircraft often spiral down after a drop.",
      "The site measures each shape from the saved positions. It counts closed loops from the turning that adds up to a full circle and allows for wind drift. A spiral descent needs that verified circling, a loss of at least 1,500 ft, and a track that descends through most of its reports. Loops close to a runway at low altitude are routine traffic around an airport and do not count. Single circles, repeated passes, long loiters, and sharp course reversals are measured too; they add context to other stories and do not get stories of their own.",
      "A shape does not tell you why an aircraft flew it. A descending circle can be training, a jump plane coming back down, or a pilot losing height before landing, and the record cannot tell these apart. Gaps in the data can also hide part of a loop, so the counts describe the saved positions only, and the track maps show where the gaps fall.",
    ],
    sources: [SOURCES.patterns, SOURCES.trafficPattern],
    guide: ["ads-b", "mlat"],
    related: ["holding-patterns", "survey-flights", "hovering"],
  },
  "rapid-descents": {
    title: "Rapid descents of 4,500 ft per minute or more",
    heading: "Rapid descents: aircraft dropping thousands of feet a minute",
    description:
      "Aircraft that descended at 4,500 ft per minute or more, measured from their reported vertical rates and altitudes, with what a fast descent can and cannot show.",
    intro: [
      "A typical airliner descent runs at a few thousand feet per minute. A much faster descent can follow a loss of cabin pressure, when the crew takes the aircraft down to air that is safe to breathe, but steep descents are also routine for skydiving aircraft, some military jets, and some approaches. This topic collects descents of 4,500 ft per minute or more.",
      "The site needs a high vertical rate that neighboring reports confirm, measured after repeated identical reports are collapsed into one, so a single bad value cannot make a story. A descent can count as an exceptional event, beyond the daily story limit, when the reported rate holds at 7,000 ft per minute or more for at least 30 seconds and four reports, and the altitude itself falls at least 6,000 ft a minute over that time. A jump-plane type descending near a runway loses that status. So does an aircraft that made a separate descent within six hours, unless the descent began at 25,000 ft or higher with the profile of an emergency descent.",
      "The data records how fast the aircraft came down. It cannot show a pressurization problem, a medical emergency, or any other reason, so a profile that matches an emergency descent is described as a match and never as proof. Vertical rates also come in two kinds, from pressure and from satellite altitude, and a story says which one it uses.",
    ],
    sources: [SOURCES.aimEmergency, SOURCES.readsb],
    guide: ["geometric-altitude", "ads-b"],
    related: ["speed-and-altitude", "squawk-codes"],
  },
  "speed-and-altitude": {
    title: "Record ground speeds and unusual altitudes",
    heading: "Speed and altitude: record ground speeds and unusual heights",
    description:
      "Aircraft that flew unusually fast over the ground, unusually high, or past their certified limits, with tailwinds and altitude references explained.",
    intro: [
      "Ground speed is how fast an aircraft moves over the ground, so a strong tailwind can push an airliner well past its usual speed without the aircraft flying any faster through the air. In winter the jet stream can add well over 100 knots. Altitude has its own catch: aircraft report pressure altitude from their instruments and often a geometric altitude from satellite positioning, and the two can differ by hundreds of feet.",
      "The site keeps the fastest sustained ground speed for each aircraft type this season, using only reports the aircraft broadcast itself for at least a minute, and writes a story when a new one is set. A record counts once at least 20 aircraft of the type have been recorded this season, and never above 900 knots or the type's listed limit. Some aircraft also report their true airspeed; when the ground speed runs 100 to 300 knots higher, the difference is the wind's push along the track. A speed or altitude far beyond what a type can fly goes to the ADS-B glitches topic instead, because it points to a bad reading or a wrong type in the database.",
      "Some types have a published type certificate data sheet that states a maximum operating altitude and a maximum operating speed. The site compares sustained reports with those figures and writes a story when at least five reports over a minute sit 1,000 ft or more above the certified ceiling, or at an airspeed 10% or more over the limit, and a second aircraft registry lists the same type. Ground speed becomes an airspeed estimate only after a modeled wind at the aircraft's altitude is taken out, unless the aircraft reports its own true airspeed. A reading 50% or more past a limit goes to the ADS-B glitches topic as bad data.",
      "A record here covers these records only, for one season. Most reports carry no airspeed, so the wind's share of a fast ground speed is usually unknown, and where an aircraft does report its true airspeed the difference gives a speed along the track without the wind's direction. An unusual altitude is compared with a type's limits only when the story states that comparison. A reading past a certified figure does not establish a violation, a cause, or who was flying, and a short excursion or a faulty report can produce one. The field guide explains ground speed and the two kinds of altitude in more detail.",
    ],
    sources: [SOURCES.phak, SOURCES.adsb, SOURCES.tcds, SOURCES.openMeteo],
    guide: ["ground-speed", "geometric-altitude"],
    related: ["rapid-descents", "high-altitude-balloons", "ads-b-glitches"],
  },
  "squawk-codes": {
    title: "Squawk 7700, 7600, and 7500 sightings",
    heading: "Squawk 7700, 7600, and 7500: emergency codes in the journal",
    description:
      "Aircraft transmitting the general emergency, radio failure, hijack, or other special transponder codes, and why a code alone does not confirm an emergency.",
    intro: [
      "A squawk code is the four-digit number a pilot sets on the transponder. Air traffic control assigns most of them, but a few have fixed meanings: 7700 for a general emergency, 7600 for a radio communication failure, and 7500 for unlawful interference, which usually means a hijacking. In the United States, 7400 marks an unmanned aircraft that has lost its control link, and other codes are set aside for gliders, firefighting aircraft, and military operations.",
      "The site follows each status code through the aircraft's own reports and treats a change such as 7700 to 7600 as one story. A code that lasts under a minute, or only a few reports, before a normal code returns is often a pilot dialing through it on the way to another code, so it ranks lower. A code reported only while the aircraft was on the ground, or one that disagrees with the aircraft's own emergency field, is tagged in the story.",
      "A code is a report, and the code alone does not confirm an emergency. Pilots set 7700 for many reasons, from a sick passenger to a precautionary landing, and codes are sometimes set by mistake. The data cannot show what happened on board, and it shows how the flight ended only when later reports cover the landing.",
    ],
    sources: [SOURCES.aimEmergency, SOURCES.aimTransponder, SOURCES.nbcap],
    guide: ["squawk"],
    related: ["tcas-advisories", "rapid-descents"],
  },
  "tcas-advisories": {
    title: "TCAS resolution advisories in ADS-B data",
    heading: "TCAS advisories: collision-avoidance alerts in broadcast data",
    description:
      "Resolution advisories from aircraft collision-avoidance systems, decoded from transponder broadcasts, with the response and spacing the data shows.",
    intro: [
      "TCAS, the traffic alert and collision avoidance system, watches the transponders of nearby aircraft and warns the crew when one gets close. When the geometry gets tight it issues a resolution advisory, a spoken instruction such as climb, descend, or level off, which the crew is expected to follow promptly. Advisories are issued automatically and happen in routine traffic as well as in close encounters.",
      "Transponders that support it report an active advisory in their broadcasts. The site decodes those reports, needs at least two active messages within 30 seconds while the aircraft is airborne, and matches the aircraft the advisory names with its own track. It then measures how the vertical rate changed after the advisory and how close the two aircraft came. Advisories decoded from Mode S replies instead of ADS-B broadcasts are treated as experimental and held back.",
      "An advisory does not establish a near miss or a loss of separation. TCAS works from its own short-term predictions, and many advisories happen at distances that air traffic control treats as normal. The data cannot show what the crews heard or said, and the spacing figures come from broadcast positions, which have errors of their own.",
    ],
    sources: [SOURCES.aimTcas, SOURCES.readsb],
    guide: ["ads-b", "geometric-altitude"],
    related: ["squawk-codes", "rapid-descents"],
  },
  "ads-b-glitches": {
    title: "ADS-B glitches: impossible speeds and bad data",
    heading:
      "ADS-B glitches: impossible speeds, altitudes, and mismatched identities",
    description:
      "Readings that cannot be right, such as thousand-knot spikes, impossible altitudes, and identity mismatches, shown with the neighboring reports.",
    intro: [
      "Some numbers in ADS-B reports are wrong. A bit error, a faulty GPS receiver, a misconfigured transponder, or a message credited to the wrong aircraft can produce a light aircraft at 1,000 knots, an altitude no aircraft could reach, or a registration that does not match the transmitter's address. These stories collect such readings and show the reports around them.",
      "The site compares each suspect value with the aircraft's own reports just before and after it, so a single spike between ordinary neighbors stands out. A reading far beyond what the listed type can fly is filed as a mismatch with the type and never as performance, and identity checks compare the registration, the address block, and the callsign. Spikes in positions estimated by multilateration stay in review, because the receiving network computes those positions instead of the aircraft reporting them. Position jumps and reports of a space vehicle are always held for review as well.",
      "A glitch story shows that a reading was wrong or inconsistent. It usually cannot show which part of the chain failed, the aircraft's equipment, the radio link, or the decoding, and it never describes what the aircraft did. The neighboring reports are the best evidence of the true value, and each story lists them beside the suspect reading.",
    ],
    sources: [SOURCES.errors, SOURCES.readsb],
    guide: ["ads-b", "ground-speed", "mlat"],
    related: ["gps-integrity", "speed-and-altitude"],
  },
  "gps-integrity": {
    title: "GPS integrity drops in aircraft ADS-B data",
    heading: "GPS integrity drops: when aircraft stop trusting their position",
    description:
      "Groups of aircraft that lost navigation integrity in their ADS-B data at about the same time, and what those integrity fields can and cannot show.",
    intro: [
      "ADS-B reports carry two numbers about the quality of an aircraft's position: the navigation integrity category (NIC) and the navigation accuracy category for position (NACp). Both come from the aircraft's own GPS equipment. When they fall to the bottom of their scales, the aircraft is saying it can no longer vouch for the position it broadcasts.",
      "The site looks for an airborne aircraft that reported good values, NIC 6 or more and NACp 8 or more, for at least a minute, then dropped to 1 or less for at least three reports over 20 seconds. One aircraft's drop only adds context to another story about it. When three or more aircraft lose integrity within ten minutes and about 60 nautical miles of one another, the site writes one story about the group.",
      "An integrity drop can come from radio interference with GPS, a satellite or receiver fault, or the equipment on one aircraft, and the data cannot tell these apart. The stories call these events integrity drops and never call them jamming or spoofing, because nothing in the broadcast identifies a cause. Positions reported during the drop can still look normal on a map, and only the integrity fields show that the aircraft stopped vouching for them.",
    ],
    sources: [SOURCES.readsb, SOURCES.aimGps],
    guide: ["ads-b"],
    related: ["ads-b-glitches"],
  },
};

export const indexCopy = {
  // The site's description: site_description, or the theme's default.
  home: SITE_DESCRIPTION,
  archive:
    "Every published story, newest first, grouped by the UTC date of the observation.",
  topics:
    "Stories grouped by what happened: unusual aircraft, flight patterns, performance, transponder reports, and data quality.",
  aircraft:
    "Every aircraft type with a story in the journal, from A to Z. Type names come from the aircraft database and a curated list of display names.",
  operators:
    "Airlines and other operators identified from the callsign designator an aircraft broadcast, never from owner records or watchlist labels.",
};
