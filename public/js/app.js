/**
 * Returns the Authorization header for protected API calls.
 *
 * The Firebase ID token is stored at login by /js/login.js. When the server runs
 * with AUTH_REQUIRED=true, omitting this yields a 401. The SSE stream does not
 * need it: EventSource cannot set headers, so the server also accepts the
 * __session cookie set at login.
 *
 * @returns {Record<string, string>}
 */
const authHeaders = () => {
  const token = localStorage.getItem("agenticCouncilIdToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
};

const mapContainer = document.getElementById("india-map");
const map = (typeof L !== "undefined" && mapContainer) ? L.map("india-map", {
  zoomControl: false,
  attributionControl: false,
  zoomSnap: 0.25,
  zoomDelta: 0.25,
  inertia: false,
  preferCanvas: false,
  dragging: false,
  scrollWheelZoom: false,
  doubleClickZoom: false,
  touchZoom: false,
  boxZoom: false,
  keyboard: false,
}) : null;

const addGlowFilter = () => {
  if (!map) return;
  const svg = map.getPanes().overlayPane.querySelector("svg");
  if (!svg || svg.querySelector("#state-glow")) {
    return;
  }

  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const filter = document.createElementNS("http://www.w3.org/2000/svg", "filter");
  filter.setAttribute("id", "state-glow");
  filter.setAttribute("x", "-40%");
  filter.setAttribute("y", "-40%");
  filter.setAttribute("width", "180%");
  filter.setAttribute("height", "180%");

  const blur = document.createElementNS("http://www.w3.org/2000/svg", "feGaussianBlur");
  blur.setAttribute("stdDeviation", "6");
  blur.setAttribute("result", "coloredBlur");

  const merge = document.createElementNS("http://www.w3.org/2000/svg", "feMerge");
  const mergeNode1 = document.createElementNS("http://www.w3.org/2000/svg", "feMergeNode");
  mergeNode1.setAttribute("in", "coloredBlur");
  const mergeNode2 = document.createElementNS("http://www.w3.org/2000/svg", "feMergeNode");
  mergeNode2.setAttribute("in", "SourceGraphic");

  merge.appendChild(mergeNode1);
  merge.appendChild(mergeNode2);

  filter.appendChild(blur);
  filter.appendChild(merge);
  defs.appendChild(filter);
  svg.appendChild(defs);
};

const STATES_GEOJSON_URL = "/map/india-states.geojson";

const stateNameToLayers = new Map();
const normalizeStateName = (value) => {
  const normalized = (value || "").toLowerCase().replace(/\s+council$/i, "").replace(/\s+/g, " ").trim();
  const aliases = {
    "nct of delhi": "delhi",
    "national capital territory of delhi": "delhi",
    "orissa": "odisha",
    "uttaranchal": "uttarakhand",
    "jammu and kashmir": "jammu & kashmir",
    "andaman and nicobar": "andaman & nicobar",
    "andaman and nicobar islands": "andaman & nicobar",
  };
  return aliases[normalized] || normalized;
};

const getStateLayers = (stateName) => stateNameToLayers.get(normalizeStateName(stateName)) || [];

const getCurrentTheme = () => {
  return document.documentElement.getAttribute("data-theme") || "dark";
};

const getBaseStateStyle = (theme = getCurrentTheme()) => {
  if (theme === "light") {
    return {
      className: "state-path",
      fillColor: "#ffffff",
      fillOpacity: 0.75,
      color: "rgba(71, 85, 105, 0.45)",
      weight: 1.2,
    };
  }
  return {
    className: "state-path",
    fillColor: "#090909",
    fillOpacity: 0.28,
    color: "rgba(255, 255, 255, 0.15)",
    weight: 1.2,
  };
};

const getPositiveStateStyle = (theme = getCurrentTheme()) => {
  if (theme === "light") {
    return {
      className: "state-path positive",
      fillColor: "#22c55e",
      fillOpacity: 0.45,
      color: "#16a34a",
      weight: 2,
    };
  }
  return {
    className: "state-path positive",
    fillColor: "#22c55e",
    fillOpacity: 0.55,
    color: "#4ade80",
    weight: 2,
  };
};

const getNegativeStateStyle = (theme = getCurrentTheme()) => {
  if (theme === "light") {
    return {
      className: "state-path negative",
      fillColor: "#ef4444",
      fillOpacity: 0.45,
      color: "#dc2626",
      weight: 2,
    };
  }
  return {
    className: "state-path negative",
    fillColor: "#ef4444",
    fillOpacity: 0.55,
    color: "#f87171",
    weight: 2,
  };
};

const getActiveStateStyle = (theme = getCurrentTheme()) => {
  if (theme === "light") {
    return {
      className: "state-path active",
      fillColor: "rgba(15, 23, 42, 0.18)",
      fillOpacity: 0.8,
      color: "rgba(15, 23, 42, 0.85)",
      weight: 1.8,
    };
  }
  return {
    className: "state-path active",
    fillColor: "rgba(255, 255, 255, 0.45)",
    fillOpacity: 0.65,
    color: "rgba(255, 255, 255, 0.85)",
    weight: 1.8,
  };
};

// Proxies with full reflection traps so Leaflet setStyle enumerates properties correctly
const createThemeStyleProxy = (getter) =>
  new Proxy({}, {
    get(target, prop) {
      return getter()[prop];
    },
    ownKeys() {
      return Object.keys(getter());
    },
    getOwnPropertyDescriptor(target, prop) {
      const val = getter()[prop];
      return val !== undefined ? { value: val, enumerable: true, writable: true, configurable: true } : undefined;
    }
  });

const baseStateStyle = createThemeStyleProxy(getBaseStateStyle);
const positiveStateStyle = createThemeStyleProxy(getPositiveStateStyle);
const negativeStateStyle = createThemeStyleProxy(getNegativeStateStyle);
const activeStateStyle = createThemeStyleProxy(getActiveStateStyle);

const updateMapTheme = (theme = getCurrentTheme()) => {
  if (!stateLayer || !stateLayer.eachLayer) return;
  stateLayer?.eachLayer?.((layer) => {
    const isStateActive = layer.getElement()?.classList.contains("active");
    if (isStateActive) {
      layer.setStyle(getActiveStateStyle(theme));
      return;
    }
    const name = layer.feature?.properties?.ST_NM || layer.feature?.properties?.NAME_1 || layer.feature?.properties?.state || layer.feature?.properties?.name || "Unknown";
    const normalized = normalizeStateName(name);
    const impact = stateImpact.get(normalized);
    const element = layer.getElement();
    if (impact === "positive") {
      element?.classList.add("positive");
      element?.classList.remove("negative");
      layer.setStyle(getPositiveStateStyle(theme));
    } else if (impact === "negative") {
      element?.classList.add("negative");
      element?.classList.remove("positive");
      layer.setStyle(getNegativeStateStyle(theme));
    } else {
      element?.classList.remove("positive", "negative");
      layer.setStyle(getBaseStateStyle(theme));
    }
  });
};

const stateLayer = (typeof L !== "undefined" && map) ? L.geoJSON(null, {
  style: () => getBaseStateStyle(),
  onEachFeature: (feature, layer) => {
    console.log(`[MAP LOAD] Properties:`, feature.properties);
    const name = feature.properties?.ST_NM || feature.properties?.NAME_1 || feature.properties?.state || feature.properties?.name || "Unknown";
    const normalized = normalizeStateName(name);
    console.log(`[MAP LOAD] State: "${name}" -> normalized: "${normalized}"`);
    const list = stateNameToLayers.get(normalized) || [];
    list.push(layer);
    stateNameToLayers.set(normalized, list);

    layer.on("click", () => {
      console.log(`State clicked: ${name}`);
    });

    layer.on("mouseover", () => {
      layer.getElement()?.classList.add("hovered");
    });

    layer.on("mouseout", () => {
      layer.getElement()?.classList.remove("hovered");
    });
  },
}) : null;

if (map && stateLayer) {
  fetch(STATES_GEOJSON_URL)
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((geojson) => {
      stateLayer.addData(geojson).addTo(map);
      map.fitBounds(stateLayer.getBounds(), {
        padding: [16, 16],
        maxZoom: 6.5,
      });
      addGlowFilter();
      stateImpact.forEach((impact, stateName) => {
        const layers = getStateLayers(stateName);
        layers.forEach((layer) => {
          const el = layer.getElement();
          if (el) {
            el.classList.remove("active");
            if (impact === "positive") {
              el.classList.add("positive");
              el.classList.remove("negative");
            } else if (impact === "negative") {
              el.classList.add("negative");
              el.classList.remove("positive");
            }
          }
          if (impact === "positive") {
            layer.setStyle(getPositiveStateStyle());
          } else if (impact === "negative") {
            layer.setStyle(getNegativeStateStyle());
          } else {
            layer.setStyle(getBaseStateStyle());
          }
        });
      });
    })
    .catch((error) => {
      console.error("Failed to load map data:", error);
      if (mapContainer) {
        mapContainer.innerHTML =
          "<div style='color:#777;padding:20px;font-size:12px;'>Map data failed to load.</div>";
      }
    });
}

const fitMapBounds = () => {
  if (map && stateLayer && stateLayer.getLayers && stateLayer.getLayers().length) {
    try {
      map.invalidateSize();
      map.fitBounds(stateLayer.getBounds(), {
        padding: [16, 16],
        maxZoom: 6.5,
        animate: false,
      });
    } catch (e) {
    }
  }
};

const resizeObserver = new ResizeObserver(() => {
  fitMapBounds();
});

if (mapContainer) {
  resizeObserver.observe(mapContainer);
}

const feedList = document.getElementById("feed-list");
const feedBody = document.getElementById("feed-body");
const feedEmpty = document.getElementById("feed-empty");
const deliberationList = document.getElementById("deliberation-list");
const layoutElement = document.querySelector(".layout");
const topicText = document.getElementById("topic-text");
const agentCards = new Map();
const statusConnection = document.getElementById("status-connection");
const statusModel = document.getElementById("status-model");
const statusStrip = statusModel?.parentElement || null;
const topbarRight = document.querySelector(".topbar__right");
const fetchNextBtn = document.getElementById("fetch-next");
const topicInput = document.getElementById("topic-input");
const stateImpact = new Map();
const agentStreamEntries = new Map();
const agentEntryAnimators = new WeakMap();
let debateLoadingNote = null;
let activeFeedCard = null;
let fetchLocked = false;
let pendingCouncilCardTimers = [];
let debateRunningTimer = null;
let councilsBooted = false;
const systemNoteQueue = [];
let systemNoteStreaming = false;
const deferredSystemNotesByRound = new Map();
let systemNotesContainer = null;
const systemRoundPending = new Map();
const pendingRoundBeginControls = new Set();
const analysisPendingRounds = new Set();
const analysisTimers = new Map();
const pendingRoundTabTimers = new Map();
const analysisStartDeferred = new Set();

const autoResizeTopicInput = () => {
  if (!topicInput) {
    return;
  }
  const maxHeight = 220;
  topicInput.style.height = "auto";
  const nextHeight = Math.min(topicInput.scrollHeight, maxHeight);
  topicInput.style.height = `${nextHeight}px`;
  topicInput.style.overflowY = topicInput.scrollHeight > maxHeight ? "auto" : "hidden";
};

const getFeedContainer = () => feedBody || feedList;

const updateFeedEmptyState = () => {
  if (!feedEmpty) {
    return;
  }
  const container = getFeedContainer();
  const hasCards = Boolean(container?.querySelector(".feed-card"));
  feedEmpty.style.display = hasCards ? "none" : "flex";
};

const isSystemNotePipelineIdle = () => !systemNoteStreaming && systemNoteQueue.length === 0;

const isRoundUiReadyToReveal = (roundId) => {
  if (roundId === null || roundId === undefined) {
    return false;
  }
  if (analysisPendingRounds.has(roundId)) {
    return false;
  }
  if ((systemRoundPending.get(roundId) || 0) > 0) {
    return false;
  }
  return isSystemNotePipelineIdle();
};

const flushDeferredRoundUi = () => {
  roundContainers.forEach((payload, roundId) => {
    if (!isRoundUiReadyToReveal(roundId)) {
      return;
    }

    if (payload?.tab) {
      payload.tab.classList.remove("pending");
      updateRoundTabMarkers();
    }

    const deferred = deferredSystemNotesByRound.get(roundId);
    if (Array.isArray(deferred) && deferred.length) {
      deferredSystemNotesByRound.delete(roundId);
      deferred.forEach((item) => {
        if (item?.message) {
          systemNoteQueue.push(item);
        }
      });
      processSystemNoteQueue();
      return;
    }

    if (pendingRoundBeginControls.has(roundId)) {
      pendingRoundBeginControls.delete(roundId);
      setRoundBeginControl(roundId);
    }
  });
};

const getGoToCurrentRoundTarget = () => {
  let target = null;
  roundContainers.forEach((entry, id) => {
    if (!entry?.tab || entry.tab.classList.contains("pending")) {
      return;
    }
    if (target === null || id > target) {
      target = id;
    }
  });
  return target;
};

function startAnalysisHold(id) {
  if (analysisTimers.has(id)) return;
  analysisPendingRounds.add(id);
  const payload = roundContainers.get(id) || ensureRoundContainer(id);
  if (payload?.tab) payload.tab.classList.add("pending");

  const timer = setTimeout(() => {
    analysisPendingRounds.delete(id);
    analysisTimers.delete(id);
    flushDeferredRoundUi();
    if (id === 0 && !councilsBooted) {
      setRoundBeginControl(0, { booting: true });
    } else if (id === 4) {
      setRoundBeginControl(id, { processing: true, processingMessage: "Preparing Verdict..." });
    } else {
      setRoundBeginControl(id, { showGoOnly: true });
    }
    flushDeferredRoundUi();
  }, 5000);

  analysisTimers.set(id, timer);
}
let agentsContainer = null;
let llmMessagesContainer = null;
let otherAgentsContainer = null;
let councilHeaderEl = null;
let councilOrchestratorCardEl = null;
let councilRoundViewsContainer = null;
let systemPanelContainer = null;
let councilPanelContainer = null;
let systemRoundTabsContainer = null;
let systemRoundControlsContainer = null;
let systemRoundViewsContainer = null;
let systemGeneralMessagesContainer = null;
let systemMessagesScrollContainer = null;
let activeSystemRound = null;
let currentSystemRound = null;
let compactModeBtn = null;
const roundContainers = new Map();
const councilRoundContainers = new Map();
const completedCouncilRounds = new Set();
const roundTabLabels = new Map();

function normalizeRoundId(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return Math.trunc(parsed);
}

const getRoundTabLabel = (roundId) => roundTabLabels.get(roundId) || `View round ${roundId}`;

const getAgentCardKey = (payload = {}) => {
  const name = payload.agent || "Council";
  const roundId = normalizeRoundId(payload.round);
  return `${roundId === null ? "x" : roundId}:${name}`;
};

const animateAgentEntryText = (entry, text, options = {}) => {
  if (!entry) {
    return;
  }
  const nextText = String(text || "");
  const flush = Boolean(options.flush);

  let state = agentEntryAnimators.get(entry);
  if (!state) {
    state = {
      shown: "",
      target: "",
      timer: null,
    };
    agentEntryAnimators.set(entry, state);
  }

  state.target = nextText;

  if (flush) {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    state.shown = state.target;
    entry.textContent = state.target;
    return;
  }

  if (!state.shown || state.shown !== entry.textContent || state.shown.length > state.target.length || !state.target.startsWith(state.shown)) {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    state.shown = "";
    entry.innerHTML = "";
    entry.textContent = "";
  }

  if (state.timer) {
    return;
  }

  const step = () => {
    if (state.shown === state.target) {
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
      return;
    }

    const remaining = state.target.length - state.shown.length;
    const chunk = Math.min(6, Math.max(1, Math.ceil(remaining / 8)));
    state.shown = state.target.slice(0, state.shown.length + chunk);
    entry.textContent = state.shown;
  };

  step();
  state.timer = setInterval(step, 18);
};

const setFeedCollapsed = (collapsed) => {
  if (!layoutElement || !feedList) {
    return;
  }
  layoutElement.classList.toggle("feed-collapsed", Boolean(collapsed));
  const toggleBtn = feedList.querySelector(".feed-collapse-btn");
  if (toggleBtn) {
    toggleBtn.classList.toggle("is-collapsed", Boolean(collapsed));
    toggleBtn.classList.toggle("is-expanded", !collapsed);
    toggleBtn.setAttribute("aria-label", collapsed ? "Expand feed panel" : "Collapse feed panel");
    toggleBtn.setAttribute("title", collapsed ? "Expand feed panel" : "Collapse feed panel");
  }
  
  // Continuously sync and fit map canvas through the 400ms CSS grid transition
  const startTime = performance.now();
  const stepMap = (now) => {
    fitMapBounds();
    if (now - startTime < 450) {
      requestAnimationFrame(stepMap);
    }
  };
  requestAnimationFrame(stepMap);
};

const setCompactMode = (enabled) => {
  if (!layoutElement) {
    return;
  }
  layoutElement.classList.toggle("compact-mode", Boolean(enabled));
  try {
    document.body.classList.toggle("compact-mode", Boolean(enabled));
  } catch (e) {}

  if (enabled) {
    layoutElement.classList.remove("feed-collapsed");
  }

  if (compactModeBtn) {
    compactModeBtn.classList.toggle("is-active", Boolean(enabled));
    compactModeBtn.setAttribute("aria-pressed", String(Boolean(enabled)));
    compactModeBtn.setAttribute("aria-label", enabled ? "Switch to full view" : "Switch to compact mode");
    compactModeBtn.setAttribute("title", enabled ? "Full View" : "Compact View");

    const label = compactModeBtn.querySelector(".compact-btn-label");
    if (label) {
      label.style.opacity = "0";
      label.style.transform = "translateY(-3px)";
      setTimeout(() => {
        label.textContent = enabled ? "Full View" : "Compact View";
        label.style.opacity = "1";
        label.style.transform = "translateY(0)";
      }, 140);
    } else {
      compactModeBtn.textContent = enabled ? "Full View" : "Compact View";
    }
  }

  // Smoothly sync and resize map canvas across the entire 480ms transition
  const startTime = performance.now();
  const stepMap = (now) => {
    fitMapBounds();
    if (now - startTime < 480) {
      requestAnimationFrame(stepMap);
    }
  };
  requestAnimationFrame(stepMap);
};

const initCompactModeToggle = () => {
  const compactContainer = topbarRight || statusStrip;
  if (!compactContainer || !layoutElement || compactContainer.querySelector(".compact-mode-btn")) {
    return;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "status-item compact-mode-btn";
  button.setAttribute("aria-label", "Switch to compact mode");
  button.innerHTML = `
    <svg class="compact-btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
      <line x1="12" y1="3" x2="12" y2="21"></line>
    </svg>
    <span class="compact-btn-label">Compact View</span>
  `;
  button.addEventListener("click", () => {
    const enabled = layoutElement.classList.contains("compact-mode");
    setCompactMode(!enabled);
  });
  const profileMenu = compactContainer.querySelector(".user-profile-menu");
  if (profileMenu) {
    compactContainer.insertBefore(button, profileMenu);
  } else {
    compactContainer.appendChild(button);
  }
  compactModeBtn = button;
};

const initFeedCollapseToggle = () => {
  if (!feedList || !layoutElement || feedList.querySelector(".feed-collapse-btn")) {
    return;
  }
  const actions = document.getElementById("feed-actions");
  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "feed-collapse-btn";
  toggleBtn.setAttribute("aria-label", "Collapse feed panel");
  toggleBtn.setAttribute("title", "Collapse feed panel");
  toggleBtn.innerHTML = `
    <svg class="feed-collapse-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="15 18 9 12 15 6"></polyline>
    </svg>
  `;
  toggleBtn.addEventListener("click", () => {
    const collapsed = layoutElement.classList.contains("feed-collapsed");
    setFeedCollapsed(!collapsed);
  });
  if (actions) {
    actions.prepend(toggleBtn);
  } else {
    feedList.appendChild(toggleBtn);
  }
  setFeedCollapsed(layoutElement.classList.contains("feed-collapsed"));
};

const stopDebateRunningIndicator = () => {
  if (debateRunningTimer) {
    clearInterval(debateRunningTimer);
    debateRunningTimer = null;
  }
};

const startDebateRunningIndicator = () => {
  if (!fetchNextBtn) {
    return;
  }
  stopDebateRunningIndicator();
  let frame = 1;
  const render = () => {
    const dots = ".".repeat(frame);
    fetchNextBtn.textContent = `Resolution running${dots}`;
    frame = frame >= 3 ? 1 : frame + 1;
  };
  render();
  debateRunningTimer = setInterval(render, 420);
};

const updateCouncilEmptyState = () => {
  if (!councilOrchestratorCardEl) {
    return;
  }
  const hasRounds = councilRoundContainers.size > 0;
  councilOrchestratorCardEl.style.display = hasRounds || fetchLocked ? "none" : "grid";
};

const setDebateLocked = (locked) => {
  try {
    document.body.classList.toggle("debate-locked", Boolean(locked));
  } catch (e) {
  }
  fetchLocked = Boolean(locked);
  try {
    if (topicInput) {
      topicInput.disabled = Boolean(locked);
      if (locked) {
        topicInput.blur();
        topicInput.setAttribute("aria-disabled", "true");
      } else {
        topicInput.removeAttribute("aria-disabled");
      }
    }
  } catch (e) {
  }
  updateCouncilEmptyState();
};

document.addEventListener(
  "click",
  (e) => {
    if (!fetchLocked) return;
    const el = e.target;
    if (el.closest && (el.closest("#fetch-next") || el.closest("#topic-text") || el.closest(".topic-pill"))) {
      e.preventDefault();
      e.stopPropagation();
    }
  },
  true
);

const isNearTop = (element, threshold = 80) => {
  if (!element) {
    return true;
  }
  return element.scrollTop <= threshold;
};

const scrollToTop = (element) => {
  if (!element) {
    return;
  }
  element.scrollTop = 0;
};

initFeedCollapseToggle();
initCompactModeToggle();
updateFeedEmptyState();
autoResizeTopicInput();
topicInput?.addEventListener("input", () => {
  autoResizeTopicInput();
  if (topicInput.value.trim()) {
    clearInputError(topicInput);
    dismissTopWarning();
  }
});

const ensureDeliberationPanels = () => {
  if (
    systemPanelContainer &&
    systemPanelContainer.parentElement === deliberationList
  ) {
    return;
  }
  deliberationList.innerHTML = "";

  const top = document.createElement("div");
  top.className = "chat-pane chat-pane-system";

  deliberationList.appendChild(top);

  systemPanelContainer = top;
  councilPanelContainer = null;
};

const syncGoToCurrentRoundControl = () => {
  if (!systemRoundControlsContainer) {
    return;
  }

  const existing = systemRoundControlsContainer.querySelector(".go-current-round-btn");
  const targetRound = getGoToCurrentRoundTarget();
  const shouldShow = targetRound !== null && targetRound > 0 && activeSystemRound !== targetRound;

  if (!shouldShow) {
    existing?.remove();
    return;
  }

  if (existing) {
    existing.classList.remove("disabled");
    existing.disabled = false;
    return;
  }

  const goCurrentBtn = document.createElement("button");
  goCurrentBtn.className = "begin-round-btn go-current-round-btn";
  goCurrentBtn.textContent = "Go to current round";
  goCurrentBtn.addEventListener("click", () => {
    const target = getGoToCurrentRoundTarget();
    if (target === null) {
      return;
    }
    setActiveRound(target);
    goCurrentBtn.remove();
  });
  systemRoundControlsContainer.appendChild(goCurrentBtn);
};

const setActiveSystemRound = (roundId) => {
  roundContainers.forEach((entry, id) => {
    if (entry.view) {
      entry.view.classList.toggle("active", id === roundId);
    }
    if (entry.tab) {
      entry.tab.classList.toggle("active", id === roundId);
    }
  });
  if (systemGeneralMessagesContainer) {
    systemGeneralMessagesContainer.style.display = roundId === null ? "flex" : "none";
  }
  activeSystemRound = roundId;
  updateRoundTabMarkers();
  syncGoToCurrentRoundControl();

  const roundLabel = (roundId !== null && roundTabLabels.get(roundId)) || "";
  const isVerdict = String(roundLabel).trim().toLowerCase() === "verdict";

  if (isVerdict && councilOrchestratorCardEl) {
    let messageCount = 0;
    councilRoundContainers.forEach((entry) => {
      messageCount += (entry.llm?.children?.length || 0) + (entry.others?.children?.length || 0);
    });

    const orchestratorVisible = messageCount === 0 && !fetchLocked;
    if (orchestratorVisible) {
      const speechList = councilOrchestratorCardEl.querySelector(".agent-speech-list");
      if (speechList) {
        let entry = speechList.querySelector(".agent-speech");
        if (!entry) {
          entry = document.createElement("div");
          entry.className = "agent-speech";
          speechList.appendChild(entry);
        }
        entry.classList.remove("loading");
        entry.textContent = "The simulation is finished";
        councilOrchestratorCardEl.classList.remove("waiting");
        councilOrchestratorCardEl.classList.remove("muted");
        updateCouncilEmptyState();
      }
      return;
    }
  }

  if (!isVerdict && councilOrchestratorCardEl) {
    const speechList = councilOrchestratorCardEl.querySelector(".agent-speech-list");
    const entry = speechList?.querySelector(".agent-speech");
    if (entry && entry.textContent === "The simulation is finished") {
      entry.textContent = "Begin a resolution to see the council's responses.";
      entry.classList.remove("loading");
      updateCouncilEmptyState();
    }
  }
};

const updateRoundTabMarkers = () => {
  roundContainers.forEach((entry, id) => {
    if (!entry.tab) {
      return;
    }
    const isCurrent = currentSystemRound === id;
    const baseLabel = getRoundTabLabel(id);
    entry.tab.classList.toggle("current", isCurrent);
    entry.tab.textContent = isCurrent ? `${baseLabel} • Current` : baseLabel;
  });
};

const setActiveCouncilRound = (roundId) => {
  councilRoundContainers.forEach((entry, id) => {
    if (entry.view) {
      entry.view.classList.toggle("active", id === roundId);
    }
  });
};

const setActiveRound = (roundId) => {
  setActiveSystemRound(roundId);
  setActiveCouncilRound(roundId);
};

const addCompletedRoundTab = (roundId) => {
  const round = roundContainers.get(roundId);
  if (!round || round.tab || !systemRoundTabsContainer) {
    return;
  }
  const tab = document.createElement("button");
  tab.className = "system-round-tab";
  tab.textContent = getRoundTabLabel(roundId);
  tab.addEventListener("click", () => {
    setActiveRound(roundId);
  });
  systemRoundTabsContainer.appendChild(tab);
  round.tab = tab;
  updateRoundTabMarkers();
};

const ensureRoundContainer = (round) => {
  const roundId = normalizeRoundId(round);
  if (roundId === null) {
    return null;
  }
  ensureSystemNotesContainer();
  if (roundContainers.has(roundId)) {
    return roundContainers.get(roundId);
  }

  const view = document.createElement("div");
  view.className = "system-round-view";
  view.dataset.round = String(roundId);
  systemRoundViewsContainer.appendChild(view);

  const payload = { roundId, view, tab: null };
  if (systemRoundTabsContainer) {
    const tab = document.createElement("button");
    tab.className = "system-round-tab";
    tab.textContent = getRoundTabLabel(roundId);
    tab.addEventListener("click", () => {
      setActiveRound(roundId);
    });
    systemRoundTabsContainer.appendChild(tab);
    payload.tab = tab;
  }
  roundContainers.set(roundId, payload);
  updateRoundTabMarkers();
  return payload;
};

function setRoundOrchestratorState(roundId, options = {}) {
  const normId = normalizeRoundId(roundId);
  if (normId === null) return;
  let container = councilRoundContainers.get(normId);
  if (!container) {
    container = ensureCouncilRoundContainer(normId);
  }
  if (!container || !container.orchestrator) return;

  const orchestrator = container.orchestrator;
  const speechList = orchestrator.querySelector(".agent-speech-list");
  if (!speechList) return;
  let entry = speechList.querySelector(".agent-speech");
  if (!entry) {
    entry = document.createElement("div");
    entry.className = "agent-speech";
    speechList.appendChild(entry);
  }

  if (options.completed) {
    completedCouncilRounds.add(normId);
    orchestrator.classList.remove("waiting", "muted");
    entry.classList.remove("loading");
    entry.textContent = `Round ${normId} has been completed.`;
  } else if (options.assessing) {
    if (completedCouncilRounds.has(normId)) return;
    orchestrator.classList.add("waiting");
    orchestrator.classList.remove("muted");
    entry.classList.add("loading");
    entry.innerHTML = `<span style="display:inline-flex; align-items:center;"><span class="thinking-text">Councils are assessing</span><span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span></span>`;
  }
}

const ensureCouncilRoundContainer = (round) => {
  const roundId = normalizeRoundId(round);
  if (roundId === null) {
    return null;
  }
  ensureAgentsContainer();
  if (councilRoundContainers.has(roundId)) {
    return councilRoundContainers.get(roundId);
  }

  const view = document.createElement("div");
  view.className = "council-round-view";
  view.dataset.round = String(roundId);

  const orchestrator = createAgentCard({
    name: "Council Orchestrator",
    message: "",
  });
  orchestrator.classList.add("council-orchestrator");
  orchestrator.classList.add("council");
  view.appendChild(orchestrator);

  const llm = document.createElement("div");
  llm.className = "llm-messages";
  const others = document.createElement("div");
  others.className = "other-agents";

  view.appendChild(llm);
  view.appendChild(others);
  councilRoundViewsContainer?.appendChild(view);

  const payload = { roundId, view, orchestrator, llm, others };
  councilRoundContainers.set(roundId, payload);

  if (completedCouncilRounds.has(roundId)) {
    setRoundOrchestratorState(roundId, { completed: true });
  } else {
    setRoundOrchestratorState(roundId, { assessing: true });
  }

  updateCouncilEmptyState();
  return payload;
};

const setRoundBeginControl = (roundId, options = {}) => {
  ensureSystemNotesContainer();
  if (!systemRoundControlsContainer) {
    return;
  }
  systemRoundControlsContainer.innerHTML = "";
  if (roundId === null || roundId === undefined) {
    return;
  }

  const goTargetRound = getGoToCurrentRoundTarget();
  const canShowGoToCurrentRound =
    goTargetRound !== null &&
    goTargetRound > 0 &&
    activeSystemRound !== goTargetRound;

  const appendGoToCurrentRoundButton = () => {
    if (!canShowGoToCurrentRound) {
      return;
    }
    const goCurrentBtn = document.createElement("button");
    goCurrentBtn.className = "begin-round-btn go-current-round-btn";
    goCurrentBtn.textContent = "Go to current round";
    goCurrentBtn.addEventListener("click", () => {
      const targetRound = getGoToCurrentRoundTarget();
      if (targetRound === null) {
        return;
      }
      setActiveRound(targetRound);
      goCurrentBtn.remove();
    });
    systemRoundControlsContainer.appendChild(goCurrentBtn);
  };

  if (options.showGoOnly) {
    setRoundOrchestratorState(roundId, { assessing: true });
    appendGoToCurrentRoundButton();
    return;
  }

  if (options.processing) {
    const processingBtn = document.createElement("button");
    processingBtn.className = "begin-round-btn is-loading processing";
    processingBtn.disabled = true;
    processingBtn.classList.add("disabled");
    processingBtn.textContent = options.processingMessage || "Processing...";
    systemRoundControlsContainer.appendChild(processingBtn);
    appendGoToCurrentRoundButton();
    return;
  }

  const btn = document.createElement("button");
  btn.className = "begin-round-btn";
  const isBooting = Boolean(options.booting);
  if (isBooting) {
    btn.disabled = true;
    btn.classList.add("disabled");
    btn.textContent = "Councils are booting...";
    systemRoundControlsContainer.appendChild(btn);
    appendGoToCurrentRoundButton();
    return;
  }
  btn.textContent = `Begin round ${roundId}`;
  btn.addEventListener("click", async () => {
    setActiveRound(roundId);
    setRoundOrchestratorState(roundId, { assessing: true });
    systemRoundControlsContainer.innerHTML = "";
    const processingBtn = document.createElement("button");
    processingBtn.className = "begin-round-btn is-loading processing";
    processingBtn.disabled = true;
    processingBtn.classList.add("disabled");
    processingBtn.textContent = "Processing...";
    systemRoundControlsContainer.appendChild(processingBtn);
    if (roundId === 0 && fetchNextBtn) {
      startDebateRunningIndicator();
      fetchNextBtn.classList.remove("is-loading");
      fetchNextBtn.disabled = true;
    }
    try {
      await fetch(`/api/round/begin`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ round: roundId }),
      });
    } catch (e) {
    }
  });

  systemRoundControlsContainer.appendChild(btn);
  setRoundOrchestratorState(roundId, { assessing: true });

  appendGoToCurrentRoundButton();
};

