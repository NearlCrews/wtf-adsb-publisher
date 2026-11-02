const point = (x, y) => `${x.toFixed(2)},${y.toFixed(2)}`;

// The sample flight lasts 27 minutes.
export const duration = 1620;

export function makeTrack() {
  const points = [];
  for (let i = 0; i <= 330; i += 1) {
    const progress = i / 330;
    let x;
    let y;
    let phase;
    if (progress < 0.12) {
      const f = progress / 0.12;
      x = 6 + f * 56;
      y = 48 - f * 20;
      phase = "Arriving";
    } else if (progress <= 0.88) {
      const f = (progress - 0.12) / 0.76;
      const angle = f * Math.PI * 22;
      const radius = 11 + Math.sin(f * Math.PI * 5) * 1.6;
      x = 50 + radius * Math.cos(angle);
      y = 28 + radius * 0.78 * Math.sin(angle);
      phase = "Circling";
    } else {
      const f = (progress - 0.88) / 0.12;
      x = 61 + f * 31;
      y = 28 - f * 19;
      phase = "Departing";
    }
    points.push({
      t: Number((progress * duration).toFixed(3)),
      x: Number(x.toFixed(3)),
      y: Number(y.toFixed(3)),
      speed: Math.round(62 + Math.sin(progress * 25) * 8),
      altitude: Math.round((1250 + Math.sin(progress * 17) * 65) / 25) * 25,
      phase,
    });
  }
  return { version: 1, kind: "schematic", points };
}

const terrain = `<g fill="none" stroke="#8fae9e" stroke-width=".13" opacity=".23"><path d="M0 10H100M0 20H100M0 30H100M0 40H100M0 50H100M10 0V60M20 0V60M30 0V60M40 0V60M50 0V60M60 0V60M70 0V60M80 0V60M90 0V60"/><path d="M0 48 25 44 49 51 72 47 100 56M4 0 18 21 25 44 29 60M0 22 34 18 54 8 100 14" stroke-width=".4"/><path d="M83 0Q73 13 82 28T89 60" stroke="#71989b" stroke-width="3.8" opacity=".5"/></g>`;

// Matches the theme's --map colour; standalone SVG files cannot read it.
const mapColor = "#142e30";

function svg(label, body, background = mapColor) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1000" viewBox="0 0 600 500" role="img" aria-label="${label}"><rect width="600" height="500" fill="${background}"/>${body}</svg>`;
}

