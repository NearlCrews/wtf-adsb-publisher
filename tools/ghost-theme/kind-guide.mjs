import { readFileSync } from "node:fs";
import Handlebars from "handlebars";
import { engineTopics } from "./paths.mjs";
import { kindTriggers } from "./topics.mjs";

// The field guide's "Kinds of story" section: one entry for each kind that can
// lead a story, grouped by topic in the order of the engine's topic table.
// Context kinds, which only add to another story, have no entry; the
// publisher's GUIDE_KINDS names the kinds that do, and a story's "How to read
// these numbers" link opens the guide at its kind's anchor. The rule that
// sets each kind off lives in topics.mjs (kindTriggers) beside the topic
// intros that quote the same thresholds; this table holds the plain account
// of each kind. Public prose: Humanizer-clean, with no purpose words and
// nothing about who was aboard.

const escape = Handlebars.escapeExpression;

// The labeled parts of every entry, in order.
export const KIND_PARTS = [
  "What it is",
  "Why it can be interesting",
  "What sets it off",
  "Common false alarms",
];

// The anchor of a kind's entry, which the publisher's limits link targets.
export const kindAnchor = (kind) => `kind-${kind.replaceAll("_", "-")}`;

const ARCHIVE_SQUAWK = (code, meaning) => ({
  name: `Squawk ${code} in saved archives`,
  what: `A ${code} code, the code for ${meaning}, found in saved reports from the past instead of live reception.`,
  why: "Replaying archives fills in codes that the live record missed, and a review can then compare them with the rest of the flight.",
  falseAlarms:
    "Archived reports can repeat an old value, so a code can look longer than it lasted. A pilot dialing through the code on the way to another leaves the same trace.",
});