const ensureSystemNotesContainer = () => {
  ensureDeliberationPanels();
  if (systemNotesContainer && systemNotesContainer.parentElement === systemPanelContainer) {
    return systemNotesContainer;
  }

  const container = document.createElement("div");
  container.className = "system-notes";

  const headerRow = document.createElement("div");
  headerRow.className = "system-header-row";

  const tabs = document.createElement("div");
  tabs.className = "system-round-tabs";

  const controls = document.createElement("div");
  controls.className = "system-round-controls";

  const general = document.createElement("div");
  general.className = "system-general-messages";

  const views = document.createElement("div");
  views.className = "system-round-views";

  const messagesScroll = document.createElement("div");
  messagesScroll.className = "system-messages-scroll";

  headerRow.appendChild(controls);
  messagesScroll.appendChild(general);
  messagesScroll.appendChild(views);

  container.appendChild(headerRow);
  container.appendChild(tabs);
  container.appendChild(messagesScroll);

  systemPanelContainer.appendChild(container);

  systemNotesContainer = container;
  systemRoundTabsContainer = tabs;
  systemRoundControlsContainer = controls;
  systemGeneralMessagesContainer = general;
  systemRoundViewsContainer = views;
  systemMessagesScrollContainer = messagesScroll;

  const initialTroyCard = createAgentCard({
    name: "Troy",
    message: "Waiting for resolution signal.",
  });
  initialTroyCard.id = "booting-card";
  general.appendChild(initialTroyCard);

  return container;
};

