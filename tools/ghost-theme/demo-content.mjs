import Handlebars from "handlebars";
import { replayCard } from "./illustrations.mjs";
import { kindGuideSection } from "./kind-guide.mjs";
import { renderMarkdown } from "./markdown.mjs";
import {
  SITE_NAME,
  aboutText,
  authorName,
  supportLink,
} from "./site-settings.mjs";

// Demo content: the author, the invented example stories, and the shared site
// pages. The site's name, author, and support link come from the site
// settings. The About page opens with a generic introduction that names no
// place, hardware, or host; an operator's about_file replaces it.

const escape = Handlebars.escapeExpression;
const authorLabel = authorName();
// Ghost's author slug must match: the name in lower case, with accents
// dropped and each run of other characters turned into one hyphen.
const authorSlug = authorLabel
  .normalize("NFKD")
  .replace(/[\u0300-\u036f]/g, "")
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, "-")
  .replace(/^-|-$/g, "");

// The public author page.
export const author = {
  name: authorLabel,
  slug: authorSlug,
  url: `/author/${authorSlug}/`,
  bio: `Runs the ${SITE_NAME} receiver and maintains the software that selects, checks, and publishes its stories about rare types, odd flight paths, and readings that don't add up.`,
};

const note = `<aside class="observation-note" aria-label="Example notice"><strong>Invented observation</strong>The flight, measurements, and timing are synthetic examples for this preview. They are not findings from the receiver history.</aside>`;
const sourceBox = (items) =>
  `<aside class="sources" aria-label="Background sources"><h2>Background sources</h2><ul>${items.map(([label, url]) => `<li><a href="${url}" rel="external noopener noreferrer">${label}</a></li>`).join("")}</ul><small>These sources explain the subject. They do not verify the invented observation.</small></aside>`;
const aircraft = (type) => ({ type, registration: null, callsign: null });
const measure = (label, value, unit, scope) => ({ label, value, unit, scope });