export const KIND_GUIDE = {
  watch_type: {
    name: "Watched aircraft type",
    what: "An aircraft of a type the site always watches for, because few of them exist or they seldom come this way.",
    why: "These are the aircraft people look up when they see one overhead: outsize freighters, bombers, and the few airliners and warbirds left from the 1940s.",
    falseAlarms:
      "The type comes from the aircraft database, so a wrong or outdated entry makes a false match. A type that visits often stops being remarkable, which is why repeat visits wait.",
  },
  notable_variant: {
    name: "Military or government variant",
    what: "A military or government version of a type, named precisely in the aircraft database's description.",
    why: "A military version of an airliner or business jet looks ordinary on a map. The database description is what sets it apart.",
    falseAlarms:
      "Database descriptions can be wrong or out of date. The crew enters the callsign, and a prefix can be set by hand.",
  },
  military: {
    name: "Military aircraft",
    what: "An aircraft the aircraft database marks as military.",
    why: "Military aircraft often carry serial numbers instead of civil registrations, and many of their types seldom appear among the airliners.",
    falseAlarms:
      "The flag belongs to the database entry, which can stay set after an aircraft leaves service or changes hands.",
  },
  database_watch: {
    name: "Database interesting flag",
    what: "An aircraft that the aircraft database's maintainers flagged as interesting.",
    why: "The flag points to an airframe someone thought worth noting, which makes it a lead for a closer look.",
    falseAlarms:
      "The database does not say why it set the flag, and the flag can be years old.",
  },
  rare_here: {
    name: "Type sighting",
    what: "An aircraft type these records seldom contain, judged against the receiver's own last 30 days.",
    why: "A type can be common elsewhere and still rarely pass this receiver, and the rare visitors are the ones worth a look.",
    falseAlarms:
      "Rarity depends on coverage, so a gap in reception or a quiet month can make a type look rare. A wrong type in the database has the same effect.",
  },
  foreign_military: {
    name: "Foreign military aircraft",
    what: "A military aircraft whose address comes from a block assigned to a country other than the United States.",
    why: "Military aircraft from other countries are a small share of the traffic here, and they often fly types and markings seldom seen in these records.",
    falseAlarms:
      "A military flag in the database can be outdated. When the registration's country is unknown, an aircraft that moved to another registry can still carry the address from its old one.",
  },
  first_type_here: {
    name: "First sighting of a type",
    what: "The first record of an aircraft type in this site's own history.",
    why: "Each first adds to the list of what flies past this receiver, and some are types that rarely fly at all.",
    falseAlarms:
      "A database change can relabel a familiar aircraft as a new type. A type can also pass during a gap in coverage, so the first record can come after the first visit.",
  },
  first_country_here: {
    name: "First sighting of a country",
    what: "The first aircraft in these records whose ICAO address comes from a block assigned to a particular country.",
    why: "Address blocks follow the State that registered the aircraft, so a new block marks a visitor from farther afield.",
    falseAlarms:
      "The block names the State that assigned the address, which can differ from where the aircraft is based or who operates it now. A re-registered aircraft can keep its old address.",
  },
  first_operator_here: {
    name: "First sighting of an operator",
    what: "The first time an operator's callsign designator, such as DLH for Lufthansa, appears in these records.",
    why: "A new designator adds an airline to the list of operators seen here, and some of them seldom fly this way.",
    falseAlarms:
      "The crew enters the callsign, so a typo or a borrowed designator can create a false first. The designator does not confirm who operates the aircraft.",
  },
  vintage: {
    name: "Vintage aircraft",
    what: "An aircraft built eight decades or more ago that is still flying.",
    why: "Few airframes this old remain airworthy, and most of them are kept flying by museums, collectors, and restoration groups.",
    falseAlarms:
      "A database year can be wrong, and a restored aircraft can carry the identity of an older airframe.",
  },
  watchlist_match: {
    name: "Plane Alert DB entry",
    what: "An aircraft listed in Plane Alert DB, a community list of aircraft that spotters follow.",
    why: "The list collects government aircraft, historic airframes, and distinctive liveries, so a match often means something out of the ordinary is overhead.",
    falseAlarms:
      "The list and the aircraft database can both be out of date. A name on the list is a label, and it is not evidence of who owns the aircraft or who was aboard.",
  },
  stratospheric_balloon: {
    name: "High-altitude balloon",
    what: "A balloon broadcasting ADS-B from far above airliner traffic while it drifts with the wind.",
    why: "Research and commercial balloons can stay aloft for days at heights no airliner reaches.",
    falseAlarms:
      "A bad altitude report on a slow aircraft can look the same, which is why the site wants corroboration. A single speed spike in balloon data is a data error.",
  },
  formation_flight: {
    name: "Formation flight",
    what: "Two aircraft moving together, close enough and matched in speed and heading closely enough to look like a formation.",
    why: "Formation flying takes an arrangement between crews, so it is uncommon outside airshow teams, military pairs, and photo flights.",
    falseAlarms:
      "One transponder appearing under two addresses can make a false pair. Parallel approaches to an airport can look similar for a minute or two.",
  },
  survey_grid: {
    name: "Grid of straight lines",
    what: "Long, straight, evenly spaced lines flown back and forth, like a mowed lawn on a map.",
    why: "Aerial photography, mapping, and geophysical survey work fly this pattern, and it is one of the easiest shapes to recognize.",
    falseAlarms:
      "Training flights and searches can fly similar lines. Gaps in reception can merge two lines into one or hide the rest of the grid.",
  },
  prolonged_holding: {
    name: "Long hold",
    what: "One aircraft flying a racetrack hold over the same point for a long time.",
    why: "Most holds last a few minutes, so a long one stands out among the aircraft that pass straight through.",
    falseAlarms:
      "Practice circuits and repeated approaches near an airport can loop the same way, which is why holds close to a runway do not count. Gaps in reception can hide part of a hold.",
  },
  holding_stack: {
    name: "Circling group",
    what: "Several aircraft holding over the same area at once, stacked at different altitudes.",
    why: "Stacks form when an airport or route is busy or weather slows arrivals, and they show the traffic waiting its turn.",
    falseAlarms:
      "Aircraft circling near one another for unrelated reasons can look like a stack, and the data hold no controller instructions.",
  },
  racetrack_orbit: {
    name: "Racetrack orbit",
    what: "A long oval flown over and over by one aircraft, with straight legs many miles long.",
    why: "A racetrack repeats the same pair of long lines many times, a shape that stands out against traffic going from one place to another.",
    falseAlarms:
      "A survey flight or a long hold can trace a similar shape. Reception gaps can cut a leg short or join two legs together.",
  },
  hovering: {
    name: "Hover",
    what: "A helicopter staying nearly still in the air.",
    why: "Almost no other aircraft can hover, and a hover marks the exact place a helicopter stopped.",
    falseAlarms:
      "A helicopter sitting on a pad with its transponder on reports the same speeds, which is why the site wants movement before and after. A landing the records missed can look like a low hover.",
  },
  thermal_soaring: {
    name: "Thermal soaring",
    what: "A glider circling tightly as it climbs in rising air.",
    why: "Soaring is how gliders stay up, and a circling climb is the clearest sign of it in the data.",
    falseAlarms:
      "A powered aircraft circling as it climbs looks much the same, so the site needs the glider evidence. A towplane climbing with a glider is filed as a formation instead.",
  },
  descending_spiral: {
    name: "Descending spiral",
    what: "An aircraft circling down over one area, losing height with each turn.",
    why: "Spirals are a standard way to lose height over one spot: pilots practice them, and skydiving aircraft often spiral down after a drop.",
    falseAlarms:
      "A pilot losing height in circles before landing at a nearby field looks the same. Gaps in the data can hide loops.",
  },
  departure_return: {
    name: "Departure and return",
    what: "A flight that takes off and comes back to the runway it left.",
    why: "A quick return can follow a problem after takeoff, though local flights and training return to their airport every day.",
    falseAlarms:
      "Pattern work, test flights, and short local hops often come straight back. The records can also miss the landing when reception ends low.",
  },
  possible_go_around: {
    name: "Possible go-around",
    what: "An approach to a runway that stops short of landing and climbs away.",
    why: "Crews go around when an approach is unstable or the runway is not clear. It is a routine safety maneuver that seldom shows up this clearly in the data.",
    falseAlarms:
      "A practice approach or a low pass flown on purpose has the same shape. A touch-and-go whose ground reports were missed can look like one too.",
  },
  extreme_turn_rate: {
    name: "Tight turn by a large aircraft",
    what: "A turn much tighter than large aircraft usually fly.",
    why: "Airliners and other large aircraft normally turn gently, so a sustained tight turn stands out.",
    falseAlarms:
      "Position noise can inflate a turn rate worked out from positions, and the rate is not a measurement of bank angle. A wrong emitter category can make a small aircraft look large.",
  },
  aerobatic_practice: {
    name: "Aerobatic-style practice",
    what: "Repeated climbs and dives in a small piece of sky.",
    why: "This is what aerobatic practice looks like on a track map, and it is rare among the steady lines of other traffic.",
    falseAlarms:
      "Stall practice and steep turns during flight training can produce similar reversals. Bad altitude reports can add false ones.",
  },
  rapid_descent: {
    name: "Rapid descent",
    what: "An aircraft coming down far faster than a normal descent.",
    why: "Fast descents follow a loss of cabin pressure, steep approaches, and skydiving drops, and the data cannot tell which.",
    falseAlarms:
      "A run of bad altitude or rate reports can look like one, and so can a jump plane heading back down after a drop.",
  },
  vertical: {
    name: "High descent rate report",
    what: "A descent rate, reported by the aircraft itself, well beyond what most aircraft fly.",
    why: "The rate field comes straight from the aircraft, so a high value is its own report of a fast descent.",
    falseAlarms:
      "Rate fields can be noisy, and a brief spike on a steep approach can reach these numbers without a long descent.",
  },
  seasonal_speed_record: {
    name: "Seasonal ground speed record",
    what: "A new top ground speed for an aircraft type in this season's records.",
    why: "Records mostly follow the jet stream, so they track the season's strongest winds aloft.",
    falseAlarms:
      "A strong tailwind usually explains most of a record. The comparison covers only this collection's reports and says nothing about what the type can do.",
  },
  fast: {
    name: "Unusual ground speed",
    what: "An aircraft moving faster over the ground than its type usually does here.",
    why: "A big ground speed usually means a strong tailwind, and it shows how much the wind can add.",
    falseAlarms:
      "Ground speed includes the wind, so a fast number says little about airspeed. A wrong type in the database can make an ordinary speed look high.",
  },
  high: {
    name: "Unusual altitude",
    what: "An aircraft flying higher than its type usually does here.",
    why: "Few aircraft fly far above the airliners, so an unusually high one is worth a look.",
    falseAlarms:
      "Pressure altitude can read hundreds of feet away from the true height. A wrong type in the database changes what counts as usual.",
  },
  wind_boost: {
    name: "Ground speed beyond true airspeed",
    what: "An aircraft whose own airspeed report shows the wind pushing it along hard.",
    why: "The gap between ground speed and true airspeed is the wind's share along the track, so a large one points to strong winds aloft.",
    falseAlarms:
      "The two reports can have different ages, and the difference does not give the wind's direction.",
  },
  selected_altitude_overshoot: {
    name: "Selected altitude overshoot",
    what: "An aircraft that went past the altitude set on its autopilot panel before coming back to it.",
    why: "Few sources show the selected altitude, and an overshoot means the aircraft went beyond the level set for it.",
    falseAlarms:
      "A selected altitude is a panel setting, and it is not a clearance. A late change to the selection, or an altitude report that lags, can create a false overshoot.",
  },
  above_rated_altitude: {
    name: "Above the certified ceiling",
    what: "An aircraft reporting altitudes above the ceiling in its type certificate data sheet.",
    why: "Certified ceilings are published limits, so a sustained reading past one is rare.",
    falseAlarms:
      "A faulty altitude report, a wrong type in the database, or a setting aboard the aircraft can produce one. A reading 50% or more past the limit counts as bad data.",
  },
  above_rated_speed: {
    name: "Above the certified speed",
    what: "An aircraft whose airspeed, estimated or reported, runs past the maximum in its type certificate data sheet.",
    why: "Certified speed limits are published figures, so a sustained reading past one is rare.",
    falseAlarms:
      "A wrong wind estimate or a bad ground speed report can push the estimate over the limit. A reading 50% or more past the limit counts as bad data.",
  },
  winds_aloft_day: {
    name: "Winds aloft day",
    what: "A day when jets at cruising height ran far faster eastbound than westbound.",
    why: "A gap that large fits a strong westerly wind at cruising height, such as a jet stream overhead.",
    falseAlarms:
      "A different mix of aircraft types in each direction can widen the gap. The site never measures the wind itself, and any wind figures shown are model forecasts.",
  },
  special_squawk_7400: {
    name: "Squawk 7400",
    what: "The code an unmanned aircraft sets in the United States when it loses its control link.",
    why: "Large unmanned aircraft are seldom seen here, and 7400 is the one code set aside for them.",
    falseAlarms:
      "A crewed aircraft dialing through 7400 on the way to another code, or a mistyped code, can show it briefly.",
  },
  special_squawk_7500: {
    name: "Squawk 7500",
    what: "The code for unlawful interference, which usually means a hijacking.",
    why: "It is one of three codes with a fixed emergency meaning worldwide, and it is very seldom real.",
    falseAlarms:
      "Pilots sometimes set it by mistake while changing codes, and a short burst before a normal code returns usually means just that.",
  },
  special_squawk_7600: {
    name: "Squawk 7600",
    what: "The code for a radio communication failure.",
    why: "It tells controllers that the crew cannot talk to them, and it is one of three codes with a fixed meaning worldwide.",
    falseAlarms:
      "Pilots can pass through 7600 while dialing another code, and a short burst before a normal code returns usually means that.",
  },
  special_squawk_7700: {
    name: "Squawk 7700",
    what: "The code for a general emergency.",
    why: "It is the most common emergency code, set for anything from a sick passenger to a precautionary landing.",
    falseAlarms:
      "Codes are sometimes set by mistake, and a short burst before a normal code returns is often a pilot dialing through it.",
  },
  special_squawk_general: {
    name: "General emergency status",
    what: "A general emergency declared through the emergency field of the aircraft's ADS-B broadcasts instead of the 7700 code.",
    why: "The emergency field is separate from the transponder code, so it can show a status the code does not.",
    falseAlarms:
      "Equipment settings and decoding errors can set the field, and the status alone does not confirm an emergency.",
  },
  special_squawk_lifeguard: {
    name: "Lifeguard status",
    what: "The lifeguard status in the emergency field of the aircraft's ADS-B broadcasts, which asks for medical priority handling.",
    why: "It is a priority request, and the site keeps it apart from the emergency codes so it never hides a later emergency.",
    falseAlarms:
      "The field can be set by mistake, or left on after the request no longer applies.",
  },
  special_squawk_minfuel: {
    name: "Minimum fuel status",
    what: "The minimum fuel status in the emergency field of the aircraft's ADS-B broadcasts.",
    why: "Minimum fuel tells controllers that the aircraft can accept little delay. It is an advisory, and it is not an emergency declaration.",
    falseAlarms:
      "The field can be set by mistake, and the data cannot show how much fuel was aboard.",
  },
  special_squawk_nordo: {
    name: "Radio failure status",
    what: "A radio failure declared through the emergency field of the aircraft's ADS-B broadcasts instead of the 7600 code.",
    why: "The emergency field is separate from the transponder code, so it can show a status the code does not.",
    falseAlarms:
      "Equipment settings and decoding errors can set the field, and the status alone confirms nothing about the radios.",
  },
  special_squawk_unlawful: {
    name: "Unlawful interference status",
    what: "Unlawful interference declared through the emergency field of the aircraft's ADS-B broadcasts instead of the 7500 code.",
    why: "The emergency field is separate from the transponder code, so it can show a status the code does not.",
    falseAlarms:
      "Equipment settings and decoding errors can set the field, and the status alone does not confirm an incident.",
  },
  special_squawk_downed: {
    name: "Downed aircraft status",
    what: "The downed aircraft status in the emergency field of the aircraft's ADS-B broadcasts.",
    why: "The status is meant for an aircraft that has come down, so a report of it from an aircraft still in flight is a puzzle worth a look.",
    falseAlarms:
      "A decoding error or an equipment setting can produce it, and the status alone confirms nothing.",
  },
  special_use_code: {
    name: "Special-use code",
    what: "A transponder code with an assigned meaning beyond the emergency codes.",
    why: "These codes mark less common kinds of flying, and they stand out among the ordinary codes controllers assign.",
    falseAlarms:
      "The meanings apply in the United States and can differ elsewhere. A code can be set by mistake or kept after it no longer applies.",
  },
  archive_squawk_7400: ARCHIVE_SQUAWK(
    "7400",
    "an unmanned aircraft's lost link",
  ),
  archive_squawk_7500: ARCHIVE_SQUAWK("7500", "unlawful interference"),
  archive_squawk_7600: ARCHIVE_SQUAWK("7600", "a radio communication failure"),
  archive_squawk_7700: ARCHIVE_SQUAWK("7700", "a general emergency"),
  tcas_ra: {
    name: "TCAS resolution advisory",
    what: "A collision-avoidance instruction, such as climb or descend, issued automatically by the aircraft's TCAS and reported in its own broadcasts.",
    why: "Broadcast data seldom shows what happens on the flight deck, and an advisory is one of the few moments it does.",
    falseAlarms:
      "Many advisories happen at spacing controllers treat as normal, so one is not evidence of a near miss. The spacing figures come from broadcast positions, which have errors of their own.",
  },
  outlier_gs: {
    name: "Ground speed spike",
    what: "A reported ground speed far beyond what any airliner could reach.",
    why: "The reports on either side of a spike usually show the true value, so the story can point to the reading that went wrong.",
    falseAlarms:
      "A very fast jet with a strong tailwind can come near the bound, so the story sets the neighboring reports beside the spike.",
  },
  outlier_alt_baro: {
    name: "Altitude spike",
    what: "A reported altitude no ordinary aircraft could reach, or one far below sea level.",
    why: "A wild altitude among normal ones points to a fault somewhere between the aircraft's equipment and the decoder.",
    falseAlarms:
      "A few balloons and specialized aircraft do fly above 65,000 ft, so the story checks the type and the neighboring reports.",
  },
  outlier_baro_rate: {
    name: "Vertical rate spike",
    what: "A reported climb or descent rate far beyond what aircraft fly.",
    why: "Setting the rate beside the altitude reports shows whether the aircraft moved at all the way the rate says.",
    falseAlarms:
      "A steep dive by a fast jet can come close to these rates, so the story compares the rate with the change in altitude.",
  },
  outlier_archive_alt: {
    name: "Altitude spike in saved archives",
    what: "An impossible altitude found in saved reports from the past.",
    why: "Archived glitches show which records need care before anyone reads a flight from them.",
    falseAlarms:
      "Archives can repeat a stale value, and without the altitude's reference the comparison is looser.",
  },
  position_jump: {
    name: "Position jump",
    what: "A track that leaps across the map between two reports.",
    why: "A jump marks a stretch of the track that should not be trusted, and maps break the line there.",
    falseAlarms:
      "Positions from different sources, such as broadcasts and multilateration estimates, can disagree enough to look like a jump.",
  },
  altitude_gap: {
    name: "Altitude gap",
    what: "Two altitude readings from one aircraft that disagree by thousands of feet.",
    why: "Pressure and geometric altitude usually stay within a few hundred feet of each other, so a wide gap is unusual.",
    falseAlarms:
      "Very cold or unusual weather can widen the gap for real, and a stale field can make it look wider.",
  },
  type_speed_mismatch: {
    name: "Speed mismatch with the listed type",
    what: "A ground speed that the aircraft type in the database could not reach.",
    why: "It usually means the address belongs to a different aircraft than the database lists.",
    falseAlarms:
      "An exceptional tailwind can carry a slow type close to the bound. A wrong type in the database is the usual cause, and the story names no cause.",
  },
  type_altitude_mismatch: {
    name: "Altitude mismatch with the listed type",
    what: "An altitude that the aircraft type in the database could not reach.",
    why: "It usually means the address belongs to a different aircraft than the database lists.",
    falseAlarms:
      "A faulty altitude report can do the same, and the story names no cause.",
  },
  identity_conflict: {
    name: "Identity conflict",
    what: "An aircraft whose identifiers point to different airframes.",
    why: "The address, the registration, and the callsign normally agree, so a disagreement shows where the database or the aircraft's settings need a second look.",
    falseAlarms:
      "A re-registered aircraft that kept its old address, a mistyped callsign, or a database error can each explain it.",
  },
  unusual_emitter: {
    name: "Unusual emitter category",
    what: "An aircraft whose transmitter says it is something other than a powered airplane or helicopter, such as a glider or a balloon.",
    why: "Gliders and balloons carrying ADS-B are a small share of traffic, and the category tells you what to look for on the track.",
    falseAlarms:
      "The category is set aboard, and wrong settings are common enough that the site wants the listed type to agree.",
  },
  space_category: {
    name: "Space vehicle category",
    what: "A transmitter that declares itself a space or trans-atmospheric vehicle.",
    why: "Real space vehicles almost never appear in this data, so the category shows plainly how a transmitter setting can go wrong.",
    falseAlarms:
      "Almost every report comes from a misconfigured transmitter or bad data, so a story never treats the category as proof of a spacecraft.",
  },
  correlated_data_anomaly: {
    name: "Shared data anomaly",
    what: "Several aircraft developing the same data fault at about the same time.",
    why: "One aircraft's glitch usually comes from its own equipment, so a shared fault points to a cause the aircraft have in common, which the data cannot name.",
    falseAlarms:
      "In busy traffic, three unrelated glitches can land in the same minute by chance.",
  },
  gnss_integrity_cluster: {
    name: "GPS integrity drops",
    what: "A group of aircraft that stopped vouching for their GPS positions at about the same time.",
    why: "When several aircraft lose integrity together, the cause is probably shared, and the story shows when and where the drops happened.",
    falseAlarms:
      "Separate equipment faults can line up by chance. The broadcasts never identify a cause, so the stories call these integrity drops and name none.",
  },
  unregistered_address: {
    name: "Unlisted address",
    what: "An ADS-B address with no entry in any registry the site checks.",
    why: "Every address should belong to one registered aircraft, so a missing entry is a small puzzle about the records.",
    falseAlarms:
      "Registries lag new registrations, some States publish no register the site can read, and an address can be programmed into a transmitter by hand.",
  },
};