const ensureAgentsContainer = () => {
  return ensureSystemNotesContainer();
};

const resetCouncilRoundWindow = () => {
  pendingCouncilCardTimers.forEach((timer) => clearTimeout(timer));
  pendingCouncilCardTimers = [];
  agentCards.clear();
  agentStreamEntries.clear();
  councilRoundContainers.forEach((entry) => {
    if (entry.llm) entry.llm.innerHTML = "";
    if (entry.others) entry.others.innerHTML = "";
  });
  councilRoundContainers.clear();
  updateCouncilEmptyState();
};

const insertAgentCard = (card, round) => {
  const roundId = normalizeRoundId(round);
  if (roundId !== null) {
    const roundPayload = ensureRoundContainer(roundId);
    if (roundPayload?.view) {
      roundPayload.view.appendChild(card);
      return;
    }
  }
  ensureSystemNotesContainer();
  systemGeneralMessagesContainer?.appendChild(card);
};

const STATE_AVATAR_STYLES = {
  "Tamil Nadu": { initials: "TN", background: "#7dd3fc" },
  Delhi: { initials: "DL", background: "#fcd34d" },
  Kerala: { initials: "KL", background: "#6ee7b7" },
  Assam: { initials: "AS", background: "#fca5a5" },
  Punjab: { initials: "PB", background: "#c4b5fd" },
  Karnataka: { initials: "KA", background: "#93c5fd" },
  Maharashtra: { initials: "MH", background: "#fda4af" },
  "Uttar Pradesh": { initials: "UP", background: "#fdba74" },
  "West Bengal": { initials: "WB", background: "#a5b4fc" },
  Gujarat: { initials: "GJ", background: "#fca5a5" },
};

const STATE_TEAMS = {
  "Tamil Nadu": "dmk",
  Delhi: "aap",
  Punjab: "aap",
  Kerala: "cpim",
  Assam: "bjp",
  Karnataka: "congress",
  Maharashtra: "bjp",
  "Uttar Pradesh": "bjp",
  "West Bengal": "tmc",
  Gujarat: "bjp",
};

const getStateTeam = (stateName) => STATE_TEAMS[stateName] || "independent";

const getStateInitials = (stateName) => {
  const trimmed = (stateName || "").trim();
  if (!trimmed) {
    return "";
  }
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase();
  }
  return (words[0][0] + words[1][0]).toUpperCase();
};

const getStateAvatar = (stateName) => {
  const style = STATE_AVATAR_STYLES[stateName];
  if (style) {
    return style;
  }
  return {
    initials: getStateInitials(stateName),
    background: "#d4d4d4",
  };
};

const setStatusText = (el, value) => {
  if (!el || !value) {
    return;
  }
  el.textContent = value;
};

const setTopic = (title) => {
  if (!topicText) {
    return;
  }
  const cleanTitle = (title || "").trim();
  if (!cleanTitle || cleanTitle.toLowerCase() === "awaiting update" || cleanTitle.toLowerCase() === "connected. awaiting update") {
    topicText.textContent = "Current Complaint: Awaiting update";
    return;
  }
  if (/^current\s+(topic|complaint):\s*/i.test(cleanTitle)) {
    topicText.textContent = cleanTitle.replace(/^current\s+(topic|complaint):\s*/i, "Current Complaint: ");
    return;
  }
  topicText.textContent = `Current Complaint: ${cleanTitle}`;
};

const formatMeta = (source, publishedAt) => {
  const label = source || "Unknown";
  const time = publishedAt ? new Date(publishedAt).toLocaleTimeString() : "now";
  return `${label} • ${time}`;
};

const getStorySummary = (story) => {
  if (!story) {
    return "Policy summary";
  }
  return story.summary || story.title || "Policy summary";
};

const clearActiveFeed = () => {
  const container = getFeedContainer();
  container?.querySelectorAll(".feed-card.active").forEach((card) => {
    card.classList.remove("active");
    const badge = card.querySelector(".badge");
    if (badge) {
      badge.textContent = "Pending";
      badge.classList.add("muted");
    }
  });
};

const addFeedCard = (story, isActive) => {
  const card = document.createElement("div");
  card.className = `feed-card${isActive ? " active" : ""}`;
  const badgeLabel = isActive ? "Active" : "Pending";
  const badgeClass = isActive ? "badge" : "badge muted";

  card.innerHTML = `
    <div class="feed-card__header">
      <span class="${badgeClass}">${badgeLabel}</span>
    </div>
    <h3>${getStorySummary(story)}</h3>
    <p>${formatMeta(story.source, story.publishedAt)}</p>
  `;

  const container = getFeedContainer();
  container?.prepend(card);
  updateFeedEmptyState();
  return card;
};

const createActiveFeedCard = (story) => {
  const card = document.createElement("div");
  card.className = "feed-card active";
  card.id = "active-feed-card";
  card.innerHTML = `
    <div class="feed-card__header"></div>
    <h3>${getStorySummary(story)}</h3>
    <p>${formatMeta(story.source, story.publishedAt)}</p>
  `;
  return card;
};


const escapeHtml = (str) => {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
};

let isSummarizing = false;

const showSummarizingState = (policyText) => {
  isSummarizing = true;
  setOrchestratorWaiting(true, "Councils are assessing...");
  if (fetchNextBtn) {
    fetchNextBtn.disabled = true;
    fetchNextBtn.classList.add("is-loading");
    fetchNextBtn.innerHTML = '<span class="btn-spinner" aria-hidden="true"></span> Summarizing...';
  }

  let card = document.getElementById("active-feed-card");
  if (!card) {
    card = document.createElement("div");
    card.className = "feed-card active processing";
    card.id = "active-feed-card";
    const container = getFeedContainer();
    container?.prepend(card);
  }
  card.innerHTML = `
    <div class="feed-card__header">
      <span class="badge badge-summarizing"><span class="badge-spinner" aria-hidden="true"></span> Processing...</span>
    </div>
    <h3 class="summarizing-pulse">Summarizing complaint...</h3>
    <p>${escapeHtml((policyText || "").trim() || "Analyzing complaint intent...")}</p>
  `;
  card.classList.add("active", "processing");
  card.id = "active-feed-card";
  activeFeedCard = card;
  updateFeedEmptyState();
};