export const stories = [
  {
    slug: "a-helicopter-kept-circling",
    title: "Helicopter track shows 11 circuits during a 27-minute observation",
    kind: "circling",
    // The invented readings' reception source; the other examples are ADS-B.
    source: "mlat",
    context: {
      topic: "flight-shapes",
      section: "flight-patterns",
      type: { code: "B407", name: "Bell 407", slug: "bell-407" },
    },
    event_type: "flight-patterns",
    is_event: true,
    observation_start: "2026-10-04T14:03:00.000Z",
    observation_end: "2026-10-04T14:30:00.000Z",
    published_at: "2026-10-04T15:00:00.000Z",
    updated_at: "2026-10-04T15:00:00.000Z",
    aircraft: aircraft("Bell 407, illustrative type assignment"),
    measurements: [
      measure("Circuits", 11, "", "Repeated turns within the selected track"),
      measure(
        "Observation",
        27,
        "min",
        "Includes arrival, circling, and departure",
      ),
      measure(
        "Track samples",
        331,
        "",
        "Synthetic positions across the observation",
      ),
    ],
    image: "circling.svg",
    excerpt:
      "Repeated turns occupy the middle of the track. Arrival and departure account for the rest of the observation interval.",
    minutes: 2,
    caption:
      "Original schematic of an invented flight, with no geographic location or receiver marker.",
    html: `${note}<p>The example track contains eleven circuits between an arrival segment and a departure segment. The complete observation lasts 27 minutes, from 14:03 to 14:30 UTC on October 4. The circling occupies about 21 minutes of that interval.</p><dl class="evidence-grid kg-width-wide"><div><dt>Circuits</dt><dd>11</dd><dd class="detail">Turns within the repeated section</dd></div><div><dt>Observation interval</dt><dd>27 <small>min</small></dd><dd class="detail">Arrival, circling, and departure</dd></div><div><dt>Track samples</dt><dd>331</dd><dd class="detail">Invented positions, about five seconds apart</dd></div></dl><h2>Track and measurements</h2><p>The repeated section returns to the same area after each turn. That geometry supports describing the movement as circling. The circuit count applies to that section; it does not count separate flights.</p>${replayCard()}<p>Ground speed and reported altitude in the replay are invented sample values. The background has no geographic scale, and the altitude reference is unspecified, so the displayed altitude cannot be read as height above the ground.</p><h2>Aircraft and interpretation</h2><p>The illustrative type assignment is a Bell 407. No registration, callsign, or independent account of the flight is supplied. The track describes the movement, but it does not establish the crew's task or an activity on the ground.</p>${sourceBox(
      [
        [
          "Bell 407 aircraft information",
          "https://www.bellflight.com/products/bell-407",
        ],
        [
          "Flightradar24's guide to flight patterns",
          "https://www.flightradar24.com/blog/aviation-explainer-series/interesting-patterns-on-flightradar24/",
        ],
      ],
    )}`,
  },
  {
    slug: "one-suspect-position-fix",
    title: "A position outlier produced two implausible speed calculations",
    kind: "position_jump",
    context: { topic: "ads-b-glitches", section: "data-quality" },
    event_type: "data-quality",
    is_event: true,
    observation_start: "2026-10-03T16:41:00.000Z",
    observation_end: "2026-10-03T16:42:40.000Z",
    published_at: "2026-10-04T15:30:00.000Z",
    updated_at: "2026-10-04T15:30:00.000Z",
    aircraft: aircraft("Aircraft type unavailable"),
    measurements: [
      measure(
        "Peak implied speed",
        8400,
        "kn",
        "Calculated for one adjacent position pair",
      ),
      measure(
        "Neighboring speeds",
        "about 220",
        "kn",
        "Position-derived values outside the two affected segments",
      ),
      measure(
        "Suspect fixes",
        1,
        "",
        "One displaced point affects two adjacent segments",
      ),
    ],
    image: "spike.svg",
    excerpt:
      "One displaced position creates excessive speeds on the segment entering it and the segment leaving it. Neighboring calculated values remain near 220 knots.",
    minutes: 2,
    caption:
      "Invented position-derived speeds. The line connects calculated values; it is not a flight path.",
    html: `${note}<p>One displaced point in this example produces calculated speeds of 8,400 and 8,380 knots on adjacent track segments. The neighboring values are near 220 knots. These are speeds calculated from position changes and elapsed time, not separate speed reports from the aircraft.</p><dl class="evidence-grid kg-width-wide"><div><dt>Peak calculated speed</dt><dd>8,400 <small>kn</small></dd><dd class="detail">One pair of adjacent positions</dd></div><div><dt>Neighboring values</dt><dd>~220 <small>kn</small></dd><dd class="detail">Also calculated from positions</dd></div><div><dt>Suspect position fixes</dt><dd>1</dd><dd class="detail">Two affected adjacent segments</dd></div></dl><h2>Why there are two spikes</h2><p>The displaced point is far from the preceding track. Dividing that distance by the short time between samples produces the first excessive speed. The following point returns to the previous track, producing a second excessive speed in the opposite direction.</p><p>The paired spikes and the surrounding sequence support flagging the position for review. They do not identify a particular equipment fault, interference, or deliberate spoofing. A review would compare the position source and timestamps with any independently reported speed that was available.</p><p>The chart connects calculated values so the outliers can be compared. A flight replay should mark the suspect position transition, rather than animate it as confirmed travel.</p>${sourceBox([["Flightradar24 on common tracking errors", "https://www.flightradar24.com/blog/inside-flightradar24/common-errors-on-flightradar24/"]])}`,
  },
  {
    slug: "a-flight-in-parallel-lines",
    title: "Aircraft track contains six parallel passes",
    kind: "parallel_passes",
    context: { topic: "survey-flights", section: "flight-patterns" },
    event_type: "flight-patterns",
    is_event: true,
    observation_start: "2026-09-30T23:49:00.000Z",
    observation_end: "2026-10-01T00:13:00.000Z",
    published_at: "2026-10-01T08:00:00.000Z",
    updated_at: "2026-10-01T08:00:00.000Z",
    aircraft: aircraft("Aircraft type unavailable"),
    measurements: [
      measure(
        "Parallel passes",
        6,
        "",
        "Straight sections connected by alternating turns",
      ),
      measure(
        "Observation",
        24,
        "min",
        "Selected interval crosses midnight UTC",
      ),
    ],
    image: "grid.svg",
    excerpt:
      "Six straight sections run alongside one another, with alternating turns connecting the passes. The 24-minute observation crosses midnight UTC.",
    minutes: 1,
    caption:
      "Invented parallel-pass pattern on a schematic background, not a geographic map.",
    html: `${note}<p>The track contains six parallel passes connected by alternating turns. The selected observation runs from 23:49 UTC on September 30 to 00:13 UTC on October 1. Its event count belongs to September 30, when the observation began.</p><p>The repeated straight sections establish the pattern in this illustration. Parallel tracks can occur during aerial survey work, as described in Flightradar24's flight-pattern guide, but the shape alone does not establish the purpose of this example.</p><p>Pass spacing, timing, and altitude would help describe a real observation in more detail. The illustration has no geographic scale and provides no altitude readings, so it cannot support measurements of pass separation or height.</p>${sourceBox([["Flightradar24's explanation of survey patterns", "https://www.flightradar24.com/blog/aviation-explainer-series/interesting-patterns-on-flightradar24/"]])}`,
  },
  {
    slug: "ground-speed-increased-during-level-segment",
    title: "Reported ground speed increased from 410 to 465 knots",
    kind: "fast",
    context: { topic: "speed-and-altitude", section: "performance" },
    event_type: "performance",
    is_event: true,
    observation_start: "2026-10-03T11:42:00.000Z",
    observation_end: "2026-10-03T12:02:00.000Z",
    published_at: "2026-10-03T12:30:00.000Z",
    updated_at: "2026-10-03T12:30:00.000Z",
    aircraft: aircraft("Aircraft type unavailable"),
    measurements: [
      measure(
        "Ground-speed change",
        55,
        "kn",
        "Difference between first and last reported speeds",
      ),
      measure(
        "Observation",
        20,
        "min",
        "Five synthetic readings at five-minute intervals",
      ),
      measure(
        "Pressure altitude",
        35000,
        "ft",
        "Same reported value in all five samples",
      ),
    ],
    image: "performance.svg",
    excerpt:
      "Five samples show rising ground speed while reported pressure altitude stays at 35,000 feet. The record contains no airspeed or wind measurements.",
    minutes: 1,
    caption:
      "Synthetic ground-speed readings at five-minute intervals. No real flight is represented.",
    html: `${note}<p>Ground speed rises from 410 to 465 knots over this 20-minute example. Five readings at five-minute intervals are 410, 424, 438, 451, and 465 knots. Each sample reports a pressure altitude of 35,000 feet.</p><p>The first-to-last increase is 55 knots. Ground speed describes movement over the ground; without airspeed and wind measurements, the increase cannot be attributed to a tailwind or to a change in the aircraft's airspeed.</p><p>The equal altitude reports apply to the five sample times. They do not establish that altitude remained constant between them.</p>`,
  },
  {
    slug: "transponder-code-changed-in-three-samples",
    title: "Transponder code changed from 1200 to 7700, then returned",
    kind: "special_squawk_7700",
    context: { topic: "squawk-codes", section: "transponder-reports" },
    event_type: "transponder-reports",
    is_event: true,
    observation_start: "2026-09-24T09:12:10.000Z",
    observation_end: "2026-09-24T09:12:30.000Z",
    published_at: "2026-09-25T10:00:00.000Z",
    updated_at: "2026-09-25T10:00:00.000Z",
    aircraft: aircraft("Aircraft type unavailable"),
    measurements: [
      measure("Recorded code", "7700", "", "Middle sample only"),
      measure(
        "Sample spacing",
        10,
        "s",
        "Three received reports, 20 seconds from first to last",
      ),
      measure(
        "Samples with 7700",
        1,
        "",
        "Does not establish how long the code was transmitted",
      ),
    ],
    image: "transponder.svg",
    excerpt:
      "Three synthetic reports ten seconds apart contain 1200, 7700, and 1200. A single changed report does not establish the duration or cause of the transmission.",
    minutes: 1,
    caption:
      "Invented transponder-code samples at 09:12:10, 09:12:20 and 09:12:30 UTC.",
    html: `${note}<p>The three recorded codes are 1200 at 09:12:10 UTC, 7700 at 09:12:20 and 1200 at 09:12:30. The changed value appears in one sample, with the earlier value present in the samples before and after it.</p><p>The FAA identifies 7700 as an emergency transponder code. Its appearance makes this sequence worth reviewing, but these three samples do not establish how long the aircraft transmitted the middle value, whether the report was valid or whether an incident occurred. No corroborating status report or account of the flight is supplied for this example.</p>${sourceBox([["FAA: transponder emergency operation", "https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap6_section_2.html"]])}`,
  },
  {
    slug: "cargo-aircraft-type-appeared-once-in-sample",
    title: "Cargo aircraft type appeared once in a 30-day sample",
    kind: "watch_type",
    context: {
      topic: "notable-aircraft",
      section: "unusual-aircraft",
      type: {
        code: "BLCF",
        name: "Boeing 747-400 Dreamlifter",
        slug: "boeing-747-dreamlifter",
      },
    },
    // Sourced type background, shown in "About the Boeing 747-400 Dreamlifter".
    story: {
      paragraphs: [],
      facts: {
        "tp.BLCF.1":
          "The Dreamlifter is a converted Boeing 747-400 with an enlarged cargo hold. Boeing describes carrying 787 components in its main-deck hold.",
        "tp.BLCF.2":
          "Boeing's historical product reference records delivery of the fourth and final Dreamlifter in February 2010.",
      },
      sources: [
        {
          id: "src.BLCF.1",
          publisher: "Boeing",
          title: "Boeing describes the Dreamlifter and its cargo",
          url: "https://www.boeing.com/features/2020/05/boeing-uses-3-dreamlifters-to-complete-another-covid-19-airlift-mission",
        },
        {
          id: "src.BLCF.2",
          publisher: "Boeing",
          title: "Boeing's historical product reference (PDF)",
          url: "https://www.boeing.com/content/dam/boeing/v2/company/history/pdf/Boeing_Products.pdf",
        },
      ],
    },
    event_type: "unusual-aircraft",
    is_event: true,
    observation_start: "2026-09-15T18:20:00.000Z",
    observation_end: "2026-09-15T18:27:00.000Z",
    published_at: "2026-10-04T14:00:00.000Z",
    updated_at: "2026-10-04T14:00:00.000Z",
    aircraft: aircraft(
      "Boeing 747-400 Large Cargo Freighter, illustrative type assignment",
    ),
    measurements: [
      measure(
        "Matching aircraft",
        1,
        "",
        "Distinct aircraft assigned this type in the synthetic collection",
      ),
      measure(
        "Comparison sample",
        4800,
        "aircraft",
        "Distinct aircraft from August 17 through September 15 UTC",
      ),
      measure(
        "Reference window",
        30,
        "days",
        "Collection-specific frequency, not worldwide rarity",
      ),
    ],
    image: "cargo.svg",
    excerpt:
      "One of 4,800 distinct aircraft in an invented reference collection is assigned this cargo-aircraft type. The count describes that collection's frequency.",
    minutes: 1,
    caption:
      "Original cargo-aircraft illustration used for an invented type-match example, not evidence of a sighting.",
    html: `${note}<p>This example assigns the Boeing 747-400 Large Cargo Freighter type to an aircraft observed for seven minutes on September 15. In its invented reference collection, one of 4,800 distinct aircraft has that type assignment during August 17 through September 15 UTC.</p><p>The comparison explains why the observation was selected: only one distinct aircraft in that collection carries the type assignment. It does not measure worldwide rarity or prove the historical type assignment. No registration or callsign is supplied.</p><p>Sourced background on the Dreamlifter appears with this example. It describes the type and does not verify the invented observation.</p>`,
  },
].map((s) => ({
  ...s,
  synthetic: s.is_event,
  url: `/stories/${s.slug}/`,
  feature_image: `/examples/${s.image}`,
  feature_image_alt: s.caption,
  feature_image_caption: s.caption,
  custom_excerpt: s.excerpt,
  authors: [author],
}));

