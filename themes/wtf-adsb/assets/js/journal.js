(async () => {
  const root = document.querySelector("[data-journal]");
  if (!root || !root.querySelector("[data-keyword-filter]")) return;
  const status = root.querySelector("[data-journal-status]");
  try {
    const model = await import("./event-query.mjs");
    const config = JSON.parse(
      root.querySelector("[data-journal-config]").textContent,
    );
    const generation = config.version === 1 ? "" : `${config.presentation}/`;
    if (
      !model.SUPPORTED_QUERY_VERSIONS.includes(config.version) ||
      (config.version === 2 && !/^[a-f0-9]{64}$/.test(config.presentation)) ||
      !/^[a-f0-9]{16,64}$/.test(config.snapshot) ||
      config.base !==
        `/event-data/${config.snapshot}/query-v${config.version}/${generation}` ||
      config.pageBase !==
        `/event-pages/${config.snapshot}/query-v${config.version}/${generation}`
    )
      throw new Error("Unsupported search snapshot");
    const form = root.querySelector("[data-event-filters]");
    const dialog = root.querySelector("[data-filter-dialog]");
    const dialogStatus = root.querySelector("[data-filter-status]");
    const applyButton = form.querySelector('[type="submit"]');
    const feed = root.querySelector("[data-event-feed]");
    const keywords = form.querySelector("[data-keyword-filter]");
    const aircraft = form.querySelector("[data-aircraft-filter]");
    const source = form.querySelector("[data-source-filter]");
    const airportPatterns = form.querySelector("[data-airport-pattern-filter]");
    const from = form.querySelector("[data-range-from]");
    const to = form.querySelector("[data-range-to]");
    let size = root.querySelector("[data-page-size]");
    const typeButtons = form.querySelectorAll("[data-type]");
    const chart = root.querySelector("[data-chart-buckets]");
    const chartTable = root.querySelector("[data-chart-table]");
    const chartLabels = [
      "[data-chart-max]",
      "[data-range-label]",
      "[data-chart-range]",
      "[data-chart-aggregation]",
    ].map((selector) => root.querySelector(selector));
    const dailyChart = root.querySelector(".daily-chart");
    const partialKey = root.querySelector("[data-partial-key]");
    const { dateLabel } = model;
    const rangeLabel = (start, end) =>
      start === end
        ? dateLabel(start)
        : `${dateLabel(start)} – ${dateLabel(end)}`;
    const node = (tag, className = "", text = "") => {
      const element = document.createElement(tag);
      if (className) element.className = className;
      if (text) element.textContent = text;
      return element;
    };
    let state = model.validateQuery(config.initial, config);
    let intended = { ...state };
    let pending;
    let request = 0;
    let typing;
    let committedStatus = status.textContent;
    let dialogOpener;
    let appliedClose = false;
    const cardCache = new Map();
    // The story archive lists every story: a view of its own beside the query
    // views. The page draws its first page at the static size, kept as drawn;
    // the script draws its other pages and sizes from the snapshot's archive
    // entries. An archive view is the archive's query (the default one, which
    // the filters show) at a size and page, known by identity, so a search or
    // range built from it is an ordinary query; the archive's address, with
    // at most a size and page, shows the archive again. The story archive is
    // known by its class: the all-history query page's view key is "archive"
    // too, and it pages as a query.
    const archive = feed.querySelector(".archive-feed")
      ? { path: window.location.pathname, query: state, view: null }
      : null;
    const archiveViews = new WeakSet();
    const isArchive = (query) =>
      query === archive?.query || archiveViews.has(query);
    function archivePage(pageSize, page) {
      if (!model.PAGE_SIZES.includes(pageSize))
        throw new Error(`Choose ${model.PAGE_SIZE_CHOICES} stories per page.`);
      const pages = Math.max(1, Math.ceil(config.eventCount / pageSize));
      if (!Number.isSafeInteger(page) || page < 1 || page > pages)
        throw new Error(
          `At ${pageSize} stories per page, the archive has ${pages} page${pages === 1 ? "" : "s"}.`,
        );
      if (pageSize === archive.query.size && page === 1) return archive.query;
      const query = { ...archive.query, size: pageSize, page };
      archiveViews.add(query);
      return query;
    }
    // Another page of the view a query belongs to.
    const pageOf = (query, page) =>
      isArchive(query) ? archivePage(query.size, page) : { ...query, page };
    const sameQuery = (a, b) =>
      a === b ||
      (isArchive(a) === isArchive(b) &&
        JSON.stringify(a) === JSON.stringify(b));

    function cancelSelection() {
      window.clearTimeout(typing);
      pending?.abort();
      feed.removeAttribute("aria-busy");
      form.removeAttribute("aria-busy");
      applyButton.disabled = false;
      return ++request;
    }

    function syncControls(query = state, updateChart = true) {
      keywords.value = query.q;
      aircraft.value = query.aircraft;
      if (source) source.value = query.source;
      if (airportPatterns) airportPatterns.checked = query.hideAirportPatterns;
      from.value = query.from;
      to.value = query.to;
      size.value = String(query.size);
      for (const button of typeButtons)
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.type === query.type),
        );
      for (const button of form.querySelectorAll("[data-range-preset]")) {
        const range = model.presetRange(button.dataset.rangePreset, config);
        const requestedDays = Number(button.dataset.rangePreset);
        button.disabled =
          Number.isFinite(requestedDays) &&
          (model.dayTime(range.to) - model.dayTime(range.from)) / model.DAY_MS +
            1 <
            requestedDays;
        button.title = button.disabled
          ? "This snapshot does not extend that far back. Use All available history."
          : "";
        button.setAttribute(
          "aria-pressed",
          String(
            !button.disabled &&
              range.from === query.from &&
              range.to === query.to,
          ),
        );
      }
      if (!updateChart) return;
      for (const button of chart.querySelectorAll("[data-day]")) {
        button.disabled = button.closest(".is-unavailable") !== null;
        button.setAttribute(
          "aria-pressed",
          String(
            state.from === button.dataset.day &&
              state.to === button.dataset.rangeEnd,
          ),
        );
      }
    }

    // The controls, summary and status show the committed view again.
    function resync() {
      intended = { ...state };
      syncControls();
      syncSummary();
      status.textContent = committedStatus;
    }

    function syncSummary() {
      if (!dialog) return;
      const defaults = model.defaultQuery(config);
      const customRange =
        state.from !== defaults.from || state.to !== defaults.to;
      const labels = [
        customRange ? rangeLabel(state.from, state.to) : "Last 30 days",
        config.types.find((type) => type.id === state.type)?.label ||
          "All event types",
      ];
      if (state.q)
        labels.push(
          `“${state.q.length > 36 ? state.q.slice(0, 36) + "…" : state.q}”`,
        );
      if (state.aircraft)
        labels.push(
          config.aircraftChoices.find(
            (choice) => choice.value === state.aircraft,
          )?.label || state.aircraft,
        );
      if (state.source)
        labels.push(
          `${model.SOURCES.find((family) => family.id === state.source)?.label || state.source} readings`,
        );
      if (state.hideAirportPatterns)
        labels.push("Airport-pattern candidates hidden");
      const count = [
        customRange,
        state.q,
        state.type,
        state.aircraft,
        state.source,
        state.hideAirportPatterns,
      ].filter(Boolean).length;
      root.querySelector("[data-filter-summary]").textContent =
        labels.join(" · ");
      const badge = root.querySelector("[data-filter-count]");
      badge.textContent = String(count);
      badge.hidden = !count;
      root
        .querySelector("[data-open-filters]")
        .setAttribute(
          "aria-label",
          count ? `Search and filters, ${count} active` : "Search and filters",
        );
      root.querySelector("[data-clear-active-filters]").hidden = !count;
    }

    function closeFilters(applied = false) {
      if (!dialog?.open) return;
      appliedClose = applied;
      if (!applied) {
        cancelSelection();
        resync();
      }
      dialog.close();
    }

    function openFilters(opener) {
      if (dialog.open) return;
      const wasPending = feed.hasAttribute("aria-busy");
      cancelSelection();
      if (wasPending) window.history.replaceState(null, "", urlFor(state));
      resync();
      dialogStatus.textContent = "";
      dialogOpener = opener;
      appliedClose = false;
      dialog.showModal();
      (window.matchMedia("(max-width: 600px), (pointer: coarse)").matches
        ? dialog.querySelector("h2")
        : keywords
      ).focus();
    }
    function controls() {
      return {
        ...intended,
        q: keywords.value,
        aircraft: aircraft.value,
        source: source?.value || "",
        hideAirportPatterns: airportPatterns?.checked || false,
        from: from.value,
        to: to.value,
        size: Number(size.value),
        page: 1,
      };
    }
    function urlFor(query) {
      const url = new URL(window.location.href);
      url.hash = "";
      if (isArchive(query)) {
        url.pathname = archive.path;
        const params = new URLSearchParams();
        if (query.size !== archive.query.size)
          params.set("size", String(query.size));
        if (query.page !== 1) params.set("page", String(query.page));
        url.search = params.toString();
      } else {
        url.pathname = `${config.pageBase}all/1/`;
        url.search = model.queryParams(query).toString();
      }
      return url;
    }
    async function json(path, signal, limit) {
      const url = new URL(path, window.location.origin);
      if (
        url.origin !== window.location.origin ||
        !url.pathname.startsWith(config.base)
      )
        throw new Error("Invalid snapshot path");
      const response = await fetch(url, {
        signal,
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      if (!response.ok) throw new Error("Snapshot unavailable");
      const text = await response.text();
      if (text.length > limit) throw new Error("Oversized snapshot");
      return JSON.parse(text);
    }
    // One of the snapshot's data files with a list for every event, fetched
    // once and checked by accept(); a failed load can be tried again.
    function snapshotList(name, key, accept) {
      let promise;
      return () => {
        if (!promise)
          promise = (async () => {
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 15000);
            try {
              const data = await json(
                `${config.base}${name}`,
                controller.signal,
                32000000,
              );
              if (
                data.version !== config.version ||
                (config.version === 2 &&
                  data.presentation !== config.presentation) ||
                data.snapshot_id !== config.snapshot ||
                !Array.isArray(data[key]) ||
                data[key].length !== config.eventCount
              )
                throw new Error("Snapshot mismatch");
              const ids = new Set();
              for (const item of data[key]) {
                if (
                  !item ||
                  !/^[a-f0-9-]{36}$/.test(item.id) ||
                  ids.has(item.id) ||
                  typeof item.observed_start !== "string" ||
                  !Number.isFinite(Date.parse(item.observed_start)) ||
                  Date.parse(item.observed_start) > Date.parse(config.as_of_utc)
                )
                  throw new Error("Invalid snapshot record");
                ids.add(item.id);
              }
              return accept(data[key]);
            } finally {
              window.clearTimeout(timeout);
            }
          })().catch((error) => {
            promise = null;
            throw error;
          });
        return promise;
      };
    }
    const aircraftFields = new Set([
      "registration",
      "callsign",
      "icao_address",
      "type_code",
      "type_description",
    ]);
    const index = snapshotList("index.json", "events", (events) => {
      for (const event of events)
        if (
          !config.types.some((type) => type.id === event.primary_type) ||
          ![event.title, event.summary, event.terms].every(
            (value) => typeof value === "string",
          ) ||
          !event.aircraft ||
          (config.version === 2 &&
            typeof event.airport_pattern_candidate !== "boolean") ||
          Array.isArray(event.aircraft) ||
          !Object.entries(event.aircraft).every(
            ([key, value]) =>
              aircraftFields.has(key) && typeof value === "string",
          )
        )
          throw new Error("Invalid search record");
      return model.prepareRecords(events, config.types);
    });
    // The archive's entries, newest first, as the static list draws them;
    // each entry's markup is checked when its page is drawn.
    const archiveStories = snapshotList(
      "archive.json",
      "stories",
      (stories) => {
        if (stories.some((story) => typeof story.html !== "string"))
          throw new Error("Invalid archive entry");
        return stories;
      },
    );
    function cardElement(html, id, lead, selector = "article.event-card") {
      if (typeof html !== "string" || html.length > 100000)
        throw new Error("Invalid card");
      const template = document.createElement("template");
      template.innerHTML = html;
      const card = template.content.firstElementChild;
      if (
        template.content.children.length !== 1 ||
        !card?.matches(selector) ||
        card.dataset.eventId !== id ||
        card.classList.contains("event-lead") !== lead ||
        card.querySelector(
          "script, iframe, object, embed, style, link, form, base, meta, template",
        )
      )
        throw new Error("Invalid card content");
      // Cards use HTML controls and local images; maps load through img, not SVG markup.
      for (const element of [card, ...card.querySelectorAll("*")]) {
        if (element.namespaceURI !== "http://www.w3.org/1999/xhtml")
          throw new Error("Invalid card content");
        for (const attr of element.attributes) {
          if (
            /^on/i.test(attr.name) ||
            [
              "srcdoc",
              "style",
              "srcset",
              "imagesrcset",
              "xlink:href",
              "poster",
              "ping",
              "background",
              "action",
              "formaction",
            ].includes(attr.name)
          )
            throw new Error("Unsafe card attribute");
          if (["href", "src"].includes(attr.name)) {
            const url = new URL(attr.value, window.location.origin);
            const local =
              url.origin === window.location.origin &&
              ["http:", "https:"].includes(url.protocol);
            const referenceAnchor =
              attr.name === "href" && element.matches(".event-references a");
            const reference =
              referenceAnchor &&
              ["external", "noopener", "noreferrer"].every((token) =>
                element.relList.contains(token),
              ) &&
              url.protocol === "https:" &&
              !url.username &&
              !url.password &&
              !url.port &&
              /^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i.test(url.hostname) &&
              !model.privateHost(url.hostname);
            if (
              url.username ||
              url.password ||
              (referenceAnchor ? !reference : !local)
            )
              throw new Error("Unsafe card link");
          }
        }
      }
      return card;
    }
    async function cardsFor(result, signal) {
      const cards = Array(result.records.length);
      let cursor = 0;
      await Promise.all(
        Array.from({ length: Math.min(8, cards.length) }, async () => {
          while (cursor < result.records.length) {
            const position = cursor++;
            const record = result.records[position];
            let data = cardCache.get(record.id);
            if (!data) {
              data = await json(
                `${config.base}cards/${record.id}.json`,
                signal,
                220000,
              );
              if (
                data.version !== config.version ||
                (config.version === 2 &&
                  data.presentation !== config.presentation) ||
                data.snapshot_id !== config.snapshot ||
                data.id !== record.id
              )
                throw new Error("Card snapshot mismatch");
              cardElement(data.lead, record.id, true);
              cardElement(data.row, record.id, false);
              cardCache.set(record.id, data);
              if (cardCache.size > 500)
                cardCache.delete(cardCache.keys().next().value);
            }
            cards[position] = cardElement(
              result.query.page === 1 && position === 0 ? data.lead : data.row,
              record.id,
              result.query.page === 1 && position === 0,
            );
          }
        }),
      );
      return cards;
    }
    function feedView(result, cards) {
      const query = result.query;
      const section = node("section", "event-feed");
      section.setAttribute("aria-labelledby", "feed-heading");
      section.dataset.view = "search";
      section.dataset.page = String(query.page);
      const headingRow = node("div", "feed-heading");
      const headingCopy = node("div");
      const type = config.types.find((item) => item.id === query.type);
      const heading = node(
        "h2",
        "",
        `${type?.label || "All events"} · ${rangeLabel(query.from, query.to)}`,
      );
      heading.id = "feed-heading";
      heading.tabIndex = -1;
      const offset = (query.page - 1) * query.size;
      const resultText = result.count
        ? `Showing ${offset + 1}–${offset + cards.length} of ${result.count} matching events.`
        : "0 matching events.";
      headingCopy.append(heading, node("p", "", resultText));
      const latest = node("a", "latest-snapshot", "Latest archive");
      latest.href = "/stories/";
      const actions = node("div", "feed-actions");
      if (dialog) actions.append(pageSizeField(query));
      actions.append(latest);
      headingRow.append(headingCopy, actions);
      section.append(headingRow);
      if (result.pages > 1) section.append(pageNav(query, result.pages, true));
      let first = 0;
      if (query.page === 1 && cards.length) {
        section.append(
          node("p", "eyebrow featured-label", "Latest matching event"),
          cards[0],
        );
        first = 1;
      }
      const rows = node("div", "event-rows");
      let previousDay = "";
      for (let i = first; i < cards.length; i += 1) {
        const day = result.records[i].observed_start.slice(0, 10);
        if (day !== previousDay)
          rows.append(node("h3", "event-date-group", dateLabel(day)));
        rows.append(cards[i]);
        previousDay = day;
      }
      section.append(rows);
      if (!result.count)
        section.append(
          node(
            "p",
            "event-empty",
            "No matching events are recorded in this snapshot. Try fewer search terms or a wider date range. This does not mean no aircraft were present.",
          ),
        );
      if (result.pages > 1) section.append(pageNav(query, result.pages, false));
      return section;
    }
    // A view's page-size picker: the archive counts stories, a search its
    // results.
    function pageSizeField(query) {
      const field = size.closest(".page-size-field").cloneNode(true);
      field.hidden = false;
      field.querySelector("label").textContent = isArchive(query)
        ? "Stories per page"
        : "Results per page";
      const select = field.querySelector("select");
      // A link from a page kept from an earlier snapshot can name a size this
      // page no longer offers.
      if (
        ![...select.options].some((item) => item.value === String(query.size))
      )
        select.append(new Option(String(query.size)));
      select.value = String(query.size);
      return field;
    }
    // A page link's address, root-relative like the static pages' links.
    const pageAddress = (query, page) => {
      const url = urlFor(pageOf(query, page));
      return url.pathname + url.search;
    };
    // The page links above (top) or below a view's list, as the partials
    // page-steps and page-numbers draw them on the static pages. A step past
    // the first or last page stays in place as a link without an address.
    function pageNav(query, pages, top) {
      const stories = isArchive(query);
      const label = stories ? "Story pages" : "Event pages";
      const noun = stories ? "stories" : "events";
      const nav = node(
        "nav",
        `pagination ${top ? "page-steps" : "page-numbers"}`,
      );
      nav.setAttribute("aria-label", top ? `${label}, top of list` : label);
      const step = (direction) => {
        const previous = direction === "previous";
        const target = query.page + (previous ? -1 : 1);
        const link = node("a", `page-step page-${previous ? "prev" : "next"}`);
        link.setAttribute(
          "aria-label",
          previous
            ? `Previous page (newer ${noun})`
            : `Next page (older ${noun})`,
        );
        const arrow = node("span", "", previous ? "←" : "→");
        arrow.setAttribute("aria-hidden", "true");
        if (previous) link.append(arrow, " Previous");
        else link.append("Next ", arrow);
        if (target < 1 || target > pages) {
          link.setAttribute("role", "link");
          link.setAttribute("aria-disabled", "true");
        } else {
          link.setAttribute("href", pageAddress(query, target));
          link.rel = previous ? "prev" : "next";
          link.dataset.eventPage = direction;
        }
        return link;
      };
      nav.append(step("previous"));
      if (top)
        nav.append(
          node("span", "page-status", `Page ${query.page} of ${pages}`),
        );
      else {
        const list = node("ol");
        for (const number of model.pageNumbers(query.page, pages)) {
          const item = node("li");
          if (number === null) {
            item.className = "page-gap";
            item.setAttribute("aria-hidden", "true");
            item.textContent = "…";
          } else if (number === query.page) {
            const current = node("span", "", String(number));
            current.setAttribute("aria-current", "page");
            item.append(current);
          } else {
            const link = node("a", "", String(number));
            link.setAttribute("href", pageAddress(query, number));
            link.dataset.eventPage = String(number);
            item.append(link);
          }
          list.append(item);
        }
        nav.append(list);
      }
      nav.append(step("next"));
      return nav;
    }
    // An archive page the script draws: the drawn first page's heading and
    // chart, this page's entries under their UTC dates, and its page links.
    function archiveView(stories, query) {
      const pages = Math.max(1, Math.ceil(stories.length / query.size));
      const offset = (query.page - 1) * query.size;
      const shown = stories.slice(offset, offset + query.size);
      const section = node("section", "event-feed archive-feed");
      section.setAttribute("aria-labelledby", "feed-heading");
      section.dataset.view = "archive";
      section.dataset.page = String(query.page);
      const headingRow = archive.view.feed[0]
        .querySelector(".feed-heading")
        .cloneNode(true);
      headingRow.querySelector("#feed-heading + p").textContent =
        model.archiveResult(offset, shown.length, stories.length);
      const field = headingRow.querySelector(".page-size-field");
      if (field && dialog) field.replaceWith(pageSizeField(query));
      section.append(headingRow);
      if (pages > 1) section.append(pageNav(query, pages, true));
      const days = node("div", "story-days");
      let day;
      let date = "";
      for (const story of shown) {
        if (story.observed_start.slice(0, 10) !== date) {
          date = story.observed_start.slice(0, 10);
          day = node("div", "story-day");
          const dayHeading = node("h3", "story-day-heading");
          const time = node("time", "", dateLabel(date));
          time.dateTime = date;
          dayHeading.append(time);
          day.append(dayHeading);
          days.append(day);
        }
        day.append(
          cardElement(
            story.html,
            story.id,
            false,
            "article.event-card.archive-entry",
          ),
        );
      }
      section.append(days);
      if (pages > 1) section.append(pageNav(query, pages, false));
      return {
        ...archive.view,
        feed: [section],
        status: `${stories.length} published stor${stories.length === 1 ? "y" : "ies"}. Page ${query.page} of ${pages}.`,
      };
    }
    // The chart's part of a view: bars, table rows, labels, and scale.
    function chartView(records, query) {
      const data = model.timeline(records, query, config);
      const bars = document.createDocumentFragment();
      const table = document.createDocumentFragment();
      const ticks = new Set([
        0,
        Math.floor((data.buckets.length - 1) / 2),
        data.buckets.length - 1,
      ]);
      for (const [i, bucket] of data.buckets.entries()) {
        const column = node(
          "div",
          `chart-day${bucket.unavailable ? " is-unavailable" : ""}${bucket.partial ? " is-partial" : ""}${bucket.incomplete ? " is-incomplete" : ""}`,
        );
        column.dataset.chartDate = bucket.from;
        const button = node("button", "day-bar");
        button.type = "button";
        button.dataset.day = bucket.from;
        button.dataset.rangeEnd = bucket.to;
        button.disabled = bucket.unavailable;
        const detail = `${rangeLabel(bucket.from, bucket.to)}: ${bucket.total === null ? "archive unavailable" : `${bucket.total} matching event${bucket.total === 1 ? "" : "s"}`}${bucket.partial ? "; includes partial day" : ""}${bucket.incomplete ? "; archive incomplete" : ""}`;
        button.setAttribute("aria-label", detail);
        button.title = detail;
        button.setAttribute(
          "aria-pressed",
          String(query.from === bucket.from && query.to === bucket.to),
        );
        for (const type of config.types) {
          if (bucket.unavailable || (query.type && type.id !== query.type))
            continue;
          const segment = node("span", `chart-segment type-${type.id}`);
          segment.dataset.segmentType = type.id;
          segment.setAttribute("aria-hidden", "true");
          segment.style.height = `${(bucket.counts[type.id] * 100) / data.max}%`;
          button.append(segment);
        }
        const tick = node(
          "span",
          "chart-tick",
          ticks.has(i) ? dateLabel(bucket.from).replace(/ \d{4}$/, "") : "",
        );
        tick.setAttribute("aria-hidden", "true");
        column.append(button, tick);
        bars.append(column);
        const row = node("tr");
        const label = node(
          "th",
          "",
          `${rangeLabel(bucket.from, bucket.to)}${bucket.partial ? " (partial day)" : ""}${bucket.incomplete ? " (archive incomplete)" : ""}`,
        );
        label.scope = "row";
        row.append(label);
        if (bucket.unavailable) {
          const cell = node("td", "", "Archive unavailable");
          cell.colSpan = config.types.length + 1;
          row.append(cell);
        } else {
          row.append(node("td", "", String(bucket.total)));
          for (const type of config.types)
            row.append(node("td", "", String(bucket.counts[type.id])));
        }
        table.append(row);
      }
      return {
        bars: [bars],
        table: [table],
        labels: [
          String(data.max),
          `${data.dayCount}-day record`,
          `${rangeLabel(query.from, query.to)} · UTC`,
          `${{ day: "Daily counts", week: "Counts in 7-day groups", month: "Monthly counts", year: "Yearly counts" }[data.unit]} · Counts match the current filters.`,
        ],
        scale: String(data.max),
        partialHidden: !data.buckets.some((bucket) => bucket.partial),
      };
    }
    // Draws a view: the feed, the chart, its table, and its labels.
    function paint(view) {
      feed.replaceChildren(...view.feed);
      size = root.querySelector("[data-page-size]");
      chart.replaceChildren(...view.bars);
      chartTable.replaceChildren(...view.table);
      chartLabels.forEach((element, index) => {
        element.textContent = view.labels[index];
      });
      dailyChart.dataset.chartScale = view.scale;
      partialKey.hidden = view.partialHidden;
    }
    async function select(
      input,
      { history = true, focus = "", modal = false } = {},
    ) {
      const current = cancelSelection();
      let desired;
      try {
        desired = isArchive(input) ? input : model.validateQuery(input, config);
      } catch (error) {
        if (!modal) intended = { ...state };
        status.textContent = error.message;
        if (modal) dialogStatus.textContent = error.message;
        return;
      }
      intended = { ...desired };
      syncControls(desired, false);
      const controller = new AbortController();
      pending = controller;
      const timeout = window.setTimeout(() => controller.abort(), 20000);
      status.textContent = isArchive(desired)
        ? "Loading stories…"
        : "Searching events…";
      if (modal) {
        dialogStatus.textContent = "Searching observations…";
        form.setAttribute("aria-busy", "true");
        if (document.activeElement === applyButton)
          dialog.querySelector("h2").focus();
        applyButton.disabled = true;
      }
      feed.setAttribute("aria-busy", "true");
      try {
        let view;
        if (desired === archive?.query) view = archive.view;
        else if (isArchive(desired)) {
          const stories = await archiveStories();
          if (request !== current) return;
          view = archiveView(stories, desired);
        } else {
          const records = await index();
          if (request !== current) return;
          const result = model.queryPage(records, desired, config);
          const cards = await cardsFor(result, controller.signal);
          view = {
            feed: [feedView(result, cards)],
            ...chartView(records, desired),
            status: `${result.count} matching event${result.count === 1 ? "" : "s"}. Page ${desired.page} of ${result.pages}.`,
          };
        }
        if (request !== current) return;
        if (controller.signal.aborted) throw new Error("Search timed out");
        paint(view);
        state = desired;
        committedStatus = view.status;
        resync();
        if (history) window.history.pushState(null, "", urlFor(state));
        if (modal) closeFilters(true);
        if (focus === "feed") feed.querySelector("#feed-heading").focus();
        // A new size keeps the reader on the picker the view redrew.
        if (focus === "size") size.focus();
        if (focus === "range") from.focus();
      } catch {
        controller.abort();
        if (request !== current) return;
        if (!modal) resync();
        if (!history) window.history.replaceState(null, "", urlFor(state));
        status.textContent =
          "The requested results could not be loaded. The previous view is still shown; its snapshot time is listed below the chart. Try the filters again.";
        if (modal)
          dialogStatus.textContent =
            "Results could not be loaded. Your previous results are unchanged. Try again or close search.";
      } finally {
        window.clearTimeout(timeout);
        if (request === current) {
          feed.removeAttribute("aria-busy");
          form.removeAttribute("aria-busy");
          applyButton.disabled = false;
        }
      }
    }
    form.hidden = false;
    size.closest(".page-size-field").hidden = false;
    syncControls();
    syncSummary();
    // The archive as the page drew it; paint() can draw it again.
    if (archive)
      archive.view = {
        feed: [...feed.children],
        bars: [...chart.children],
        table: [...chartTable.children],
        labels: chartLabels.map((element) => element.textContent),
        scale: dailyChart.dataset.chartScale,
        partialHidden: partialKey.hidden,
        status: committedStatus,
      };
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      select(controls(), dialog ? { modal: true, focus: "feed" } : {});
    });
    keywords.addEventListener("input", () => {
      cancelSelection();
      if (dialog) {
        dialogStatus.textContent = "";
        status.textContent = committedStatus;
        return;
      }
      status.textContent = "Searching events…";
      typing = window.setTimeout(() => select(controls()), 250);
    });
    function changeDraft(query) {
      if (!dialog) return select(query);
      cancelSelection();
      intended = { ...query };
      syncControls(query, false);
      dialogStatus.textContent = "";
      status.textContent = committedStatus;
    }
    for (const input of [aircraft, source, from, to, airportPatterns].filter(
      Boolean,
    ))
      input.addEventListener("change", () => changeDraft(controls()));
    // A new page size shows the page that holds the first entry shown. The
    // earlier inline form applies its pending edits too, from page one.
    root.addEventListener("change", (event) => {
      if (!event.target.matches("[data-page-size]")) return;
      const pageSize = Number(event.target.value);
      const page = model.pageAfterResize(state.page, state.size, pageSize);
      let query;
      try {
        query = !dialog
          ? { ...controls(), size: pageSize, page: 1 }
          : isArchive(state)
            ? archivePage(pageSize, page)
            : { ...state, size: pageSize, page };
      } catch (error) {
        status.textContent = error.message;
        return;
      }
      select(query, { focus: "size" });
    });
    for (const button of typeButtons)
      button.addEventListener("click", () =>
        changeDraft({ ...controls(), type: button.dataset.type }),
      );
    for (const button of form.querySelectorAll("[data-range-preset]"))
      button.addEventListener("click", () =>
        changeDraft({
          ...controls(),
          ...model.presetRange(button.dataset.rangePreset, config),
        }),
      );
    form
      .querySelector("[data-clear-filters]")
      .addEventListener("click", () =>
        changeDraft({ ...model.defaultQuery(config), size: state.size }),
      );
    if (dialog) {
      const launcher = root.querySelector("[data-open-filters]");
      launcher.addEventListener("click", () => openFilters(launcher));
      root.addEventListener("open-event-search", (event) =>
        openFilters(event.detail.opener),
      );
      root
        .querySelector("[data-close-filters]")
        .addEventListener("click", () => closeFilters());
      dialog.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          closeFilters();
        }
        if (event.key === "Tab") {
          const focusable = [
            ...dialog.querySelectorAll(
              "button, input, select, a[href], [tabindex]",
            ),
          ].filter(
            (element) =>
              !element.disabled &&
              element.tabIndex >= 0 &&
              element.getClientRects().length,
          );
          const first = focusable[0];
          const last = focusable.at(-1);
          if (
            event.shiftKey &&
            (document.activeElement === first ||
              document.activeElement === dialog.querySelector("h2"))
          ) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }
      });
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        closeFilters();
      });
      dialog.addEventListener("close", () => {
        if (!appliedClose) dialogOpener?.focus();
        appliedClose = false;
      });
      root
        .querySelector("[data-clear-active-filters]")
        .addEventListener("click", () =>
          select(
            { ...model.defaultQuery(config), size: state.size },
            { focus: "feed" },
          ),
        );
      root.querySelector("[data-search-toolbar]").hidden = false;
      root.dataset.searchReady = "true";
    }
    chart.addEventListener("click", (event) => {
      const button = event.target.closest("[data-day]");
      if (button && !button.disabled)
        select(
          {
            ...(dialog ? state : controls()),
            page: 1,
            from: button.dataset.day,
            to: button.dataset.rangeEnd,
          },
          { focus: dialog ? "feed" : "range" },
        );
    });
    feed.addEventListener("click", (event) => {
      const link = event.target.closest("[data-event-page]");
      if (
        !link ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey ||
        event.button !== 0
      )
        return;
      event.preventDefault();
      // A step link moves one page; a numbered link names its page.
      const step = { next: 1, previous: -1 }[link.dataset.eventPage];
      select(
        pageOf(
          state,
          step ? state.page + step : Number(link.dataset.eventPage),
        ),
        { focus: "feed" },
      );
    });
    function restore() {
      closeFilters();
      cancelSelection();
      const location = new URL(window.location.href);
      const onArchive = location.pathname === archive?.path;
      // After a reload, Back can reach another page's entry (the archive's,
      // from a query page) without leaving this document, which cannot draw
      // that page. It is loaded in full instead.
      if (!onArchive && !location.pathname.startsWith(config.pageBase)) {
        window.location.reload();
        return;
      }
      try {
        // The archive's address shows the archive, at the size and page it
        // names, unless it names a filter; a shared link's foreign keys
        // (fbclid, utm_source) do not count.
        const shown =
          onArchive &&
          model.archiveParams(location.searchParams, archive.query.size);
        const desired = shown
          ? archivePage(shown.size, shown.page)
          : model.queryFromParams(
              location.searchParams,
              config.initial,
              config,
            );
        if (sameQuery(desired, state)) resync();
        else select(desired, { history: false });
      } catch (error) {
        status.textContent = error.message;
      }
    }
    window.addEventListener("popstate", restore);
    restore();
  } catch {
    status.textContent =
      "Search is temporarily unavailable. The saved chart, events, and archive links are still available.";
  }
})();