const setActiveFeed = (story) => {
  isSummarizing = false;
  let card = document.getElementById("active-feed-card");
  if (!card) {
    const activeCard = createActiveFeedCard(story);
    const container = getFeedContainer();
    container?.prepend(activeCard);
    card = activeCard;
  }
  const header = card.querySelector("h3");
  const meta = card.querySelector("p");
  if (header) {
    header.textContent = getStorySummary(story);
    header.classList.remove("summarizing-pulse");
  }
  if (meta) {
    meta.textContent = formatMeta(story.source, story.publishedAt);
  }

  // Remove processing block once summary has been shown
  const headerContainer = card.querySelector(".feed-card__header");
  if (headerContainer) {
    headerContainer.innerHTML = "";
  }

  card.classList.remove("processing");
  card.classList.add("active");
  card.id = "active-feed-card";
  activeFeedCard = card;
  updateFeedEmptyState();

  card.style.cursor = "default";
};

let currentActiveStateName = null;

const clearActiveState = () => {
  currentActiveStateName = null;
  if (!stateLayer || !stateLayer.eachLayer) return;
  stateLayer?.eachLayer?.((layer) => {
    const element = layer.getElement();
    if (element) {
      element.classList.remove("active");
    }
    const name = layer.feature?.properties?.ST_NM || layer.feature?.properties?.NAME_1 || layer.feature?.properties?.state || layer.feature?.properties?.name || "Unknown";
    const normalized = normalizeStateName(name);
    const impact = stateImpact.get(normalized);
    if (impact === "positive") {
      element?.classList.add("positive");
      element?.classList.remove("negative");
      layer.setStyle(getPositiveStateStyle());
    } else if (impact === "negative") {
      element?.classList.add("negative");
      element?.classList.remove("positive");
      layer.setStyle(getNegativeStateStyle());
    } else {
      element?.classList.remove("positive", "negative");
      layer.setStyle(getBaseStateStyle());
    }
  });
};

const setActiveState = (stateName) => {
  const normalized = normalizeStateName(stateName);
  if (!normalized) return;
  if (currentActiveStateName === normalized) {
    return; // Already active; avoid resetting styles and restarting animation on every token
  }

  // Clear any previous active highlights so ONLY this state is highlighted
  clearActiveState();
  currentActiveStateName = normalized;

  const targets = getStateLayers(normalized);
  targets.forEach((layer) => {
    const element = layer.getElement();
    if (element) {
      element.classList.add("active");
    }
    layer.setStyle(getActiveStateStyle());
  });
};

const setStateImpact = (stateName, impact) => {
  const normalized = normalizeStateName(stateName);
  console.log(`[MAP] Setting ${impact} impact for "${stateName}" -> normalized: "${normalized}"`);
  stateImpact.set(normalized, impact);
  const targets = getStateLayers(normalized);
  console.log(`[MAP] Found ${targets.length} layer(s) for "${normalized}"`);
  targets.forEach((layer) => {
    const el = layer.getElement();
    if (el) {
      el.classList.remove("active");
      if (impact === "positive") {
        el.classList.add("positive");
        el.classList.remove("negative");
      } else if (impact === "negative") {
        el.classList.add("negative");
        el.classList.remove("positive");
      } else {
        el.classList.remove("positive", "negative");
      }
    }
    if (impact === "positive") {
      layer.setStyle(getPositiveStateStyle());
    } else if (impact === "negative") {
      layer.setStyle(getNegativeStateStyle());
    } else {
      layer.setStyle(getBaseStateStyle());
    }
  });
};

function createAgentCard(agent) {
  const card = document.createElement("div");
  card.className = "agent-card";
  const isTroy = agent?.name === "System" || agent?.name === "Session Moderator" || agent?.name === "Troy" || agent?.name === "Council Orchestrator" || !agent?.name;
  const agentDisplayName = isTroy ? "Troy" : agent.name;
  card.dataset.agentName = agentDisplayName;
  if (isTroy) {
    card.classList.add("troy");
  } else if (agent?.state) {
    card.classList.add("state-agent");
    card.dataset.state = agent.state;
    card.classList.add(`team-${getStateTeam(agent.state)}`);
  } else {
    card.classList.add("delegate");
  }
  const avatarClass = "agent-avatar";
  const avatarInner = isTroy
    ? `<video class="agent-avatar-video" autoplay loop muted playsinline><source src="/assets/images/AI_spin.webm" type="video/webm" /></video><canvas class="agent-avatar-canvas" width="96" height="96"></canvas>`
    : "";
  const role = agent?.state ? `<span>${agent.state}</span>` : "";
  const messageText = agent?.message ?? "Waiting for resolution signal.";
  const hasMessage = Boolean(String(messageText).trim());
  card.innerHTML = `
    <div class="${avatarClass}">${avatarInner}</div>
    <div>
      <div class="agent-name">${agentDisplayName} ${role}</div>
      <div class="agent-speech-list">
        ${hasMessage ? `<div class="agent-speech">${messageText}</div>` : ""}
      </div>
    </div>
  `;
  card.querySelectorAll("video").forEach((v) => v.play().catch(() => {}));
  if (isTroy) {
    const avatar = card.querySelector(".agent-avatar");
    if (avatar) initTransparentVideoCanvas(avatar);
  }
  return card;
};

const initTransparentVideoCanvas = (avatarEl) => {
  if (!avatarEl || avatarEl.dataset.canvasInited) return;
  avatarEl.dataset.canvasInited = "true";
  const video = avatarEl.querySelector("video");
  const canvas = avatarEl.querySelector("canvas");
  if (!video || !canvas) return;

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  video.muted = true;
  video.play().catch(() => {});

  let animId;
  const render = () => {
    if (video.readyState >= 2 && !video.paused) {
      const w = canvas.width;
      const h = canvas.height;
      const vw = video.videoWidth || 960;
      const vh = video.videoHeight || 720;
      // Proportionate crop around the glowing AI orb
      const cropSize = Math.min(vw, vh) * 0.68;
      // The source video frame is horizontally compressed (~0.86 width/height).
      // Using sWidth = cropSize * 0.86 un-squishes the orb to a perfectly symmetrical 1:1 circle.
      const sHeight = cropSize;
      const sWidth = cropSize * 0.86;
      const sx = (vw - sWidth) / 2;
      const sy = (vh - sHeight) / 2;

      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(video, sx, sy, sWidth, sHeight, 0, 0, w, h);
      const imgData = ctx.getImageData(0, 0, w, h);
      const data = imgData.data;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        const brightness = Math.max(r, g, b);
        if (brightness < 20) {
          data[i + 3] = 0;
        } else if (brightness < 60) {
          data[i + 3] = Math.round(((brightness - 20) / 40) * 255);
        }
      }
      ctx.putImageData(imgData, 0, 0);
      video.style.opacity = "0";
      video.style.position = "absolute";
      video.style.pointerEvents = "none";
    }
    animId = requestAnimationFrame(render);
  };
  video.addEventListener("play", () => {
    if (!animId) animId = requestAnimationFrame(render);
  });
  animId = requestAnimationFrame(render);
};
const createBootingCard = () => {
  const card = document.createElement("div");
  card.className = "agent-card troy";
  card.id = "booting-card";
  card.innerHTML = `
    <div class="agent-avatar">
      <video class="agent-avatar-video" autoplay loop muted playsinline>
        <source src="/assets/images/AI_spin.webm" type="video/webm" />
      </video>
      <canvas class="agent-avatar-canvas" width="96" height="96"></canvas>
    </div>
    <div>
      <div class="agent-name">Troy</div>
      <div class="agent-speech-list">
        <div class="agent-speech">Waiting for resolution signal.</div>
      </div>
    </div>
  `;
  const avatar = card.querySelector(".agent-avatar");
  if (avatar) {
    initTransparentVideoCanvas(avatar);
  }
  return card;
};

const markCouncilBooted = () => {
  const booting = document.getElementById("booting-card");
  councilsBooted = true;
  if (!booting) {
    if (currentSystemRound === 0) {
      setRoundBeginControl(0);
    }
    return;
  }
  const nameEl = booting.querySelector(".agent-name");
  const speechEl = booting.querySelector(".agent-speech");
  if (nameEl) {
    nameEl.textContent = "Troy";
  }
  if (speechEl) {
    speechEl.textContent = "All councils loaded and ready.";
  }
  if (currentSystemRound === 0) {
    setRoundBeginControl(0);
  }
};

const removeBootingCard = () => {
  const booting = document.getElementById("booting-card");
  if (booting) {
    booting.remove();
  }
};

const removeStandingBy = (card) => {
  if (!card) {
    return;
  }
  const speechList = card.querySelector(".agent-speech-list");
  if (!speechList) {
    return;
  }
  const entries = Array.from(speechList.querySelectorAll(".agent-speech"));
  entries.forEach((entry) => {
    const text = (entry.textContent || "").trim().toLowerCase();
    if (text === "standing by..." || text === "awaiting signal.") {
      entry.remove();
    }
  });
};


const resetCouncil = (agents = []) => {
  deliberationList.innerHTML = "";
  councilsBooted = true;
  currentActiveStateName = null;
  agentCards.clear();
  agentStreamEntries.clear();
  systemNotesContainer = null;
  agentsContainer = null;
  llmMessagesContainer = null;
  otherAgentsContainer = null;
  councilHeaderEl = null;
  councilOrchestratorCardEl = null;
  councilRoundViewsContainer = null;
  systemPanelContainer = null;
  councilPanelContainer = null;
  systemRoundTabsContainer = null;
  systemRoundControlsContainer = null;
  systemRoundViewsContainer = null;
  systemGeneralMessagesContainer = null;
  systemMessagesScrollContainer = null;
  activeSystemRound = null;
  currentSystemRound = null;
  roundContainers.clear();
  councilRoundContainers.clear();
  roundTabLabels.clear();
  ensureSystemNotesContainer();
  ensureAgentsContainer();
  updateCouncilEmptyState();
  agents.forEach((agent) => {
    const card = createAgentCard({
      name: agent.name,
      state: agent.state,
      message: "Standing by...",
      muted: true,
    });
    const key = `0:${agent.name}`;
    agentCards.set(key, card);
    insertAgentCard(card, 0);
  });
};

const resetCouncilDelayed = (agents = [], delayMs = 2000) => {
  deliberationList.innerHTML = "";
  councilsBooted = false;
  currentActiveStateName = null;
  agentCards.clear();
  agentStreamEntries.clear();
  pendingCouncilCardTimers.forEach((timer) => clearTimeout(timer));
  pendingCouncilCardTimers = [];
  systemNotesContainer = null;
  agentsContainer = null;
  llmMessagesContainer = null;
  otherAgentsContainer = null;
  councilHeaderEl = null;
  councilOrchestratorCardEl = null;
  councilRoundViewsContainer = null;
  systemPanelContainer = null;
  councilPanelContainer = null;
  systemRoundTabsContainer = null;
  systemRoundControlsContainer = null;
  systemRoundViewsContainer = null;
  systemGeneralMessagesContainer = null;
  systemMessagesScrollContainer = null;
  activeSystemRound = null;
  currentSystemRound = null;
  roundContainers.clear();
  councilRoundContainers.clear();
  completedCouncilRounds.clear();
  roundTabLabels.clear();
  ensureSystemNotesContainer();
  ensureAgentsContainer();
  ensureCouncilRoundContainer(0);
  setRoundOrchestratorState(0, { assessing: true });
  setActiveRound(0);
  updateCouncilEmptyState();
  insertAgentCard(createBootingCard(), 0);
  let loadedCount = 0;
  if (!agents.length) {
    markCouncilBooted();
  }
  agents.forEach((agent, index) => {
    const timer = setTimeout(() => {
      if (agentCards.has(`0:${agent.name}`)) {
        return;
      }
      const card = createAgentCard({
        name: agent.name,
        state: agent.state,
        message: "Standing by...",
        muted: true,
      });
      const key = `0:${agent.name}`;
      agentCards.set(key, card);
      insertAgentCard(card, 0);
      loadedCount += 1;
      if (loadedCount >= agents.length) {
        markCouncilBooted();
      }
    }, delayMs * (index + 1));
    pendingCouncilCardTimers.push(timer);
  });
};

const createThinkingBanner = (isCompleted = false) => {
  const banner = document.createElement("div");
  banner.className = isCompleted ? "thinking-banner completed" : "thinking-banner";
  if (isCompleted) {
    banner.innerHTML = `<span class="thinking-done-badge">✓</span><span class="thinking-done-text">Council Member’s Assessment Complete</span>`;
  } else {
    banner.innerHTML = `<span class="thinking-text">Council member is thinking</span><span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span>`;
  }
  return banner;
};

const updateThinkingBanner = (banner, isCompleted = false) => {
  if (!banner) return;
  if (isCompleted) {
    banner.className = "thinking-banner completed";
    banner.innerHTML = `<span class="thinking-done-badge">✓</span><span class="thinking-done-text">Council Member’s Assessment Complete</span>`;
  } else {
    banner.className = "thinking-banner";
    banner.innerHTML = `<span class="thinking-text">Council member is thinking</span><span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span>`;
  }
};

const createThoughtBlock = (initialText = "", isStreaming = false, initialCollapsed = false) => {
  const block = document.createElement("div");
  block.className = `agent-thought-block${initialCollapsed ? " collapsed" : ""}`;
  block.innerHTML = `
    <div class="agent-thought-header" role="button" tabindex="0" title="Click to collapse / expand">
      <span class="thought-badge">${isStreaming ? "Reasoning" : "Thought Process"}</span>
      ${isStreaming ? '<span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span>' : ""}
      <span class="thought-toggle-hint">${initialCollapsed ? "Click to expand" : "Click to collapse"}</span>
      <svg class="thought-chevron" viewBox="0 0 20 20" fill="currentColor" width="13" height="13" aria-hidden="true">
        <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
      </svg>
    </div>
    <div class="agent-thought-body"></div>
  `;
  const bodyEl = block.querySelector(".agent-thought-body");
  if (bodyEl && initialText) {
    bodyEl.textContent = initialText;
  }
  const header = block.querySelector(".agent-thought-header");
  const toggleCollapse = () => {
    block.classList.toggle("collapsed");
    const hint = block.querySelector(".thought-toggle-hint");
    if (hint) {
      hint.textContent = block.classList.contains("collapsed") ? "Click to expand" : "Click to collapse";
    }
  };
  header.addEventListener("click", toggleCollapse);
  header.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleCollapse();
    }
  });
  return block;
};

