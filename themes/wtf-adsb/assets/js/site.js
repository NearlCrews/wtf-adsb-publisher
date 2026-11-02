(() => {
  "use strict";

  const appearanceButton = document.querySelector("[data-appearance-toggle]");
  const setAppearance = (value) => {
    const appearance = value === "night" ? "night" : "paper";
    document.documentElement.dataset.appearance = appearance;
    if (appearanceButton) {
      appearanceButton.querySelector("[data-appearance-label]").textContent =
        appearance === "night" ? "Day view" : "Night view";
    }
  };
  // appearance.js has already applied any saved choice; label the control to match.
  setAppearance(document.documentElement.dataset.appearance);
  if (appearanceButton) {
    appearanceButton.addEventListener("click", () => {
      const value =
        document.documentElement.dataset.appearance === "night"
          ? "paper"
          : "night";
      setAppearance(value);
      try {
        localStorage.setItem("wtf-appearance", value);
      } catch {
        /* The control works without storage. */
      }
    });
    // Header controls stay hidden until this script can make them work.
    appearanceButton.hidden = false;
  }

  document.querySelectorAll(".topic-navigation a").forEach((link) => {
    if (new URL(link.href).pathname === window.location.pathname)
      link.setAttribute("aria-current", "page");
  });

  // Search reads the theme's own index, so it needs no Ghost API or script.
  function startSearch(button) {
    const dialog = document.createElement("dialog");
    dialog.className = "search-dialog";
    dialog.setAttribute("aria-labelledby", "search-title");
    dialog.innerHTML =
      '<header><h2 id="search-title">Find a story</h2><button type="button" aria-label="Close search">Close ×</button></header><label class="sr-only" for="story-search">Search stories</label><input id="story-search" type="search" placeholder="Try a word such as helicopter or speed" autocomplete="off"><p class="search-count" role="status"></p><ul class="search-results"></ul>';
    document.body.append(dialog);
    const input = dialog.querySelector("input");
    const list = dialog.querySelector("ul");
    const count = dialog.querySelector("[role=status]");
    let stories = null;
    async function loadStories() {
      const response = await fetch(button.dataset.searchIndex, {
        credentials: "omit",
      });
      if (!response.ok) throw new Error("Search unavailable");
      // The index URL-encodes its text so a title cannot break the JSON.
      return (await response.json()).map((entry) => {
        const url = new URL(entry.url, window.location.href);
        if (url.origin !== window.location.origin)
          throw new Error("Search results must link to this site.");
        const title = decodeURIComponent(entry.title);
        const topic = decodeURIComponent(entry.topic);
        return {
          title,
          topic,
          url,
          text: `${title} ${decodeURIComponent(entry.excerpt)} ${topic}`.toLocaleLowerCase(),
        };
      });
    }
    function showResults() {
      if (!stories) return;
      const query = input.value.toLocaleLowerCase().trim();
      const matches = stories.filter((story) => story.text.includes(query));
      list.replaceChildren(
        ...matches.map((story) => {
          const item = document.createElement("li");
          const link = document.createElement("a");
          link.href = story.url;
          const title = document.createElement("strong");
          title.textContent = story.title;
          const topic = document.createElement("small");
          topic.textContent = story.topic;
          link.append(topic, title);
          item.append(link);
          return item;
        }),
      );
      count.textContent = matches.length
        ? `${matches.length} matching ${matches.length === 1 ? "story" : "stories"}`
        : "No stories match. Try a different word.";
    }
    button.addEventListener("click", async () => {
      const journal = document.querySelector(
        '[data-journal][data-search-ready="true"]',
      );
      if (journal) {
        journal.dispatchEvent(
          new CustomEvent("open-event-search", { detail: { opener: button } }),
        );
        return;
      }
      dialog.showModal();
      input.focus();
      if (!stories) {
        count.textContent = "Loading stories…";
        try {
          stories = await loadStories();
        } catch {
          count.textContent =
            "Search is unavailable. You can still browse the story archive.";
        }
      }
      showResults();
    });
    input.addEventListener("input", showResults);
    dialog
      .querySelector("button")
      .addEventListener("click", () => dialog.close());
    // A search field with text would use Escape to clear itself; close the dialog.
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dialog.close();
      }
    });
    dialog.addEventListener("close", () => button.focus());
    button.hidden = false;
  }
  document.querySelectorAll("[data-search-index]").forEach(startSearch);

  const timeLabel = (time) =>
    `${Math.floor(time / 60)
      .toString()
      .padStart(2, "0")}:${Math.floor(time % 60)
      .toString()
      .padStart(2, "0")}`;
  const svgElement = (name) =>
    document.createElementNS("http://www.w3.org/2000/svg", name);

  async function startReplay(figure) {
    const source = new URL(figure.dataset.replaySrc, window.location.href);
    if (source.origin !== window.location.origin)
      throw new Error("Replay data must be served from this site.");
    const response = await fetch(source, {
      credentials: "omit",
      referrerPolicy: "no-referrer",
      redirect: "error",
    });
    if (!response.ok) throw new Error("The replay could not be loaded.");
    const text = await response.text();
    if (text.length > 200000)
      throw new Error("Replay data exceeds the supported size.");
    const data = JSON.parse(text);
    if (
      data.version !== 1 ||
      data.kind !== "schematic" ||
      !Array.isArray(data.points) ||
      data.points.length < 2 ||
      data.points.length > 2000
    )
      throw new Error("Unsupported replay data.");
    let previousTime = -1;
    for (const point of data.points) {
      if (
        ![point.t, point.x, point.y].every(Number.isFinite) ||
        point.t <= previousTime ||
        point.t < 0 ||
        point.t > 86400 ||
        point.x < 0 ||
        point.x > 100 ||
        point.y < 0 ||
        point.y > 60
      )
        throw new Error("Invalid replay point.");
      if (
        [point.speed, point.altitude].some(
          (value) =>
            value !== null && value !== undefined && !Number.isFinite(value),
        )
      )
        throw new Error("Invalid replay measurement.");
      if (point.break !== undefined && typeof point.break !== "boolean")
        throw new Error("Invalid replay gap marker.");
      if (
        point.phase !== undefined &&
        (typeof point.phase !== "string" || point.phase.length > 80)
      )
        throw new Error("Invalid replay phase.");
      previousTime = point.t;
    }
    const points = data.points;
    if (points[0].t !== 0)
      throw new Error("The replay must start at elapsed time zero.");
    const duration = points.at(-1).t;
    const map = figure.querySelector(".replay-map");
    const layer = map.querySelector("[data-track-layer]");
    const base = svgElement("path");
    const flown = svgElement("path");
    const marker = svgElement("path");
    base.setAttribute("class", "track-base");
    flown.setAttribute("class", "track-flown");
    marker.setAttribute("class", "replay-plane");
    marker.setAttribute("d", "M0 -1.8 L0.8 0.7 L0 0.2 L-0.8 0.7 Z");
    marker.setAttribute("aria-hidden", "true");
    const pathFor = (list) =>
      list
        .map(
          (p, index) =>
            `${index === 0 || p.break === true ? "M" : "L"}${p.x},${p.y}`,
        )
        .join(" ");
    base.setAttribute("d", pathFor(points));
    layer.append(base, flown, marker);
    const controls = figure.querySelector(".replay-controls");
    const play = controls.querySelector("[data-replay-play]");
    const range = controls.querySelector("input[type=range]");
    const speed = controls.querySelector("select");
    const status = figure.querySelector("[data-replay-status]");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let elapsed = 0;
    let playing = false;
    let frame = 0;
    let lastTick = 0;
    range.max = String(duration);
    range.step = "any";
    const readouts = {
      time: figure.querySelector("[data-replay-time]"),
      speed: figure.querySelector("[data-replay-speed]"),
      altitude: figure.querySelector("[data-replay-altitude]"),
      phase: figure.querySelector("[data-replay-phase]"),
    };
    // Playback redraws every frame; text changes far less often.
    const write = (node, text) => {
      if (node.textContent !== text) node.textContent = text;
    };
    let flownIndex = -1;
    let flownPrefix = "";
    const labelPlay = () => {
      play.textContent = !reducedMotion.matches
        ? playing
          ? "Pause"
          : "Play replay"
        : elapsed >= duration
          ? "Back to start"
          : "Advance 30s";
    };

    function draw() {
      const index = points.findLastIndex((p) => p.t <= elapsed);
      const point = points[index];
      const next = points[Math.min(index + 1, points.length - 1)];
      const gap = next.break === true && elapsed > point.t && elapsed < next.t;
      const fraction =
        next.t === point.t || gap
          ? 0
          : (elapsed - point.t) / (next.t - point.t);
      const current = {
        x: point.x + (next.x - point.x) * fraction,
        y: point.y + (next.y - point.y) * fraction,
      };
      if (index !== flownIndex) {
        flownIndex = index;
        flownPrefix = pathFor(points.slice(0, index + 1));
      }
      flown.setAttribute(
        "d",
        gap ? flownPrefix : `${flownPrefix} L${current.x},${current.y}`,
      );
      const headingStart =
        index === points.length - 1 ? points[index - 1] : point;
      const angle =
        (Math.atan2(next.y - headingStart.y, next.x - headingStart.x) * 180) /
          Math.PI +
        90;
      marker.setAttribute(
        "transform",
        `translate(${current.x} ${current.y}) rotate(${angle})`,
      );
      marker.style.display = gap ? "none" : "";
      range.value = String(elapsed);
      const time = timeLabel(elapsed);
      const position = `${time} of ${timeLabel(duration)}${gap ? ", no position available" : ""}`;
      if (range.getAttribute("aria-valuetext") !== position)
        range.setAttribute("aria-valuetext", position);
      write(readouts.time, time);
      write(
        readouts.speed,
        !gap && Number.isFinite(point.speed)
          ? `${Math.round(point.speed)} kn`
          : "—",
      );
      write(
        readouts.altitude,
        !gap && Number.isFinite(point.altitude)
          ? `${Math.round(point.altitude).toLocaleString()} ft`
          : "—",
      );
      write(
        readouts.phase,
        gap ? "Gap in the record" : String(point.phase || "Flight"),
      );
    }

    const interrupted = "Replay paused.";
    const pausedAt = () => `Paused at ${timeLabel(elapsed)}.`;
    function stop(message) {
      playing = false;
      cancelAnimationFrame(frame);
      labelPlay();
      status.textContent = message;
    }
    function tick(now) {
      // A frame's timestamp can precede the click that started the replay.
      const step = Math.max(0, Math.min((now - lastTick) / 1000, 0.25));
      elapsed = Math.min(duration, elapsed + step * Number(speed.value));
      lastTick = now;
      draw();
      if (elapsed >= duration) stop("Replay complete.");
      else frame = requestAnimationFrame(tick);
    }
    play.addEventListener("click", () => {
      if (reducedMotion.matches) {
        const atEnd = elapsed >= duration;
        elapsed = atEnd ? 0 : Math.min(duration, elapsed + 30);
        draw();
        labelPlay();
        status.textContent = atEnd
          ? "Back at the start."
          : `Advanced to ${timeLabel(elapsed)}.`;
      } else if (playing) {
        stop(pausedAt());
      } else {
        if (elapsed >= duration) elapsed = 0;
        playing = true;
        lastTick = performance.now();
        labelPlay();
        status.textContent = "Replay playing.";
        frame = requestAnimationFrame(tick);
      }
    });
    range.addEventListener("input", () => {
      elapsed = Number(range.value);
      // The slider announces its own position. Say once that a drag stopped the
      // replay, keep that through the drag, and drop any message the move made stale.
      stop(playing || status.textContent === interrupted ? interrupted : "");
      draw();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && playing) stop(pausedAt());
    });
    reducedMotion.addEventListener("change", () => {
      if (playing) stop(pausedAt());
      else labelPlay();
    });
    // Draw before revealing the controls: a failure here must leave only the error message.
    labelPlay();
    draw();
    controls.hidden = false;
    play.disabled = false;
  }

  document.querySelectorAll("[data-replay-src]").forEach((figure) => {
    startReplay(figure).catch(() => {
      const message = figure.querySelector("[data-replay-status]");
      if (!message) return;
      message.className = "replay-error";
      message.textContent =
        "The replay is unavailable. The illustrated track and written evidence are still available above.";
    });
  });
})();