const ext = (url, label) =>
  `<a href="${url}" rel="external noopener noreferrer">${label}</a>`;

// A page's settings: %SITE% is the site name (also in titles), %AUTHOR% a
// link to the author page, and %SUPPORT% the support section, which only a
// configured support link adds.
const support = supportLink();
const supportSection = support
  ? `<h2 id="support">Support the project</h2>
<p>If you find these observations useful, you can support ${escape(SITE_NAME)}: ${ext(escape(support.url), escape(support.label))}.</p>
`
  : "";
const fill = (text) =>
  text
    .replaceAll("%SITE%", escape(SITE_NAME))
    .replaceAll(
      "%AUTHOR%",
      `<a href="${author.url}">${escape(author.name)}</a>`,
    )
    .replaceAll("%SUPPORT%", supportSection);

// Shared site pages. The field guide's section ids are link targets for story
// glossary links and topic pages; keep them stable.
export const guideAnchors = [
  "ads-b",
  "mlat",
  "ground-speed",
  "geometric-altitude",
  "squawk",
  "type-match",
  "callsigns",
  "gaps",
  "purpose",
  "counting",
];

// The About page's generic introduction, which says what the site does. An
// operator's about_file replaces it; the sections after it stay.
const aboutIntro = `<p>%SITE% writes about unusual aircraft and odd ADS-B readings picked up by one receiver. It looks for observations worth explaining, from repeated loops and uncommon aircraft types to numbers that disagree. Each story shows the measurements behind it and explains what they support. A flight pattern alone cannot tell us why an aircraft was there.</p>
<h2>How the data arrives</h2>
<p>Aircraft transmit digital messages that can include identity, position, altitude, and speed. Receivers listen on 1090 MHz and, for ${ext("https://www.faa.gov/air_traffic/technology/equipadsb/capabilities/ins_outs", "ADS-B in the United States")}, also on 978 MHz, a system called UAT, short for Universal Access Transceiver.</p>
<p>Some positions are multilateration (MLAT) estimates from cooperating receivers, and stories distinguish those estimates from positions broadcast by an aircraft. Reception gaps and missing fields limit what we can reconstruct; the journal is a selection of observations, not a complete record of traffic.</p>
<p>This is the generic introduction. The site's operator can replace it with their own text, such as who runs the receiver and why, by naming a Markdown file in the publisher's <code>about_file</code> setting.</p>
`;
// The operator's About text, rendered, and the time it last changed.
const operatorAbout = aboutText();