const createDecisionBlock = (initialText = "") => {
  const block = document.createElement("div");
  block.className = "agent-decision-block";
  block.innerHTML = `
    <div class="agent-decision-header">
      <span class="decision-badge">Decision</span>
    </div>
    <div class="agent-decision-body agent-speech-text"></div>
  `;
  const bodyEl = block.querySelector(".agent-decision-body");
  if (bodyEl && initialText) {
    bodyEl.textContent = initialText;
  }
  return block;
};

const upsertAgentStreamMessage = (payload) => {
  const name = payload.agent || "Council";
  const roundId = normalizeRoundId(payload.round);
  const cardKey = getAgentCardKey(payload);
  agentCards.forEach((existing) => existing.classList.remove("speaking"));
  let card = agentCards.get(cardKey);
  if (!card) {
    card = createAgentCard({ name, state: payload.state, message: "" });
    agentCards.set(cardKey, card);
    insertAgentCard(card, roundId);
  }
  removeBootingCard();
  removeStandingBy(card);

  const speechList = card.querySelector(".agent-speech-list");
  const nameEl = card.querySelector(".agent-name");
  const avatarEl = card.querySelector(".agent-avatar");
  const label = payload.label || "";
  const streamKey = `${name}:${payload.round ?? ""}:${label}`;
  const eventType = payload._eventType || "delta";

  if (speechList) {
    let entry = agentStreamEntries.get(streamKey);
    if (!entry) {
      const loadingEntry = speechList.querySelector(".agent-speech.loading");
      if (loadingEntry) {
        entry = loadingEntry;
      } else {
        entry = document.createElement("div");
        entry.className = "agent-speech";
        speechList.appendChild(entry);
      }
      if (label) {
        entry.classList.add(`speech-${label.toLowerCase().replace(/\s+/g, "-")}`);
      }
      agentStreamEntries.set(streamKey, entry);
    }

    if (eventType === "start") {
      entry.classList.add("loading");
      entry.innerHTML = "";
      const banner = createThinkingBanner(false);
      entry.appendChild(banner);
    } else if (eventType === "reasoning_delta") {
      entry.classList.remove("loading");
      let banner = entry.querySelector(".thinking-banner");
      if (!banner) {
        banner = createThinkingBanner(false);
        entry.prepend(banner);
      }
      let thoughtBlock = entry.querySelector(".agent-thought-block");
      if (!thoughtBlock) {
        thoughtBlock = createThoughtBlock("", true);
        if (banner.nextSibling) {
          entry.insertBefore(thoughtBlock, banner.nextSibling);
        } else {
          entry.appendChild(thoughtBlock);
        }
      }
      const bodyEl = thoughtBlock.querySelector(".agent-thought-body");
      if (bodyEl && payload.delta) {
        bodyEl.textContent += payload.delta;
        bodyEl.scrollTop = bodyEl.scrollHeight;
      }
    } else if (eventType === "delta") {
      entry.classList.remove("loading");
      let banner = entry.querySelector(".thinking-banner");
      if (banner) {
        updateThinkingBanner(banner, true);
      } else {
        banner = createThinkingBanner(true);
        entry.prepend(banner);
      }

      const thoughtBlock = entry.querySelector(".agent-thought-block");
      if (thoughtBlock) {
        const badge = thoughtBlock.querySelector(".thought-badge");
        if (badge) badge.textContent = "Thought Process";
        const dots = thoughtBlock.querySelector(".thinking-dots");
        if (dots) dots.remove();
        // Auto-collapse thought process once thinking is complete
        thoughtBlock.classList.add("collapsed");
        const hint = thoughtBlock.querySelector(".thought-toggle-hint");
        if (hint) {
          hint.textContent = "Click to expand";
        }
      }

      let decisionBlock = entry.querySelector(".agent-decision-block");
      if (!decisionBlock) {
        decisionBlock = createDecisionBlock("");
        entry.appendChild(decisionBlock);
      }
      const decisionBody = decisionBlock.querySelector(".agent-decision-body");
      if (decisionBody && payload.delta) {
        const cleanDelta = payload.delta.replace(/<\/?(?:thought|think)\b[^>]*>/gi, "");
        if (cleanDelta) {
          decisionBody.textContent += cleanDelta;
          decisionBody.textContent = decisionBody.textContent
            .replace(/^\s*(?:\*{3,}|-{3,}|_{3,})\s*/g, "")
            .replace(/^\s*(?:(?:\*{1,2}|\[|\()?\s*(?:Public\s+)?(?:Speech|Statement|Verdict|Decision)\s*(?:\([^)]*\))?\s*(?:\*{1,2}|\]|\))?\s*:?\s*)/gi, "")
            .replace(/^\s*(?:\*{3,}|-{3,}|_{3,})\s*/g, "");
        }
      }
    } else if (eventType === "end" || payload.message) {
      entry.classList.remove("loading");
      speechList.querySelectorAll(".agent-speech.loading").forEach((el) => {
        if (el !== entry) el.remove();
      });

      let banner = entry.querySelector(".thinking-banner");
      if (banner) {
        updateThinkingBanner(banner, true);
      }

      const reasoningText = (payload.reasoning || "").trim();
      let thoughtBlock = entry.querySelector(".agent-thought-block");
      if (!thoughtBlock && reasoningText) {
        thoughtBlock = createThoughtBlock(reasoningText, false, true);
        if (banner && banner.nextSibling) {
          entry.insertBefore(thoughtBlock, banner.nextSibling);
        } else if (banner) {
          entry.appendChild(thoughtBlock);
        } else {
          entry.prepend(thoughtBlock);
        }
      } else if (thoughtBlock) {
        const badge = thoughtBlock.querySelector(".thought-badge");
        if (badge) badge.textContent = "Thought Process";
        const dots = thoughtBlock.querySelector(".thinking-dots");
        if (dots) dots.remove();
        // Auto-collapse thought process block
        thoughtBlock.classList.add("collapsed");
        const hint = thoughtBlock.querySelector(".thought-toggle-hint");
        if (hint) {
          hint.textContent = "Click to expand";
        }
        if (reasoningText) {
          const bodyEl = thoughtBlock.querySelector(".agent-thought-body");
          if (bodyEl && !bodyEl.textContent.trim()) {
            bodyEl.textContent = reasoningText;
          }
        }
      }

      let decisionBlock = entry.querySelector(".agent-decision-block");
      if (!decisionBlock) {
        decisionBlock = createDecisionBlock(payload.message || "");
        entry.appendChild(decisionBlock);
      } else {
        const decisionBody = decisionBlock.querySelector(".agent-decision-body");
        if (decisionBody) {
          decisionBody.textContent = payload.message || "";
        }
      }
    }
  }

  if (nameEl && payload.state) {
    nameEl.innerHTML = `${name} <span>${payload.state}</span>`;
  }
  if (payload.state) {
    card.classList.add("state-agent");
    card.dataset.state = payload.state;
    card.classList.add(`team-${getStateTeam(payload.state)}`);
    if (avatarEl) {
      avatarEl.textContent = "";
      avatarEl.style.background = "";
    }
  }
  card.classList.remove("muted");
  card.classList.add("speaking");
  if (payload.state) {
    setActiveState(payload.state);
  }
  if (isNearTop(deliberationList)) {
    scrollToTop(deliberationList);
  }
};

const upsertAgentMessage = (payload) => {
  const name = payload.agent || "Council";
  const roundId = normalizeRoundId(payload.round);
  const cardKey = getAgentCardKey(payload);
  agentCards.forEach((existing) => existing.classList.remove("speaking"));
  let card = agentCards.get(cardKey);
  if (!card) {
    card = createAgentCard({ name, state: payload.state, message: "" });
    agentCards.set(cardKey, card);
    insertAgentCard(card, roundId);
  }

  const speechList = card.querySelector(".agent-speech-list");
  const nameEl = card.querySelector(".agent-name");
  const avatarEl = card.querySelector(".agent-avatar");
  if (speechList) {
    const label = payload.label || "";
    const streamKey = `${name}:${payload.round ?? ""}:${label}`;
    let entry = agentStreamEntries.get(streamKey);
    if (!entry) {
      const loadingEntry = speechList.querySelector(".agent-speech.loading");
      if (loadingEntry) {
        entry = loadingEntry;
      } else {
        entry = document.createElement("div");
        entry.className = "agent-speech";
        speechList.appendChild(entry);
      }
      if (label) {
        entry.classList.add(`speech-${label.toLowerCase().replace(/\s+/g, "-")}`);
      }
      agentStreamEntries.set(streamKey, entry);
    }
    entry.classList.remove("loading");
    speechList.querySelectorAll(".agent-speech.loading").forEach((el) => {
      if (el !== entry) el.remove();
    });

    const hasReasoning = Boolean(payload.reasoning && payload.reasoning.trim());

    // 1. Thinking Banner at top
    let banner = entry.querySelector(".thinking-banner");
    if (!banner) {
      banner = createThinkingBanner(true);
      entry.prepend(banner);
    } else {
      updateThinkingBanner(banner, true);
    }

    // 2. Block 1: Thought Process Block (if reasoning exists or already present)
    let thoughtBlock = entry.querySelector(".agent-thought-block");
    if (!thoughtBlock && hasReasoning) {
      thoughtBlock = createThoughtBlock(payload.reasoning.trim(), false, true);
      if (banner && banner.nextSibling) {
        entry.insertBefore(thoughtBlock, banner.nextSibling);
      } else {
        entry.appendChild(thoughtBlock);
      }
    } else if (thoughtBlock) {
      const badge = thoughtBlock.querySelector(".thought-badge");
      if (badge) badge.textContent = "Thought Process";
      const dots = thoughtBlock.querySelector(".thinking-dots");
      if (dots) dots.remove();
      // Auto-collapse thought process block
      thoughtBlock.classList.add("collapsed");
      const hint = thoughtBlock.querySelector(".thought-toggle-hint");
      if (hint) {
        hint.textContent = "Click to expand";
      }
      if (hasReasoning) {
        const bodyEl = thoughtBlock.querySelector(".agent-thought-body");
        if (bodyEl && !bodyEl.textContent.trim()) {
          bodyEl.textContent = payload.reasoning.trim();
        }
      }
    }

    // 3. Block 2: Decision Block
    let decisionBlock = entry.querySelector(".agent-decision-block");
    if (!decisionBlock) {
      decisionBlock = createDecisionBlock(payload.message || "");
      entry.appendChild(decisionBlock);
    } else {
      const decisionBody = decisionBlock.querySelector(".agent-decision-body");
      if (decisionBody) {
        decisionBody.textContent = payload.message || "";
      }
    }
  }

  if (nameEl && payload.state) {
    nameEl.innerHTML = `${name} <span>${payload.state}</span>`;
  }
  if (payload.state) {
    card.classList.add("state-agent");
    card.dataset.state = payload.state;
    card.classList.add(`team-${getStateTeam(payload.state)}`);
    if (avatarEl) {
      avatarEl.textContent = "";
      avatarEl.style.background = "";
    }
  }
  card.classList.remove("muted");

  removeBootingCard();
  removeStandingBy(card);

  card.classList.add("speaking");
  if (payload.state) {
    setActiveState(payload.state);
  }
  if (isNearTop(deliberationList)) {
    scrollToTop(deliberationList);
  }
};

const addSystemNote = (message, options = {}) => {
  const note = createAgentCard({ name: "Troy", message, muted: false });
  const speechList = note.querySelector(".agent-speech-list");
  const isLoading = Boolean(options.loading);
  if (speechList && message?.includes("\n")) {
    speechList.innerHTML = "";
    message.split("\n").forEach((line, index) => {
      const entry = document.createElement("div");
      entry.className = "agent-speech";
      if (isLoading && index === 0) {
        entry.classList.add("loading");
      }
      if (line.startsWith("Positively impacted:")) {
        entry.classList.add("agent-speech--positive-impact");
        const rest = line.substring("Positively impacted:".length);
        entry.innerHTML = `<span class="impact-label impact-label--positive">Positively impacted:</span><span class="impact-states impact-states--positive">${escapeHtml(rest)}</span>`;
      } else if (line.startsWith("Negatively impacted:")) {
        entry.classList.add("agent-speech--negative-impact");
        const rest = line.substring("Negatively impacted:".length);
        entry.innerHTML = `<span class="impact-label impact-label--negative">Negatively impacted:</span><span class="impact-states impact-states--negative">${escapeHtml(rest)}</span>`;
      } else {
        entry.textContent = line;
      }
      speechList.appendChild(entry);
    });
  } else if (speechList) {
    const entry = speechList.querySelector(".agent-speech");
    if (entry) {
      if (isLoading) {
        entry.classList.add("loading");
      }
      if (message.startsWith("Positively impacted:")) {
        entry.classList.add("agent-speech--positive-impact");
        const rest = message.substring("Positively impacted:".length);
        entry.innerHTML = `<span class="impact-label impact-label--positive">Positively impacted:</span><span class="impact-states impact-states--positive">${escapeHtml(rest)}</span>`;
      } else if (message.startsWith("Negatively impacted:")) {
        entry.classList.add("agent-speech--negative-impact");
        const rest = message.substring("Negatively impacted:".length);
        entry.innerHTML = `<span class="impact-label impact-label--negative">Negatively impacted:</span><span class="impact-states impact-states--negative">${escapeHtml(rest)}</span>`;
      }
    }
  }
  const roundId = normalizeRoundId(options.round);
  if (roundId !== null) {
    const round = ensureRoundContainer(roundId);
    round?.view.append(note);
    if (activeSystemRound === null) {
      setActiveRound(roundId);
    }
  } else {
    ensureSystemNotesContainer();
    systemGeneralMessagesContainer?.append(note);
  }
  scrollToTop(systemMessagesScrollContainer);
  return note;
};