const topicsTable = JSON.parse(readFileSync(engineTopics, "utf8"));

// One kind's entry: its name as a heading and the four labeled parts.
const entry = (kind) => {
  const { name, what, why, falseAlarms } = KIND_GUIDE[kind];
  const parts = [what, why, kindTriggers[kind], falseAlarms];
  return `<h4 id="${kindAnchor(kind)}">${escape(name)}</h4>
<dl class="kind-guide">${parts.map((text, index) => `<div><dt>${KIND_PARTS[index]}</dt><dd>${escape(text)}</dd></div>`).join("")}</dl>`;
};

// The section, a topic heading over each group of entries.
export const kindGuideSection = () => {
  const groups = Object.entries(topicsTable.topics)
    .map(([slug, topic]) => [
      slug,
      topic.title,
      topic.kinds.filter((kind) => kind in KIND_GUIDE),
    ])
    .filter(([, , kinds]) => kinds.length);
  return `<h2 id="kinds">Kinds of story</h2>
<p>Every story starts from one of the detection rules below, grouped by topic. The rules pick out readings worth a closer look, and the story around each one explains what the reading can and cannot show. The list of limits at the end of a story links to its entry here.</p>
${groups.map(([slug, title, kinds]) => `<h3 id="kinds-${slug}">${escape(title)}</h3>\n${kinds.map(entry).join("\n")}`).join("\n")}`;
};