export const pages = [
  {
    slug: "field-guide",
    title: "Reading aircraft observations",
    // The date of the last content change, and that content's pageDigest.
    updated: "2026-11-04T00:00:00Z",
    contentSha256:
      "3e341dfe442cf800449fa0fe2e93e7bc4c47a58b698fead794f56eeffb627c7b",
    custom_excerpt:
      "How to read aircraft identities, positions, and measurements, and how to tell a report from an explanation.",
    html: `<p>Aircraft tracking combines messages about identity and movement into a sequence of observations. The source, timing, and quality of those messages decide which conclusions a record supports.</p>
<h2 id="ads-b">ADS-B</h2>
<p>Automatic Dependent Surveillance-Broadcast is a system in which equipped aircraft broadcast information including identity, position, altitude, and speed. Receivers on the ground collect those messages for tracking. The aircraft works out its own position, usually from GPS, so the position is only as good as the aircraft's equipment. Background: ${ext("https://www.faa.gov/air_traffic/technology/equipadsb/capabilities/ins_outs", "the FAA's ADS-B explanation")}.</p>
<h2 id="mlat">MLAT</h2>
<p>Multilateration estimates an aircraft's position from the times its signals reach several receivers. It locates aircraft that do not broadcast their own position, but the result is an estimate made by the receiving network, with larger errors than a position the aircraft reports itself. Stories say when a position came from MLAT. Background: ${ext("https://www.flightaware.com/adsb/mlat/", "FlightAware's guide to MLAT")}.</p>
<h2 id="sources">Reception sources</h2>
<p>Each story carries a small badge naming how the receiver got its readings, and the story journal can filter on it. The badge describes the reading source only. Story lists leave it off plain ADS-B, the usual source, which a story page still shows. <strong>ADS-B</strong> readings came straight from the aircraft's own broadcasts; most carry the aircraft's ICAO address, some a non-transponder or other address type. <strong>ADS-R</strong> readings were relayed by a ground station that rebroadcasts ADS-B from one frequency to the other, so they are the aircraft's own positions passed along. <strong>TIS-B</strong> readings describe traffic that a ground station saw by radar and broadcast to nearby aircraft; the aircraft itself may carry no ADS-B. <strong>ADS-C</strong> readings are position reports an aircraft sent under a contract with an air traffic service rather than broadcast to all. <strong>MLAT</strong> readings are positions estimated from signal timing across several receivers, with the larger errors described above. <strong>Mode S</strong> readings came from transponder replies that carry identity and altitude but no position.</p>
<h2 id="ground-speed">Ground speed and airspeed</h2>
<p>Ground speed is how fast an aircraft moves over the ground. Airspeed is how fast it moves through the air, and the difference is the wind. A strong tailwind can make an ordinary airliner look fast over the ground. Speeds here are in knots: one knot is one nautical mile per hour, about 1.15 miles per hour. Most reports carry no airspeed. When an aircraft does report its true airspeed, the difference from its ground speed shows how much the wind added or took away along the track, and it does not give the wind's direction.</p>
<h2 id="geometric-altitude">Pressure and geometric altitude</h2>
<p>Aircraft report altitude in two ways. Pressure altitude comes from the barometric altimeter and is the number pilots and controllers use; it assumes a standard atmosphere, so it can differ from the true height by hundreds of feet as temperature and pressure change. Geometric altitude comes from satellite positioning and measures height above a model of the earth's shape. Neither is the height above the ground below the aircraft, and each measurement in a story names its reference.</p>
<h2 id="squawk">Squawk codes</h2>
<p>A squawk is the four-digit code a pilot sets on the transponder, using the digits 0 to 7. Controllers assign most codes so they can tell aircraft apart. A few have fixed meanings, including 7700 for an emergency, 7600 for a radio failure, and 7500 for unlawful interference, and in the United States 1200 marks a flight under visual flight rules that is not talking to a controller. A code is what the transponder reported, and on its own it does not confirm an emergency. Background: ${ext("https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap6_section_2.html", "the FAA's guidance on transponder emergency codes")}.</p>
<h2 id="type-match">Type matches and aircraft databases</h2>
<p>Aircraft broadcast a 24-bit ICAO address, not their type or registration. The site looks the address up in an aircraft database to find the registration, the type designator, and a description. That match describes the database entry today. Registrations move between airframes, owners change, and some entries are wrong, so a story says "the aircraft database lists" when it relies on the match, and the match never shows who was aboard.</p>
<h2 id="callsigns">Callsigns and registrations</h2>
<p>A callsign identifies a flight or operating identity; a registration identifies an aircraft in a registry. They can look alike, but they are separate fields, and an observation should say which one the record contains. Airline callsigns start with a three-letter designator, such as AAR for Asiana Airlines, and the site names an operator only from that designator.</p>
<h2 id="gaps">Gaps in a track</h2>
<p>A gap means there are no suitable observations for that part of the record. Reception, equipment, and data processing all affect what is available. A gap does not establish what the aircraft did between samples, so maps break the line there instead of joining the two sides.</p>
<h2 id="purpose">What a track cannot show</h2>
<p>A track describes movement. An operator's account or another checked source may explain a mission, but the data alone cannot. An aircraft's owner does not establish who was aboard, and a pattern that looks like training, a search, or a survey may be something else.</p>
<h2 id="counting">How the journal counts observations</h2>
<p>One published observation episode counts as one story, even when several detection rules describe it. The daily chart uses the episode's start date in UTC. Later edits do not add stories. Totals describe the journal's selected record, not every aircraft or flight in a region.</p>
${kindGuideSection()}
<p><a class="text-link" href="/stories/">Read every story <span aria-hidden="true">↗</span></a></p>`,
  },
  {
    slug: "about",
    title: "About %SITE%",
    updated: "2026-11-03T00:00:00Z",
    // pageDigest of the text, generic introduction included, before fill()
    // with siteNotes, which replace the closing section. An about_file's text
    // is a setting like the name, so it leaves the digest alone.
    contentSha256:
      "ef687a11e9d5121c350e9cb615f4e0e073a1d0f3d6eb8683802404c8855f57ae",
    custom_excerpt:
      "Where the observations come from, what receives them, and how stories are written and checked.",
    html: `${aboutIntro}<h2 id="writing">Writing and review</h2>
<p>Stories publish automatically, at most nine a day with room for rare exceptional events. Each one starts as template text built from the recorded data, pinned reference tables, earlier stories here, and researched background about the aircraft type. That background comes from an AI research step that may cite only an allowlist of publishers, and every researched fact appears with its source. An AI writer may rewrite the headline, summary, and story text from the same facts; a validator then checks every number, name, date, and place against those facts and rejects text that mentions the receiver's location, guesses who was aboard, or claims an emergency or a mission without hedging. A story that fails keeps its template text.</p>
<p>Measurements, record limits, and aircraft links come straight from the data and are never written by AI. A current registration or type match may differ from an aircraft's historical assignment, and a database association does not say who was aboard.</p>
<h2 id="credits">Data sources and credits</h2>
<ul>
<li>Aircraft type and operator data: the ${ext("https://github.com/Mictronics/aircraft-database", "Mictronics aircraft database")}, under the ${ext("https://opendatacommons.org/licenses/by/1-0/", "Open Data Commons Attribution License 1.0 (ODC-By)")}.</li>
<li>ICAO address allocation ranges: the table in ${ext("https://github.com/wiedehopf/tar1090", "tar1090")}, which transcribes ICAO's allocation plan.</li>
<li>Transponder code meanings: the FAA's ${ext("https://www.faa.gov/documentLibrary/media/Order/2024-04-04_Order_JO_7110.66H_National_Beacon_Code_Allocation_Plan_(NBCAP)_FINAL.pdf", "National Beacon Code Allocation Plan")} and ${ext("https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap4_section_1.html", "Aeronautical Information Manual")}.</li>
<li>Watchlist categories: ${ext("https://github.com/sdr-enthusiasts/plane-alert-db", "Plane Alert DB")}, under the ${ext("https://opendatacommons.org/licenses/odbl/1-0/", "Open Database License 1.0")} and ${ext("https://opendatacommons.org/licenses/dbcl/1-0/", "Database Contents License 1.0")}. A category describes the list entry, not the people aboard.</li>
<li>Organization names and descriptions behind watchlist links: ${ext("https://www.wikidata.org/", "Wikidata")}, under ${ext("https://creativecommons.org/publicdomain/zero/1.0/", "CC0 1.0")}. People are never described this way.</li>
<li>Upper winds for airspeed comparisons: ${ext("https://open-meteo.com/", "Open-Meteo")}, weather data by Open-Meteo.com under the ${ext("https://creativecommons.org/licenses/by/4.0/", "Creative Commons Attribution 4.0 license (CC BY 4.0)")}.</li>
<li>Photos: representative aircraft photos carry their own <a href="/about/image-credits/">credits and licenses</a>; photos of an observed aircraft come from ${ext("https://www.planespotters.net/", "Planespotters.net")}, with the photographer named beside the image. That photo was taken on another occasion, and your browser loads it from Planespotters.net directly.</li>
</ul>
<p>A type's usual ground speed, quoted in speed-record and type-mismatch stories, is the 10th to 90th percentile of the median ground speeds this receiver logged for that type in one flight phase and altitude band over the 30 days before the story was first written, counting each aircraft once, leaving out the story's own aircraft, and requiring at least 20 other aircraft seen over 14 days. It describes these records, not what the type can do.</p>
<h2 id="map-data">Aircraft on a map</h2>
<p>Maps show the saved positions, centered on the recorded path, on topographic backgrounds from ${ext("https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer", "USGS The National Map")} (map services and data available from the U.S. Geological Survey, National Geospatial Program). Each background is saved with the track image, so the map works without a live service; it is a current reference map, not the ground on the flight date. Nearby-place headings and fallback maps use ${ext("https://www.naturalearthdata.com/about/terms-of-use/", "Natural Earth")}, the ${ext("https://www.census.gov/geographies/reference-files/2025/geo/gazetter-file.html", "2025 U.S. Census Gazetteer")}, and ${ext("https://ourairports.com/data/", "OurAirports")}; a place name or airport gives context and does not establish where a flight began or ended.</p>
<p>The scale bar is approximate at the map's middle latitude, and the recorded path length sums the accepted positions in each connected segment, so gaps and position errors affect it. In a replay the marker follows the saved positions, movement between reports is interpolated, the readings shown belong to the saved report, and the marker hides across gaps. Altitude with an unspecified reference is labeled as such. The receiver's exact position, marker, and coverage rings stay private; gaps stay visible, and a selected part of a flight is labeled as such.</p>
%SUPPORT%<h2 id="this-site">About this preview</h2>
<p>The event examples use invented flight data, dates, and measurements. They are not reconstructions of real observations. The Dreamlifter background is sourced and describes the type only.</p>
<p><a class="text-link" href="/field-guide/">Read the field guide <span aria-hidden="true">↗</span></a></p>`,
  },
].map((p) => ({
  ...p,
  url: `/${p.slug}/`,
  template: { title: p.title, html: p.html },
  title: p.title.replaceAll("%SITE%", SITE_NAME),
  html: fill(
    p.slug === "about" && operatorAbout
      ? p.html.replace(aboutIntro, () => renderMarkdown(operatorAbout.text))
      : p.html,
  ),
  // An edited About file moves the page's date.
  ...(p.slug === "about" &&
    operatorAbout &&
    operatorAbout.modified > p.updated && { updated: operatorAbout.modified }),
}));

// The closing section differs for synthetic, review, and published builds.
export const siteNotes = {
  synthetic:
    "<p>The event examples use invented flight data, dates, and measurements. They are not reconstructions of real observations. The Dreamlifter background is sourced and describes the type only.</p>",
  review:
    "<p>This private preview displays an exported observation snapshot. Each story states its publication status and measurement limits. The archive availability boundary describes this selected record; it does not establish complete receiver history.</p>",
  production:
    "<p>Qualifying observations publish automatically after evidence and privacy checks. Each week, one selected observation receives additional sourced background. The archive describes this selected record; it does not establish complete receiver history.</p>",
};