const streamCardMessage = (card, message, options = {}) =>
  new Promise((resolve) => {
    const speechList = card?.querySelector(".agent-speech-list");
    const text = String(message || "");
    const lines = text.split("\n");
    const speed = Number.isFinite(options.speed) ? options.speed : 50;

    if (!speechList) {
      resolve(card);
      return;
    }

    speechList.innerHTML = "";

    if (!text.trim()) {
      resolve(card);
      return;
    }

    let lineIndex = 0;
    let charIndex = 0;
    let currentEntry = document.createElement("div");
    currentEntry.className = "agent-speech system-streaming";
    if (lines[0]?.startsWith("Positively impacted:")) {
      currentEntry.classList.add("agent-speech--positive-impact");
    } else if (lines[0]?.startsWith("Negatively impacted:")) {
      currentEntry.classList.add("agent-speech--negative-impact");
    }
    if (options.loading) {
      currentEntry.classList.add("loading");
    }
    speechList.appendChild(currentEntry);

    const scrollContainer = options.scrollContainer || null;
    let finished = false;
    let timer = null;

    const finishStream = () => {
      if (finished) {
        return;
      }
      finished = true;
      if (timer) {
        clearInterval(timer);
      }
      const entries = speechList.querySelectorAll(".agent-speech");
      lines.forEach((l, idx) => {
        const ent = entries[idx];
        if (!ent) return;
        ent.classList.remove("system-streaming");
        if (l.startsWith("Positively impacted:")) {
          ent.classList.add("agent-speech--positive-impact");
          const rest = l.substring("Positively impacted:".length);
          ent.innerHTML = `<span class="impact-label impact-label--positive">Positively impacted:</span><span class="impact-states impact-states--positive">${escapeHtml(rest)}</span>`;
        } else if (l.startsWith("Negatively impacted:")) {
          ent.classList.add("agent-speech--negative-impact");
          const rest = l.substring("Negatively impacted:".length);
          ent.innerHTML = `<span class="impact-label impact-label--negative">Negatively impacted:</span><span class="impact-states impact-states--negative">${escapeHtml(rest)}</span>`;
        }
      });
      resolve(card);
    };

    const step = () => {
      const line = lines[lineIndex] || "";
      if (charIndex <= line.length) {
        currentEntry.textContent = line.slice(0, charIndex);
        charIndex += 1;
        if (scrollContainer) {
          scrollToTop(scrollContainer);
        }
        return;
      }

      currentEntry.classList.remove("system-streaming");
      if (line.startsWith("Positively impacted:")) {
        currentEntry.classList.add("agent-speech--positive-impact");
        const rest = line.substring("Positively impacted:".length);
        currentEntry.innerHTML = `<span class="impact-label impact-label--positive">Positively impacted:</span><span class="impact-states impact-states--positive">${escapeHtml(rest)}</span>`;
      } else if (line.startsWith("Negatively impacted:")) {
        currentEntry.classList.add("agent-speech--negative-impact");
        const rest = line.substring("Negatively impacted:".length);
        currentEntry.innerHTML = `<span class="impact-label impact-label--negative">Negatively impacted:</span><span class="impact-states impact-states--negative">${escapeHtml(rest)}</span>`;
      }

      lineIndex += 1;
      if (lineIndex >= lines.length) {
        finishStream();
        return;
      }

      charIndex = 0;
      currentEntry = document.createElement("div");
      currentEntry.className = "agent-speech system-streaming";
      const nextLine = lines[lineIndex] || "";
      if (nextLine.startsWith("Positively impacted:")) {
        currentEntry.classList.add("agent-speech--positive-impact");
      } else if (nextLine.startsWith("Negatively impacted:")) {
        currentEntry.classList.add("agent-speech--negative-impact");
      }
      if (options.loading) {
        currentEntry.classList.add("loading");
      }
      speechList.appendChild(currentEntry);
    };

    timer = setInterval(step, speed);
  });

const streamSystemNote = (message, options = {}) =>
  new Promise((resolve) => {
    const note = createAgentCard({ name: "Troy", message: "", muted: false });

    const roundId = normalizeRoundId(options.round);
    if (roundId !== null) {
      const round = ensureRoundContainer(roundId);
      round?.view.append(note);
      if (activeSystemRound === null) {
        setActiveRound(roundId);
      }
    } else {
      ensureSystemNotesContainer();
      systemGeneralMessagesContainer?.append(note);
    }
    streamCardMessage(note, message, {
      speed: 50,
      loading: Boolean(options.loading),
      scrollContainer: systemMessagesScrollContainer,
    }).then(() => {
      setSystemNoteLoading(note, false);
      resolve(note);
    });
  });

const processSystemNoteQueue = async () => {
  if (systemNoteStreaming) {
    return;
  }
  systemNoteStreaming = true;
  try {
    while (systemNoteQueue.length) {
      const item = systemNoteQueue.shift();
      if (!item || !item.message) {
        continue;
      }
      await streamSystemNote(item.message, item.options || {});

      const roundId = normalizeRoundId(item.options?.round);
      if (roundId !== null) {
        const prev = Math.max(0, systemRoundPending.get(roundId) || 0) - 1;
        systemRoundPending.set(roundId, prev);

        if (prev <= 0 && analysisStartDeferred.has(roundId)) {
          analysisStartDeferred.delete(roundId);
          startAnalysisHold(roundId);
        }

        if (prev <= 0) {
          flushDeferredRoundUi();
        }
      }

      flushDeferredRoundUi();
    }
  } finally {
    systemNoteStreaming = false;
    flushDeferredRoundUi();
  }
};
try {
  if (typeof window !== "undefined") {
    window.addSystemNote = addSystemNote;
  }
} catch (e) {
}

const safeAddNote = (message, options = {}) => {
  try {
    if (typeof window !== "undefined" && typeof window["addSystemNote"] === "function") {
      const roundId = normalizeRoundId(options?.round);
      if (roundId !== null && analysisPendingRounds.has(roundId)) {
        const bucket = deferredSystemNotesByRound.get(roundId) || [];
        bucket.push({ message, options });
        deferredSystemNotesByRound.set(roundId, bucket);
        return null;
      }
      if (options?.loading && !options?.stream) {
        return window["addSystemNote"](message, options);
      }
      systemNoteQueue.push({ message, options });
      processSystemNoteQueue();
      return null;
    }
  } catch (e) {
  }
  try {
    console.warn("[SYS NOTE]", message);
  } catch (e) {}
};

const setSystemNoteLoading = (note, isLoading, message) => {
  if (!note) {
    return;
  }
  const speechList = note.querySelector(".agent-speech-list");
  if (!speechList) {
    return;
  }
  const entry = speechList.querySelector(".agent-speech");
  if (!entry) {
    return;
  }
  entry.classList.toggle("loading", Boolean(isLoading));
  if (message) {
    if (isLoading) {
      entry.innerHTML = `<span class="thinking-text">${message.replace(/\.{3,}$/, "")}</span><span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span>`;
    } else {
      entry.textContent = message;
    }
  }
};

const setOrchestratorWaiting = (isWaiting, message = "Councils are assessing...") => {
  if (currentSystemRound !== null) {
    if (isWaiting) {
      setRoundOrchestratorState(currentSystemRound, { assessing: true });
    }
  }
  if (!councilOrchestratorCardEl) return;
  const speechList = councilOrchestratorCardEl.querySelector(".agent-speech-list");
  if (!speechList) return;
  let entry = speechList.querySelector(".agent-speech");
  if (!entry) {
    entry = document.createElement("div");
    entry.className = "agent-speech";
    speechList.appendChild(entry);
  }
  if (isWaiting) {
    councilOrchestratorCardEl.classList.add("waiting");
    entry.classList.add("loading");
    entry.innerHTML = `<span style="display:inline-flex; align-items:center;"><span class="thinking-text">${message.replace(/\.{3,}$/, "")}</span><span class="thinking-dots"><span class="thinking-dot">.</span><span class="thinking-dot">.</span><span class="thinking-dot">.</span></span></span>`;
    councilOrchestratorCardEl.classList.remove("muted");
  } else {
    councilOrchestratorCardEl.classList.remove("waiting");
    entry.classList.remove("loading");
  }
  updateCouncilEmptyState();
};

const clearDebateLoading = (payload) => {
  if (!debateLoadingNote) {
    return;
  }
  const message = payload?.message?.trim();
  if (!message) {
    return;
  }
  setSystemNoteLoading(debateLoadingNote, false, "Simulation started.");
  debateLoadingNote = null;
};

const clearSystemLoadingIndicators = () => {
  if (!systemNotesContainer) {
    return;
  }
  const loadingEntries = systemNotesContainer.querySelectorAll(".agent-speech.loading");
  loadingEntries.forEach((entry) => entry.classList.remove("loading"));
};