export function illustrations() {
  const track = makeTrack();
  const path = track.points
    .map((p, index) => `${index ? "L" : "M"}${point(p.x, p.y)}`)
    .join(" ");
  const commonHeader = `<text x="34" y="42" fill="#ceded0" font-family="sans-serif" font-size="11" letter-spacing="2">TRACK / INVENTED OBSERVATION</text>`;
  const circle = svg(
    "Illustration of a helicopter flying repeated circuits; invented example, not a geographic map",
    `${commonHeader}<g transform="translate(0 67) scale(6)">${terrain}<path d="${path}" fill="none" stroke="#92c9ed" stroke-width=".43" stroke-linejoin="round"/><circle cx="50" cy="28" r=".8" fill="#dfe2cb"/><path d="M92 7 93 11 92 10 90 11Z" fill="#fff4d8"/></g><g fill="#e9ecd9" font-family="sans-serif"><text x="34" y="414" font-size="34">11<tspan font-size="11" dx="10" fill="#c3d2c4">CIRCUITS</tspan></text><text x="216" y="414" font-size="34">27<tspan font-size="11" dx="10" fill="#c3d2c4">MINUTES</tspan></text><text x="430" y="414" font-size="34">331<tspan font-size="11" dx="10" fill="#c3d2c4">POINTS</tspan></text><path d="M34 436H566" stroke="#547271"/><text x="34" y="460" font-size="10" fill="#bed0c4">SCHEMATIC TRACK · SAMPLE DATA · NOT A GEOGRAPHIC MAP</text></g>`,
  );
  const plane = svg(
    "Original schematic side-view illustration of an oversized cargo aircraft, not a photograph",
    `<g stroke="#bfc8c0" opacity=".5"><path d="M0 100H600M0 200H600M0 300H600M0 400H600M100 0V500M200 0V500M300 0V500M400 0V500M500 0V500"/></g><text x="34" y="42" fill="#3b514d" font-family="sans-serif" font-size="11" letter-spacing="2">AIRCRAFT FILE / TYPE ILLUSTRATION</text><g transform="translate(20 75)"><path d="M42 200Q48 185 71 181L108 142Q139 116 183 116H340Q369 117 379 142L427 187 504 171 527 121 546 119 538 204 566 216Q562 233 541 235L103 242 51 227Q31 217 42 200Z" fill="#eeeae0" stroke="#526a64" stroke-width="2"/><path d="M121 244 256 266 377 260 344 241 209 232Z" fill="#849a8c" stroke="#526a64" stroke-width="2"/><path d="M92 181 110 164 138 164 136 182Z" fill="#284541"/><path d="M63 218H507" stroke="#b64b2c" stroke-width="3"/><path d="M281 253 294 277 328 279 321 252" fill="#dee3d9" stroke="#526a64" stroke-width="2"/><path d="M134 240V259M444 235V252" stroke="#526a64" stroke-width="4"/><circle cx="134" cy="263" r="7" fill="#29413c"/><circle cx="444" cy="256" r="7" fill="#29413c"/></g><text x="34" y="424" fill="#29413c" font-family="serif" font-size="34">747-400 cargo conversion</text><text x="34" y="461" fill="#415952" font-family="sans-serif" font-size="11" letter-spacing="1">747 LARGE CARGO FREIGHTER / ORIGINAL SCHEMATIC</text>`,
    "#dae2d6",
  );
  const values = [220, 230, 218, 226, 224, 8400, 8380, 229, 218, 224, 230];
  const chart = values.map((v, i) => point(57 + i * 47, 365 - v / 30));
  const spike = svg(
    "Illustrative chart with two position-implied speed spikes around 8400 knots surrounded by values near 220 knots",
    `<text x="34" y="42" fill="#3b514d" font-family="sans-serif" font-size="11" letter-spacing="2">THE NUMBERS / INVENTED EXAMPLE</text><g stroke="#cfc9b8"><path d="M57 90H550M57 180H550M57 270H550M57 365H550"/></g><path d="M${chart.join(" L")}" fill="none" stroke="#ad442b" stroke-width="3"/>${chart.map((p) => `<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="4" fill="#ad442b"/>`).join("")}<g font-family="sans-serif" fill="#354e46"><text x="378" y="90" font-size="36">8,400<tspan font-size="13" dx="6">kn</tspan></text><text x="378" y="111" font-size="11">ONE SUSPECT FIX</text><text x="57" y="420" font-size="29">Neighboring speeds: ~220 kn</text><text x="57" y="457" font-size="10" letter-spacing="1">POSITION-IMPLIED SPEED / SAMPLE DATA</text></g>`,
    "#eee7d7",
  );
  const grid = svg(
    "Illustration of repeated parallel flight passes, invented example",
    `${commonHeader}<g transform="translate(0 60) scale(6)">${terrain}<path d="M8 47 25 43V12Q25 7 31 7T37 12V43Q37 48 43 48T49 43V12Q49 7 55 7T61 12V43Q61 48 67 48T73 43V12Q73 7 79 7T85 12V43L94 52" stroke="#92c9ed" stroke-width=".5" fill="none" stroke-linejoin="round"/></g><text x="34" y="445" fill="#e5e7d3" font-family="serif" font-size="32">Six parallel passes</text><text x="34" y="473" fill="#c0d1c5" font-family="sans-serif" font-size="10">SCHEMATIC TRACK · SAMPLE DATA</text>`,
  );
  const speeds = [410, 424, 438, 451, 465];
  const speedPoints = speeds.map((v, i) =>
    point(76 + i * 117, 350 - (v - 400) * 3.5),
  );
  const performance = svg(
    "Five invented ground-speed reports rise from 410 to 465 knots over twenty minutes; pressure altitude is 35000 feet at each sample",
    `<text x="34" y="42" fill="#d2e5df" font-family="sans-serif" font-size="11" letter-spacing="2">PERFORMANCE / INVENTED OBSERVATION</text><g font-family="sans-serif" font-size="11" fill="#bfd8cc">${[400, 420, 440, 460].map((v) => `<path d="M76 ${350 - (v - 400) * 3.5}H544" stroke="#496a63"/><text x="34" y="${354 - (v - 400) * 3.5}">${v}</text>`).join("")}<text x="34" y="89">kn</text><text x="76" y="381">0 min</text><text x="299" y="381">10 min</text><text x="508" y="381">20 min</text></g><path d="M${speedPoints.join(" L")}" fill="none" stroke="#85dec4" stroke-width="3"/>${speedPoints.map((p, i) => `<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="5" fill="#85dec4"/><text x="${Number(p.split(",")[0]) - 14}" y="${Number(p.split(",")[1]) - 17}" font-family="sans-serif" font-size="16" fill="#e7f4eb">${speeds[i]}</text>`).join("")}<text x="34" y="434" fill="#e7f4eb" font-family="sans-serif" font-size="29">Ground speed increased by 55 kn</text><text x="34" y="468" fill="#bfd8cc" font-family="sans-serif" font-size="10">FIVE SAMPLES · REPORTED PRESSURE ALTITUDE 35,000 FT</text>`,
  );
  const transponder = svg(
    "Invented transponder reports show 1200 at 09:12:10 UTC, 7700 at 09:12:20 UTC and 1200 at 09:12:30 UTC",
    `<text x="34" y="42" fill="#594b24" font-family="sans-serif" font-size="11" letter-spacing="2">TRANSPONDER / INVENTED OBSERVATION</text><path d="M100 270H500" stroke="#9b7d30" stroke-width="2"/><g font-family="sans-serif" text-anchor="middle">${["1200", "7700", "1200"].map((code, i) => `<circle cx="${100 + i * 200}" cy="270" r="7" fill="#826418"/><text x="${100 + i * 200}" y="230" font-size="42" fill="#493d1e">${code}</text><text x="${100 + i * 200}" y="308" font-size="14" fill="#594b24">09:12:${10 + i * 10}</text>`).join("")}</g><text x="34" y="399" fill="#493d1e" font-family="sans-serif" font-size="29">One sample contains 7700</text><text x="34" y="433" fill="#594b24" font-family="sans-serif" font-size="12">The samples do not establish the transmission's duration.</text><text x="34" y="467" fill="#594b24" font-family="sans-serif" font-size="10" letter-spacing="1">THREE CODE REPORTS · TIMES IN UTC</text>`,
    "#f0e7cf",
  );
  return {
    "circling.svg": circle,
    "cargo.svg": plane,
    "spike.svg": spike,
    "grid.svg": grid,
    "performance.svg": performance,
    "transponder.svg": transponder,
  };
}

