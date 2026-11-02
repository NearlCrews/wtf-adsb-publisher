(() => {
  "use strict";

  const selector = "[data-geographic-replay]";
  const replays = new Map();
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const write = (element, value) => {
    if (element.textContent !== value) element.textContent = value;
  };
  const utc = (timestamp) =>
    new Date(timestamp).toISOString().slice(0, 19).replace("T", " · ");
  const svg = (name) =>
    document.createElementNS("http://www.w3.org/2000/svg", name);
  const pathFor = (points) =>
    points
      .map(
        (point, index) =>
          `${index === 0 || point.break ? "M" : "L"}${point.x},${point.y}`,
      )
      .join(" ");

  class GeographicReplay {
    constructor(figure) {
      this.figure = figure;
      this.controls = figure.querySelector("[data-geographic-controls]");
      this.play = figure.querySelector("[data-geographic-play]");
      this.range = figure.querySelector("[data-geographic-position]");
      this.rate = figure.querySelector("[data-geographic-speed]");
      this.status = figure.querySelector("[data-geographic-status]");
      this.readouts = Object.fromEntries(
        ["time", "report", "distance", "groundspeed", "altitude"].map(
          (name) => [name, figure.querySelector(`[data-geographic-${name}]`)],
        ),
      );
      this.elapsed = 0;
      this.playing = false;
      this.frame = 0;
      this.message = "";
      this.play.addEventListener("click", () => this.activate());
      this.range.addEventListener("input", () => {
        this.elapsed = Number(this.range.value);
        this.pause("Replay paused.");
      });
      this.rate.addEventListener("change", () => {
        if (this.playing) this.message = `Playing at ${this.rate.value}×.`;
        this.draw();
      });
      this.controls.hidden = false;
    }

    async load() {
      this.play.setAttribute("aria-disabled", "true");
      this.controls.setAttribute("aria-busy", "true");
      write(this.play, "Loading…");
      write(this.status, "Loading recorded positions…");
      this.controller = new AbortController();
      const timeout = window.setTimeout(() => this.controller.abort(), 15000);
      try {
        const source = new URL(
          this.figure.dataset.geographicReplay,
          window.location.origin,
        );
        if (
          source.origin !== window.location.origin ||
          source.username ||
          source.password ||
          source.search ||
          source.hash ||
          !/^\/event-media\/[a-f0-9]{64}\.json$/.test(source.pathname)
        )
          throw new Error("Replay must be a local recorded asset.");
        const response = await fetch(source, {
          credentials: "omit",
          referrerPolicy: "no-referrer",
          redirect: "error",
          signal: this.controller.signal,
        });
        if (!response.ok) throw new Error("Replay unavailable.");
        const text = await response.text();
        if (text.length > 200000) throw new Error("Replay is too large.");
        const digest = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(text),
        );
        const hash = Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
        if (source.pathname !== `/event-media/${hash}.json`)
          throw new Error("Replay asset changed.");
        const { validateGeographicReplay } =
          await import("./geographic-track.mjs");
        const image = new URL(this.figure.querySelector("img").src);
        if (image.origin !== window.location.origin)
          throw new Error("Replay image must be local.");
        const data = validateGeographicReplay(JSON.parse(text), {
          eventId: this.figure.dataset.replayEventId,
          evidenceVersion: this.figure.dataset.replayEvidenceVersion,
          image: image.pathname,
        });
        if (!this.figure.isConnected) return;
        this.data = data;
        this.duration = data.points.at(-1).t;
        this.started = Date.parse(data.started_at);
        this.range.max = String(this.duration);
        const overlay = svg("svg");
        overlay.classList.add("geographic-replay-overlay");
        overlay.setAttribute("viewBox", `0 0 ${data.width} ${data.height}`);
        overlay.setAttribute("aria-hidden", "true");
        this.flown = svg("path");
        this.flown.classList.add("geographic-replay-flown");
        this.marker = svg("circle");
        this.marker.classList.add("geographic-replay-marker");
        this.marker.setAttribute("r", "13");
        overlay.append(this.flown, this.marker);
        this.figure.querySelector("[data-geographic-stage]").append(overlay);
        for (const name of ["scrubber", "readouts", "note"])
          this.figure.querySelector(`[data-geographic-${name}]`).hidden = false;
        this.motionChanged();
        this.message = reducedMotion.matches
          ? "Replay ready. Advance 30s or use the timeline to step through it."
          : "Replay ready. Press Play to start.";
        this.draw();
      } catch {
        if (this.figure.isConnected) {
          write(this.play, "Retry replay");
          write(
            this.status,
            "Replay unavailable. The static track and written evidence remain available. Try again to reload it.",
          );
        }
      } finally {
        window.clearTimeout(timeout);
        this.controller = null;
        this.play.removeAttribute("aria-disabled");
        this.controls.removeAttribute("aria-busy");
      }
    }

    labelPlay() {
      write(
        this.play,
        reducedMotion.matches
          ? this.elapsed >= this.duration
            ? "Back to start"
            : "Advance 30s"
          : this.playing
            ? "Pause"
            : "Play replay",
      );
    }

    motionChanged() {
      if (!this.data) return;
      if (this.playing) this.pause("Replay paused.");
      this.figure.querySelector("[data-geographic-speed-field]").hidden =
        reducedMotion.matches;
      this.labelPlay();
    }

    draw() {
      if (!this.data) return;
      const points = this.data.points;
      const index = points.findLastIndex((point) => point.t <= this.elapsed);
      const point = points[index];
      const next = points[Math.min(index + 1, points.length - 1)];
      const gap = next.break === true && this.elapsed > point.t;
      const fraction =
        next.t === point.t || gap
          ? 0
          : (this.elapsed - point.t) / (next.t - point.t);
      const x = point.x + (next.x - point.x) * fraction;
      const y = point.y + (next.y - point.y) * fraction;
      this.marker.setAttribute("cx", String(x));
      this.marker.setAttribute("cy", String(y));
      this.marker.setAttribute("visibility", gap ? "hidden" : "visible");
      if (index !== this.flownIndex) {
        this.flownIndex = index;
        this.flownPrefix = pathFor(points.slice(0, index + 1));
      }
      this.flown.setAttribute(
        "d",
        this.flownPrefix + (gap ? "" : ` L${x},${y}`),
      );
      this.range.value = String(this.elapsed);
      const replayTime = utc(this.started + this.elapsed * 1000);
      this.range.setAttribute(
        "aria-valuetext",
        `${replayTime} UTC${gap ? ", data gap" : ""}`,
      );
      write(this.readouts.time, replayTime);
      write(
        this.readouts.report,
        gap ? "Not recorded" : utc(this.started + point.t * 1000),
      );
      write(
        this.readouts.distance,
        gap ? "Not recorded" : `≈ ${point.distance_nm.toFixed(1)} nmi`,
      );
      write(
        this.readouts.groundspeed,
        gap || point.speed === null
          ? "Not recorded"
          : `${Math.round(point.speed)} kn`,
      );
      write(
        this.readouts.altitude,
        gap || point.altitude === null
          ? "Not recorded"
          : `${Math.round(point.altitude).toLocaleString()} ft`,
      );
      write(
        this.figure.querySelector("[data-geographic-altitude-label]"),
        !gap && point.altitude_reference === "barometric"
          ? "Barometric altitude"
          : !gap && point.altitude_reference === "geometric"
            ? "Geometric altitude"
            : "Archived altitude · reference unspecified",
      );
      write(
        this.status,
        gap
          ? "Data gap. No position or readings are recorded at this time."
          : this.message,
      );
    }

    pause(message) {
      this.playing = false;
      cancelAnimationFrame(this.frame);
      this.message = message;
      this.labelPlay();
      this.draw();
    }

    activate() {
      if (this.figure.closest("details:not([open])")) return;
      if (!this.data) {
        if (!this.controller) this.load();
        return;
      }
      if (reducedMotion.matches) {
        this.elapsed =
          this.elapsed >= this.duration
            ? 0
            : Math.min(this.duration, this.elapsed + 30);
        this.pause(
          this.elapsed >= this.duration
            ? "Replay complete."
            : "Stepped through recorded time. Motion is reduced.",
        );
      } else if (this.playing) {
        this.pause("Replay paused.");
      } else {
        if (this.elapsed >= this.duration) this.elapsed = 0;
        this.playing = true;
        this.message = `Playing at ${this.rate.value}×.`;
        this.lastTick = performance.now();
        this.labelPlay();
        this.draw();
        const tick = (now) => {
          if (this.figure.closest("details:not([open])")) {
            this.pause("Replay paused while observation details are closed.");
            return;
          }
          const step = Math.max(
            0,
            Math.min((now - this.lastTick) / 1000, 0.25),
          );
          this.lastTick = now;
          this.elapsed = Math.min(
            this.duration,
            this.elapsed + step * Number(this.rate.value),
          );
          this.draw();
          if (this.elapsed >= this.duration) this.pause("Replay complete.");
          else this.frame = requestAnimationFrame(tick);
        };
        this.frame = requestAnimationFrame(tick);
      }
    }

    dispose() {
      cancelAnimationFrame(this.frame);
      this.controller?.abort();
    }
  }

  const visibility = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const replay = replays.get(entry.target);
      if (!entry.isIntersecting && replay?.playing)
        replay.pause("Replay paused while out of view.");
    }
  });
  function discover(node) {
    if (!(node instanceof Element)) return;
    const figures = node.matches(selector)
      ? [node]
      : node.querySelectorAll(selector);
    for (const figure of figures) {
      if (replays.has(figure)) continue;
      replays.set(figure, new GeographicReplay(figure));
      visibility.observe(figure);
    }
  }
  discover(document.body);
  // Search replaces cards with validated HTML; enhance newly inserted figures
  // without fetching their replay assets or retaining removed card instances.
  new MutationObserver((records) => {
    for (const record of records)
      for (const node of record.addedNodes) discover(node);
    if (
      !records.some((record) =>
        Array.from(record.removedNodes).some((node) => node instanceof Element),
      )
    )
      return;
    for (const [figure, replay] of replays) {
      if (figure.isConnected) continue;
      replay.dispose();
      visibility.unobserve(figure);
      replays.delete(figure);
    }
  }).observe(document.body, { childList: true, subtree: true });
  // Native toggle events do not bubble. Capture closes of any ancestor, while
  // allowing an in-flight load to finish paused and ready for the next opening.
  document.addEventListener(
    "toggle",
    (event) => {
      if (!(event.target instanceof HTMLDetailsElement) || event.target.open)
        return;
      for (const replay of replays.values())
        if (replay.playing && event.target.contains(replay.figure))
          replay.pause("Replay paused while observation details are closed.");
    },
    true,
  );
  document.addEventListener("visibilitychange", () => {
    if (document.hidden)
      for (const replay of replays.values())
        if (replay.playing)
          replay.pause("Replay paused while this page is hidden.");
  });
  reducedMotion.addEventListener("change", () => {
    for (const replay of replays.values()) replay.motionChanged();
  });
})();