const connectLiveStream = () => {
  if (!window.EventSource) {
    console.warn("[Stream] Updates unsupported in this browser.");
    return;
  }

  const token = localStorage.getItem("agenticCouncilIdToken");
  const streamUrl = token ? `/api/stream?token=${encodeURIComponent(token)}` : "/api/stream";
  const stream = new EventSource(streamUrl);
  stream.addEventListener("open", () => {
    setTopic("Awaiting update");
    setStatusText(statusConnection, "connected");
  });

  stream.addEventListener("topic", (event) => {
    const data = JSON.parse(event.data);
    setTopic(data.title || "Update");
  });

  stream.addEventListener("policy_summarizing", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      showSummarizingState(data.policy);
    } catch (_) {
      showSummarizingState();
    }
  });

  stream.addEventListener("feed", (event) => {
    const data = JSON.parse(event.data);
    setActiveFeed(data);
  });

  stream.addEventListener("council_start", (event) => {
    const data = JSON.parse(event.data);
    stateLayer?.eachLayer?.((layer) => {
      layer.getElement()?.classList.remove("active");
      layer.setStyle(baseStateStyle);
    });
    stateImpact.clear();
    resetCouncilDelayed(data.agents || [], 2000);
    setRoundBeginControl(null);
    debateLoadingNote = safeAddNote("Beginning resolution...", { loading: true });
    setDebateLocked(true);
  });

  stream.addEventListener("system", (event) => {
    const data = JSON.parse(event.data);
    if (data.message) {
      safeAddNote(data.message, {
        round: data.round,
        loading: Boolean(data.loading),
        stream: Boolean(data.stream),
      });

      const roundId = normalizeRoundId(data.round);
      if (Boolean(data.stream) && roundId !== null) {
        systemRoundPending.set(roundId, (systemRoundPending.get(roundId) || 0) + 1);
        if (roundId === currentSystemRound) {
          setOrchestratorWaiting(true);
        }
      }
    }
  });

  stream.addEventListener("round_start", (event) => {
    const data = JSON.parse(event.data);
    const roundId = normalizeRoundId(data.round);
    if (roundId === null) {
      return;
    }
    if (String(data.label || "").trim().toLowerCase() === "verdict") {
      roundTabLabels.set(roundId, "Preparing Verdict...");
    }
    clearSystemLoadingIndicators();
    const prevSystemRound = currentSystemRound;
    currentSystemRound = roundId;
    const roundPayload = ensureRoundContainer(roundId);
    ensureCouncilRoundContainer(roundId);
    if (activeSystemRound === null) {
      setActiveRound(roundId);
    }
    clearActiveState();

    for (let r = 0; r < roundId; r++) {
      setRoundOrchestratorState(r, { completed: true });
    }
    setRoundOrchestratorState(roundId, { assessing: true });
    setOrchestratorWaiting(true, "Assessing...");

    analysisPendingRounds.add(roundId);
    if (roundPayload?.tab) {
      roundPayload.tab.classList.add("pending");
    }

    if (roundId === 1 && prevSystemRound === 0) {
      if ((systemRoundPending.get(roundId) || 0) === 0) {
        startAnalysisHold(roundId);
      } else {
        analysisStartDeferred.add(roundId);
      }
    } else {
      startAnalysisHold(roundId);
    }

    if ((systemRoundPending.get(roundId) || 0) > 0) {
      setOrchestratorWaiting(true, "Assessing...");
    }

    if (roundId === 0 && !councilsBooted) {
      setRoundBeginControl(0, { booting: true });
    } else if (String(data.label || "").trim().toLowerCase() === "verdict") {
      setRoundBeginControl(roundId, { processing: true, processingMessage: "Preparing Verdict..." });
    } else {
      pendingRoundBeginControls.add(roundId);
      setRoundOrchestratorState(roundId, { assessing: true });
      flushDeferredRoundUi();
    }
  });

  stream.addEventListener("panel_selected", (event) => {
    const data = JSON.parse(event.data);

    if (data.benched) {
      data.benched.forEach((stateName) => {
        const normalized = normalizeStateName(stateName);
        const targets = getStateLayers(normalized);
        targets.forEach((layer) => {
          layer.setStyle({
            className: "state-path",
            fillColor: "#090909",
            fillOpacity: 0.12,
            color: "rgba(255, 255, 255, 0.06)",
            weight: 0.8,
          });
        });
      });
    }
    if (data.benched) {
      data.benched.forEach((stateName) => {
        agentCards.forEach((card, name) => {
          if (name.includes(stateName)) {
            card.classList.add("benched");
          }
        });
      });
    }
  });

  stream.addEventListener("rebuttal_selected", (event) => {
    JSON.parse(event.data);
  });

  stream.addEventListener("reply_selected", (event) => {
    JSON.parse(event.data);
  });

  stream.addEventListener("round_summary", (event) => {
    const data = JSON.parse(event.data);
    clearActiveState();
    const roundId = normalizeRoundId(data.round);
    if (roundId !== null) {
      setRoundOrchestratorState(roundId, { completed: true });
    }
    const isCompact = Boolean(layoutElement?.classList.contains("compact-mode"));
    if (!isCompact || !data?.message) {
      return;
    }
    if (roundId === null) {
      return;
    }
    const label = data.label || `Round ${roundId}`;
    safeAddNote(`${label} Summary\n${data.message}`, { round: roundId });
  });

  stream.addEventListener("agent", (event) => {
    const data = JSON.parse(event.data);
    upsertAgentMessage(data);
    clearDebateLoading(data);
  });

  stream.addEventListener("agent_start", (event) => {
    const data = JSON.parse(event.data);
    upsertAgentStreamMessage({ ...data, message: "", _eventType: "start" });
  });

  stream.addEventListener("agent_reasoning_delta", (event) => {
    const data = JSON.parse(event.data);
    upsertAgentStreamMessage({ ...data, _eventType: "reasoning_delta" });
    clearDebateLoading(data);
  });

  stream.addEventListener("agent_delta", (event) => {
    const data = JSON.parse(event.data);
    upsertAgentStreamMessage({ ...data, _eventType: "delta" });
    clearDebateLoading(data);
  });

  stream.addEventListener("agent_end", (event) => {
    const data = JSON.parse(event.data);
    upsertAgentStreamMessage({ ...data, _eventType: "end" });
    clearDebateLoading(data);
  });

  stream.addEventListener("council_end", (event) => {
    const data = JSON.parse(event.data);
    const resultRoundId = normalizeRoundId(data.round) ?? 4;
    for (let r = 0; r <= resultRoundId; r++) {
      setRoundOrchestratorState(r, { completed: true });
    }
    if (resultRoundId === 4) {
      roundTabLabels.set(4, "Verdict");
      updateRoundTabMarkers();
    }
    if (currentSystemRound !== null) {
      addCompletedRoundTab(currentSystemRound);
    }
    setRoundBeginControl(null);
    if (data.summary) {
      if (resultRoundId !== null) {
        safeAddNote(data.summary, { round: resultRoundId });
      } else {
        safeAddNote(data.summary);
      }
    }

    if (data.impacts && (data.impacts.positive || data.impacts.negative)) {
      const pos = (data.impacts.positive || []).filter(Boolean);
      const neg = (data.impacts.negative || []).filter(Boolean);
      if (pos.length || neg.length) {
        const posText = pos.length ? `Positively impacted: ${pos.join(", ")}` : "Positively impacted: n/a";
        const negText = neg.length ? `Negatively impacted: ${neg.join(", ")}` : "Negatively impacted: n/a";
        if (resultRoundId !== null) {
          safeAddNote(`${posText}\n${negText}`, { round: resultRoundId });
        } else {
          safeAddNote(`${posText}\n${negText}`);
        }
      }
    }

    if (activeFeedCard) {
      const badge = activeFeedCard.querySelector(".badge");
      if (badge) {
        badge.textContent = "Completed";
        badge.classList.remove("processing");
        badge.classList.add("completed");
      }
      activeFeedCard.classList.remove("active", "processing");
      activeFeedCard = null;
      updateFeedEmptyState();
    }
    
    clearActiveState();
    stateLayer?.eachLayer?.((layer) => {
      const el = layer.getElement();
      if (el) {
        el.classList.remove("active", "positive", "negative");
      }
      layer.setStyle(getBaseStateStyle());
    });
    stateImpact.clear();

    if (data.impacts) {
      (data.impacts.positive || []).forEach((s) => setStateImpact(s, "positive"));
      (data.impacts.negative || []).forEach((s) => setStateImpact(s, "negative"));
    }

    stopDebateRunningIndicator();
    setDebateLocked(false);
    if (fetchNextBtn) {
      fetchNextBtn.disabled = false;
      fetchNextBtn.classList.remove("is-loading");
      fetchNextBtn.textContent = "Begin Resolution";
    }
  });

  stream.addEventListener("status", (event) => {
    const data = JSON.parse(event.data);
    let msg = data.message;
    if (msg) {
      if (msg.toLowerCase().includes("council stream") || msg.toLowerCase().includes("waiting for resolution")) {
        msg = "Waiting for resolution signal.";
      }
      if (!msg.toLowerCase().includes("model:") && !msg.toLowerCase().includes("provider:")) {
        const existing = systemGeneralMessagesContainer?.querySelector(".agent-speech");
        if (existing && existing.textContent.trim() === msg.trim()) {
          return;
        }
        safeAddNote(msg);
      }
    }
    if (data.model) {
      setStatusText(statusModel, `model: ${data.model}`);
    }
    if (data.connection) {
      setStatusText(statusConnection, data.connection);
    }
  });

  stream.addEventListener("error", () => {
    console.warn("[Stream] disconnected. Retrying...");
    setStatusText(statusConnection, "reconnecting");
  });
};

let topWarningTimer = null;

function showTopWarning(message) {
  const banner = document.getElementById("top-warning-banner");
  const textEl = document.getElementById("top-warning-text");
  if (!banner || !textEl) return;

  textEl.textContent = message || "Validation error";
  banner.classList.remove("banner-hide");
  banner.style.display = "flex";

  clearTimeout(topWarningTimer);
  topWarningTimer = setTimeout(() => {
    dismissTopWarning();
  }, 4000);
}

function dismissTopWarning() {
  const banner = document.getElementById("top-warning-banner");
  if (!banner || banner.style.display === "none") return;
  clearTimeout(topWarningTimer);
  banner.classList.add("banner-hide");
  setTimeout(() => {
    banner.style.display = "none";
    banner.classList.remove("banner-hide");
  }, 220);
}

function triggerInputError(element, message) {
  if (!element) return;
  const layoutEl = document.querySelector(".layout");
  if (layoutEl?.classList.contains("feed-collapsed")) {
    layoutEl.classList.remove("feed-collapsed");
  }
  element.classList.add("has-error");
  element.classList.remove("error-shake");
  void element.offsetWidth;
  element.classList.add("error-shake");
  element.focus();
  const configBox = document.getElementById("provider-config-box");
  if (element !== topicInput && configBox) {
    configBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  showTopWarning(message);
}

function clearInputError(element) {
  if (!element) return;
  element.classList.remove("has-error", "error-shake");
}

if (fetchNextBtn) {
  console.log("[BOOT] Begin Resolution button found, binding click handler");
  fetchNextBtn.addEventListener("click", async () => {
    console.log("[UI] Begin Resolution clicked, fetchLocked=", fetchLocked);
    if (fetchLocked) {
      return;
    }
    const query = topicInput?.value?.trim() || "";
    if (!query) {
      triggerInputError(topicInput, "Complaint details have not been entered.");
      return;
    }

    const providerSelect = document.getElementById("provider-select");
    const provider = providerSelect?.value || "";
    if (!provider || !PROVIDERS[provider]) {
      triggerInputError(providerSelect, "Provider has not been selected.");
      return;
    }

    const modelInput = document.getElementById("model-input");
    const modelSelect = document.getElementById("model-select");
    const isLmStudio = provider === "lmstudio";
    const model = (isLmStudio ? modelInput?.value?.trim() : modelSelect?.value?.trim()) || "";
    if (!model) {
      const modelEl = isLmStudio ? modelInput : modelSelect;
      triggerInputError(modelEl, "Model has not been selected.");
      return;
    }

    const providerConfig = PROVIDERS[provider];
    const requiresKey = providerConfig ? providerConfig.requiresKey !== false : true;
    const apiKeyInput = document.getElementById("api-key-input");
    const apiKey = apiKeyInput?.value?.trim() || "";

    if (requiresKey && !apiKey) {
      triggerInputError(apiKeyInput, "API key has not been entered.");
      return;
    }

    fetchLocked = true;
    setDebateLocked(true);
    showSummarizingState(query);
    clearActiveState();
    stateLayer?.eachLayer?.((layer) => {
      const el = layer.getElement();
      if (el) {
        el.classList.remove("active", "positive", "negative");
      }
      layer.setStyle(getBaseStateStyle());
    });
    stateImpact.clear();

    try {
      const response = await fetch("/api/next", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          query,
          provider,
          model,
          apiKey,
        }),
      });

      const resData = await response.json();
      if (!response.ok || !resData.ok) {
        throw new Error(resData?.error || "Failed to start simulation.");
      }
    } catch (error) {
      isSummarizing = false;
      stopDebateRunningIndicator();
      setDebateLocked(false);
      fetchNextBtn.disabled = false;
      fetchNextBtn.classList.remove("is-loading");
      fetchNextBtn.textContent = "Begin Resolution";
      const card = document.getElementById("active-feed-card");
      if (card && card.querySelector(".badge-summarizing")) {
        card.remove();
        activeFeedCard = null;
        updateFeedEmptyState();
      }
      showTopWarning(error.message || "Failed to start simulation.");
    }
  });
}

const PROVIDERS = {
  openrouter: {
    name: "OpenRouter",
    badge: "Cloud (Router)",
    baseUrl: "https://openrouter.ai/api/v1",
    defaultModel: "openai/gpt-6.1-sol-pro",
    exampleModels: [
      "openai/gpt-6.1-sol-pro",
      "google/gemini-3.8-flash",
      "anthropic/claude-sonnet-5-5",
      "deepseek/deepseek-r1",
    ],
    keyHint: "Required",
    keyPlaceholder: "sk-or-v1-...",
  },
  openai: {
    name: "OpenAI",
    badge: "Cloud",
    baseUrl: "https://api.openai.com/v1",
    defaultModel: "gpt-6.1-sol",
    exampleModels: [
      "gpt-6-astra",
      "gpt-6.1-sol",
      "gpt-6-luna",
      "gpt-4o",
    ],
    keyHint: "Required",
    keyPlaceholder: "sk-...",
  },
  anthropic: {
    name: "Anthropic",
    badge: "Cloud",
    baseUrl: "https://api.anthropic.com/v1",
    defaultModel: "claude-sonnet-5-5",
    exampleModels: [
      "claude-fable-5-1",
      "claude-opus-5-5",
      "claude-sonnet-5-5",
      "claude-haiku-4-5",
    ],
    keyHint: "Required",
    keyPlaceholder: "sk-ant-...",
  },
  groq: {
    name: "Groq",
    badge: "Cloud (Fast)",
    baseUrl: "https://api.groq.com/openai/v1",
    defaultModel: "openai/gpt-oss-120b",
    exampleModels: [
      "openai/gpt-oss-120b",
      "openai/gpt-oss-20b",
      "llama-3.3-70b-versatile",
      "llama-3.1-8b-instant",
    ],
    keyHint: "Required",
    keyPlaceholder: "gsk_...",
  },
  lmstudio: {
    name: "LM Studio",
    badge: "Local (Port 1235)",
    baseUrl: "http://localhost:1235/v1",
    defaultModel: "local-model",
    exampleModels: [],
    keyHint: "Not required",
    keyPlaceholder: "None (local server)",
    requiresKey: false,
    customInput: true,
  },
};