export function replayCard() {
  return `<figure class="flight-replay kg-width-wide" data-replay-src="/examples/circling.json">
  <div class="replay-heading"><strong>Track replay</strong><span>Illustrative example · 27 minutes · elapsed time</span></div>
  <svg class="replay-map" viewBox="0 0 100 60" role="img" aria-label="Schematic replay of the invented helicopter flight"><rect width="100" height="60" fill="${mapColor}"/>${terrain}<g data-track-layer></g></svg>
  <div class="replay-controls" hidden>
    <button type="button" data-replay-play disabled>Play replay</button>
    <label class="replay-scrubber"><span class="sr-only">Flight replay position</span><input type="range" min="0" max="${duration}" step="1" value="0"><span data-replay-time>00:00</span></label>
    <label><span class="sr-only">Playback speed</span><select aria-label="Playback speed"><option value="30">30× speed</option><option value="60" selected>60× speed</option><option value="120">120× speed</option></select></label>
  </div>
  <div class="replay-readouts"><div><span>Ground speed </span><strong data-replay-speed>—</strong></div><div><span>Reported altitude </span><strong data-replay-altitude>—</strong></div><div><span>Phase </span><strong data-replay-phase>—</strong></div></div>
  <p class="sr-only" role="status" data-replay-status></p>
  <noscript><p class="replay-error">The interactive replay needs JavaScript. The illustration above shows the complete example path.</p></noscript>
  <figcaption>The line is an invented flight path on a schematic background. The marker moves between sample points; speed and altitude show the preceding sample. No geographic location is represented. Use the slider to examine a moment.</figcaption>
  </figure>`;
}