const initProviderConfig = () => {
  const providerSelect = document.getElementById("provider-select");
  const modelSelect = document.getElementById("model-select");
  const modelSelectWrapper = document.getElementById("model-select-wrapper");
  const modelInput = document.getElementById("model-input");
  const modelInputWrapper = document.getElementById("model-input-wrapper");
  const modelFieldLabel = document.getElementById("model-field-label");
  const apiKeyInput = document.getElementById("api-key-input");
  const providerBadge = document.getElementById("provider-badge");
  const apiKeyHint = document.getElementById("api-key-hint");
  const toggleKeyBtn = document.getElementById("toggle-key-visibility");

  if (!providerSelect || !modelSelect) return;

  const modelCache = {};

  const getActiveModel = () => {
    if (providerSelect?.value === "lmstudio") {
      return modelInput?.value?.trim() || "";
    }
    return modelSelect?.value?.trim() || "";
  };

  const setActiveModel = (val) => {
    if (val === undefined || val === null) return;
    if (modelInput) {
      modelInput.value = val;
    }
    if (modelSelect) {
      let matched = false;
      for (let i = 0; i < modelSelect.options.length; i++) {
        if (modelSelect.options[i].value === val) {
          modelSelect.selectedIndex = i;
          matched = true;
          break;
        }
      }
      if (!matched && val) {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = val;
        modelSelect.prepend(opt);
        modelSelect.selectedIndex = 0;
      }
    }
  };

  const updateModelMode = (providerKey) => {
    const isLmStudio = providerKey === "lmstudio";
    if (isLmStudio) {
      if (modelSelectWrapper) modelSelectWrapper.style.display = "none";
      if (modelInputWrapper) modelInputWrapper.style.display = "flex";
      if (modelFieldLabel) modelFieldLabel.setAttribute("for", "model-input");
    } else {
      if (modelSelectWrapper) modelSelectWrapper.style.display = "flex";
      if (modelInputWrapper) modelInputWrapper.style.display = "none";
      if (modelFieldLabel) modelFieldLabel.setAttribute("for", "model-select");
    }
  };

  const updateModelOptions = (providerKey, models, preferredModel) => {
    const targetModel = preferredModel || "";

    modelSelect.innerHTML = "";
    const placeholderOpt = document.createElement("option");
    placeholderOpt.value = "";
    placeholderOpt.textContent = "Select Model";
    placeholderOpt.disabled = true;
    if (!targetModel) {
      placeholderOpt.selected = true;
    }
    modelSelect.appendChild(placeholderOpt);

    const list = (models && models.length > 0) ? models : (PROVIDERS[providerKey]?.exampleModels || []);
    list.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (targetModel && m === targetModel) {
        opt.selected = true;
      }
      modelSelect.appendChild(opt);
    });

    let found = false;
    for (let i = 0; i < modelSelect.options.length; i++) {
      if (modelSelect.options[i].value === targetModel) {
        modelSelect.selectedIndex = i;
        found = true;
        break;
      }
    }
    if (!found && targetModel) {
      const opt = document.createElement("option");
      opt.value = targetModel;
      opt.textContent = targetModel;
      opt.selected = true;
      modelSelect.appendChild(opt);
      modelSelect.value = targetModel;
    }
    if (!targetModel) {
      modelSelect.value = "";
    }
  };

  const saveConfigLocally = () => {
    try {
      const payload = {
        provider: providerSelect.value,
        model: getActiveModel(),
      };
      localStorage.setItem("policy_llm_config", JSON.stringify(payload));
    } catch (e) {
    }
  };

  let syncTimer = null;
  const syncConfigToServer = () => {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(async () => {
      const provider = providerSelect.value;
      const model = getActiveModel();
      const apiKey = apiKeyInput?.value?.trim() || "";
      try {
        const res = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            provider,
            model,
            apiKey,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          if (res.status !== 409 && res.status !== 400) {
            console.warn("[Config]", body?.error);
          }
        }
      } catch (err) {
        console.warn("[Config] Could not reach server:", err.message);
      }
    }, 350);
  };

  const loadModelsDynamically = async (providerKey, isManual = false) => {
    const config = PROVIDERS[providerKey];
    if (!config) return;
    const apiKey = apiKeyInput?.value?.trim() || "";
    const currentModel = getActiveModel();

    try {
      const q = new URLSearchParams({ provider: providerKey });

      const headers = authHeaders();
      if (apiKey) headers["X-API-Key"] = apiKey;

      const res = await fetch(`/api/models?${q.toString()}`, { headers });
      const data = await res.json();

      if (data.ok && Array.isArray(data.models) && data.models.length > 0) {
        modelCache[providerKey] = data.models;
        updateModelOptions(providerKey, data.models, currentModel || "");
        if (currentModel) {
          saveConfigLocally();
          syncConfigToServer();
        }
        return;
      }

      if (data.error && isManual) {
        console.warn(`[Models] ${data.error}`);
      }
    } catch (err) {
      if (isManual) {
        console.warn(`[Models] Failed to fetch: ${err.message}`);
      }
    }

    const fallbackList = modelCache[providerKey] || config.exampleModels || [];
    updateModelOptions(providerKey, fallbackList, currentModel || "");
  };

  const applyProvider = (providerKey, isInitial = false, explicitModel = null) => {
    if (!providerKey || !PROVIDERS[providerKey]) {
      if (providerBadge) providerBadge.textContent = "None";
      if (apiKeyHint) apiKeyHint.textContent = "";
      if (apiKeyInput) {
        apiKeyInput.placeholder = "Enter API Key";
        clearInputError(apiKeyInput);
      }
      if (modelSelect) {
        modelSelect.innerHTML = '<option value="" disabled selected>Select Model</option>';
        clearInputError(modelSelect);
      }
      if (modelInput) {
        modelInput.value = "";
        clearInputError(modelInput);
      }
      updateModelMode("");
      return;
    }

    const config = PROVIDERS[providerKey];
    if (providerBadge) providerBadge.textContent = config.badge;
    if (apiKeyHint) apiKeyHint.textContent = config.keyHint;
    if (apiKeyInput) apiKeyInput.placeholder = config.keyPlaceholder;

    clearInputError(apiKeyInput);
    clearInputError(providerSelect);
    clearInputError(modelSelect);
    clearInputError(modelInput);
    dismissTopWarning();

    updateModelMode(providerKey);

    const chosenModel = explicitModel || "";
    const initialList = modelCache[providerKey] || config.exampleModels || [];
    updateModelOptions(providerKey, initialList, chosenModel);

    if (!isInitial && chosenModel) {
      setActiveModel(chosenModel);
      saveConfigLocally();
      syncConfigToServer();
    }

    if (!config.customInput) {
      loadModelsDynamically(providerKey, false);
    }
  };

  toggleKeyBtn?.addEventListener("click", () => {
    if (!apiKeyInput) return;
    const isPassword = apiKeyInput.type === "password";
    apiKeyInput.type = isPassword ? "text" : "password";
    const eyeOpen = toggleKeyBtn.querySelector(".eye-open");
    const eyeClosed = toggleKeyBtn.querySelector(".eye-closed");
    if (eyeOpen) eyeOpen.style.display = isPassword ? "none" : "block";
    if (eyeClosed) eyeClosed.style.display = isPassword ? "block" : "none";
  });

  providerSelect.addEventListener("change", () => {
    clearInputError(providerSelect);
    dismissTopWarning();
    applyProvider(providerSelect.value, false);
    if (providerSelect.value) {
      saveConfigLocally();
      syncConfigToServer();
    }
  });

  modelSelect?.addEventListener("change", () => {
    if (modelSelect.value) {
      clearInputError(modelSelect);
      dismissTopWarning();
    }
    saveConfigLocally();
    syncConfigToServer();
  });

  modelInput?.addEventListener("input", () => {
    if (modelInput.value.trim()) {
      clearInputError(modelInput);
      dismissTopWarning();
    }
    saveConfigLocally();
    syncConfigToServer();
  });

  let keyDebounceTimer = null;
  apiKeyInput?.addEventListener("input", () => {
    if (apiKeyInput.value.trim()) {
      clearInputError(apiKeyInput);
      dismissTopWarning();
    }
    saveConfigLocally();
    syncConfigToServer();
    clearTimeout(keyDebounceTimer);
    keyDebounceTimer = setTimeout(() => {
      const pCfg = PROVIDERS[providerSelect.value];
      if (pCfg && !pCfg.customInput) {
        loadModelsDynamically(providerSelect.value, false);
      }
    }, 600);
  });
  // Do not default select any provider or model
  providerSelect.value = "";
  applyProvider("", true);

  fetch("/api/config", { headers: authHeaders() })
    .then((r) => {
      if (r.status === 401) {
        window.location.href = "login.html";
        return null;
      }
      return r.json();
    })
    .catch(() => {});
};

const initUserProfileMenu = () => {
  const profileMenu = document.getElementById("user-profile-menu");
  const avatarBtn = document.getElementById("user-avatar-btn");
  const dropdown = document.getElementById("user-dropdown");
  const nameEl = document.getElementById("user-dropdown-name");
  const emailEl = document.getElementById("user-dropdown-email");
  const themeBtn = document.getElementById("user-menu-theme") || document.getElementById("user-menu-settings");
  const signoutBtn = document.getElementById("user-menu-signout");
  const avatarInitial = document.getElementById("user-dropdown-avatar");

  if (!profileMenu || !avatarBtn || !dropdown) {
    return;
  }

  const applyUserData = (user) => {
    if (!user) return;
    const email = user.email || (user.username ? `${user.username}@council.local` : "");
    let fullName = user.fullName || user.username || "Admin";
    if (fullName === "Council Member") {
      fullName = "Admin";
    }
    if (nameEl) nameEl.textContent = fullName;
    if (emailEl) {
      emailEl.textContent = email || "admin@council.local";
      emailEl.title = email || "";
    }
    const firstLetter = (fullName || email || "A").trim().charAt(0).toUpperCase();
    if (avatarInitial) {
      avatarInitial.textContent = firstLetter;
    }
    const topbarInitial = document.getElementById("user-avatar-initial");
    const topbarIcon = document.querySelector("#user-avatar-circle .user-avatar-icon");
    const topbarImg = document.getElementById("user-avatar-image");
    const dropdownImg = document.getElementById("user-dropdown-avatar-image");
    const dropdownSvg = document.querySelector("#user-dropdown-avatar svg");
    const photoUrl = user.photoURL || user.photoUrl || user.picture || user.avatarUrl || "";

    if (photoUrl) {
      if (topbarImg) {
        topbarImg.src = photoUrl;
        topbarImg.style.display = "block";
        topbarImg.onerror = () => {
          topbarImg.style.display = "none";
          if (topbarInitial && firstLetter) {
            topbarInitial.style.display = "block";
          } else if (topbarIcon) {
            topbarIcon.style.display = "block";
          }
        };
      }
      if (topbarInitial) topbarInitial.style.display = "none";
      if (topbarIcon) topbarIcon.style.display = "none";

      if (dropdownImg) {
        dropdownImg.src = photoUrl;
        dropdownImg.style.display = "block";
        dropdownImg.onerror = () => {
          dropdownImg.style.display = "none";
          if (dropdownSvg) dropdownSvg.style.display = "block";
          if (avatarInitial) avatarInitial.textContent = firstLetter;
        };
        if (dropdownSvg) dropdownSvg.style.display = "none";
      }
    } else {
      if (topbarImg) topbarImg.style.display = "none";
      if (dropdownImg) dropdownImg.style.display = "none";
      if (dropdownSvg) dropdownSvg.style.display = "block";
      if (topbarInitial && firstLetter) {
        topbarInitial.textContent = firstLetter;
        topbarInitial.style.display = "block";
        if (topbarIcon) topbarIcon.style.display = "none";
      } else if (topbarIcon) {
        topbarIcon.style.display = "block";
      }
      if (avatarInitial) {
        avatarInitial.textContent = firstLetter;
      }
    }
  };

  try {
    const cached = localStorage.getItem("agenticCouncilUser");
    if (cached) {
      const u = JSON.parse(cached);
      applyUserData(u);
    }
  } catch (e) {}

  fetch("/api/auth/me", { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : null))
    .then((res) => {
      if (res?.user) {
        localStorage.setItem("agenticCouncilUser", JSON.stringify(res.user));
        applyUserData(res.user);
      }
    })
    .catch(() => {});

  if (window.firebase && typeof window.firebase.auth === "function") {
    try {
      const auth = window.firebase.auth();
      auth.onAuthStateChanged((fbUser) => {
        if (fbUser && fbUser.photoURL) {
          const cachedRaw = localStorage.getItem("agenticCouncilUser");
          const cachedUser = cachedRaw ? JSON.parse(cachedRaw) : {};
          if (!cachedUser.photoURL) {
            cachedUser.photoURL = fbUser.photoURL;
            localStorage.setItem("agenticCouncilUser", JSON.stringify(cachedUser));
          }
          applyUserData({ ...cachedUser, photoURL: fbUser.photoURL });
        }
      });
    } catch (_) {}
  }

  const toggleDropdown = (open) => {
    const isOpen = typeof open === "boolean" ? open : !dropdown.classList.contains("open");
    dropdown.classList.toggle("open", isOpen);
    profileMenu.classList.toggle("is-active", isOpen);
    avatarBtn.setAttribute("aria-expanded", String(isOpen));
  };

  avatarBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDropdown();
  });

  document.addEventListener("click", (e) => {
    if (!profileMenu.contains(e.target)) {
      toggleDropdown(false);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && dropdown.classList.contains("open")) {
      toggleDropdown(false);
      avatarBtn.focus();
    }
  });

  themeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleTheme();
  });

  signoutBtn?.addEventListener("click", async () => {
    toggleDropdown(false);
    try {
      localStorage.removeItem("agenticCouncilIdToken");
      localStorage.removeItem("agenticCouncilUser");
      sessionStorage.clear();
      if (window.firebase && typeof window.firebase.auth === "function") {
        try {
          const auth = window.firebase.auth();
          if (auth && typeof auth.signOut === "function") {
            await auth.signOut();
          }
        } catch (_) {}
      }
    } finally {
      window.location.href = "/logout";
    }
  });
};

const applyTheme = (theme, persist = true) => {
  const effectiveTheme = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", effectiveTheme);
  if (persist) {
    try {
      localStorage.setItem("policy_sim_theme", effectiveTheme);
    } catch (e) {}
  }

  // Update theme badge in user menu
  const themeBadge = document.getElementById("theme-status-badge");
  if (themeBadge) {
    themeBadge.textContent = effectiveTheme === "light" ? "Light" : "Dark";
  }

  // Update icons in user menu
  const darkIcon = document.querySelector(".theme-icon-dark");
  const lightIcon = document.querySelector(".theme-icon-light");
  if (darkIcon && lightIcon) {
    if (effectiveTheme === "light") {
      darkIcon.style.display = "none";
      lightIcon.style.display = "block";
    } else {
      darkIcon.style.display = "block";
      lightIcon.style.display = "none";
    }
  }

  // Update Leaflet map canvas theme
  updateMapTheme(effectiveTheme);
};

const toggleTheme = () => {
  const current = getCurrentTheme();
  const next = current === "light" ? "dark" : "light";
  applyTheme(next, true);
};

const initTheme = () => {
  let savedTheme = "dark";
  try {
    savedTheme = localStorage.getItem("policy_sim_theme") || "dark";
  } catch (e) {}
  applyTheme(savedTheme, false);
};

ensureSystemNotesContainer();
ensureAgentsContainer();
initTheme();
initProviderConfig();
initUserProfileMenu();
document.querySelectorAll(".agent-avatar").forEach((avatar) => {
  if (avatar.querySelector("video") && avatar.querySelector("canvas")) {
    initTransparentVideoCanvas(avatar);
  }
});

connectLiveStream();


