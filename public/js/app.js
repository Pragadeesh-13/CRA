/**
 * Complaint Resolution Agent (CRA) - Troy
 * Frontend Client Application
 */

// ── Auth & Headers ──────────────────────────────────────────────────────────

const authHeaders = () => {
  const token = localStorage.getItem("agenticCouncilIdToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
};

// ── Theme Management ────────────────────────────────────────────────────────

const getCurrentTheme = () => {
  return document.documentElement.getAttribute("data-theme") || "dark";
};

const applyTheme = (theme, persist = true) => {
  const effectiveTheme = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", effectiveTheme);
  if (persist) {
    try {
      localStorage.setItem("cra_troy_theme", effectiveTheme);
      localStorage.setItem("policy_sim_theme", effectiveTheme);
    } catch (_) {}
  }

  const themeBadge = document.getElementById("theme-status-badge");
  if (themeBadge) {
    themeBadge.textContent = effectiveTheme === "light" ? "Light" : "Dark";
  }

  const darkIcon = document.querySelector(".theme-icon-dark");
  const lightIcon = document.querySelector(".theme-icon-light");
  if (darkIcon && lightIcon) {
    darkIcon.style.display = effectiveTheme === "light" ? "none" : "block";
    lightIcon.style.display = effectiveTheme === "light" ? "block" : "none";
  }
};

const toggleTheme = () => {
  const next = getCurrentTheme() === "light" ? "dark" : "light";
  applyTheme(next, true);
};

const initTheme = () => {
  let savedTheme = "dark";
  try {
    savedTheme = localStorage.getItem("cra_troy_theme") || localStorage.getItem("policy_sim_theme") || "dark";
  } catch (_) {}
  applyTheme(savedTheme, false);
};

// ── UI Alerts & Warnings ────────────────────────────────────────────────────

let topWarningTimer = null;

const showTopWarning = (message) => {
  const banner = document.getElementById("top-warning-banner");
  const textEl = document.getElementById("top-warning-text");
  if (!banner || !textEl) return;
  textEl.textContent = message || "An unexpected error occurred.";
  banner.style.display = "flex";

  clearTimeout(topWarningTimer);
  topWarningTimer = setTimeout(() => {
    banner.style.display = "none";
  }, 6000);
};

const dismissTopWarning = () => {
  const banner = document.getElementById("top-warning-banner");
  if (banner) banner.style.display = "none";
  clearTimeout(topWarningTimer);
};

const triggerInputError = (el, message = "") => {
  if (!el) return;
  el.classList.add("input-error");
  el.focus();
  if (message) showTopWarning(message);
};

const clearInputError = (el) => {
  if (!el) return;
  el.classList.remove("input-error");
};

// ── Providers & LLM Config ──────────────────────────────────────────────────

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
    defaultModel: "gpt-4o",
    exampleModels: [
      "gpt-4o",
      "gpt-4o-mini",
      "gpt-4-turbo",
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
      "claude-sonnet-5-5",
      "claude-haiku-4-5",
      "claude-opus-5-5",
    ],
    keyHint: "Required",
    keyPlaceholder: "sk-ant-...",
  },
  groq: {
    name: "Groq",
    badge: "Cloud (Fast)",
    baseUrl: "https://api.groq.com/openai/v1",
    defaultModel: "llama-3.3-70b-versatile",
    exampleModels: [
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
    defaultModel: "qwen/qwen3.5-9b",
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
    if (!val) return;
    if (modelInput) modelInput.value = val;
    if (modelSelect) {
      let matched = false;
      for (let i = 0; i < modelSelect.options.length; i++) {
        if (modelSelect.options[i].value === val) {
          modelSelect.selectedIndex = i;
          matched = true;
          break;
        }
      }
      if (!matched) {
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
    if (!targetModel) placeholderOpt.selected = true;
    modelSelect.appendChild(placeholderOpt);

    const list = models && models.length > 0 ? models : PROVIDERS[providerKey]?.exampleModels || [];
    list.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (targetModel && m === targetModel) opt.selected = true;
      modelSelect.appendChild(opt);
    });

    if (targetModel && !Array.from(modelSelect.options).some((o) => o.value === targetModel)) {
      const opt = document.createElement("option");
      opt.value = targetModel;
      opt.textContent = targetModel;
      opt.selected = true;
      modelSelect.appendChild(opt);
    }
  };

  const saveConfigLocally = () => {
    try {
      const payload = {
        provider: providerSelect.value,
        model: getActiveModel(),
      };
      localStorage.setItem("cra_troy_config", JSON.stringify(payload));
    } catch (_) {}
  };

  let syncTimer = null;
  const syncConfigToServer = () => {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(async () => {
      const provider = providerSelect.value;
      const model = getActiveModel();
      const apiKey = apiKeyInput?.value?.trim() || "";
      if (!provider) return;
      try {
        await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ provider, model, apiKey }),
        });
      } catch (err) {
        console.warn("[Config sync error]", err);
      }
    }, 400);
  };

  const loadModelsDynamically = async (providerKey) => {
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
        updateModelOptions(providerKey, data.models, currentModel);
        return;
      }
    } catch (_) {}

    updateModelOptions(providerKey, modelCache[providerKey] || config.exampleModels || [], currentModel);
  };

  const applyProvider = (providerKey, explicitModel = null) => {
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

    const chosenModel = explicitModel || (providerKey === "lmstudio" ? "qwen/qwen3.5-9b" : config.defaultModel);
    if (providerKey === "lmstudio") {
      if (modelInput) modelInput.value = chosenModel;
    } else {
      updateModelOptions(providerKey, modelCache[providerKey] || config.exampleModels || [], chosenModel);
      loadModelsDynamically(providerKey);
    }

    saveConfigLocally();
    syncConfigToServer();
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
    applyProvider(providerSelect.value);
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

  apiKeyInput?.addEventListener("input", () => {
    if (apiKeyInput.value.trim()) {
      clearInputError(apiKeyInput);
      dismissTopWarning();
    }
    saveConfigLocally();
    syncConfigToServer();
  });

  // Load existing config from server
  fetch("/api/config", { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (cfg && cfg.provider) {
        providerSelect.value = cfg.provider;
        applyProvider(cfg.provider, cfg.model);
        if (cfg.maskedKey && apiKeyInput) {
          apiKeyInput.placeholder = cfg.maskedKey;
        }
      } else {
        // Try local storage
        try {
          const raw = localStorage.getItem("cra_troy_config");
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed.provider) {
              providerSelect.value = parsed.provider;
              applyProvider(parsed.provider, parsed.model);
            }
          }
        } catch (_) {}
      }
    })
    .catch(() => {});
};

// ── User Profile Menu ───────────────────────────────────────────────────────

const initUserProfileMenu = () => {
  const profileMenu = document.getElementById("user-profile-menu");
  const avatarBtn = document.getElementById("user-avatar-btn");
  const dropdown = document.getElementById("user-dropdown");
  const nameEl = document.getElementById("user-dropdown-name");
  const emailEl = document.getElementById("user-dropdown-email");
  const themeBtn = document.getElementById("user-menu-theme");
  const signoutBtn = document.getElementById("user-menu-signout");
  const avatarInitial = document.getElementById("user-dropdown-avatar");

  if (!profileMenu || !avatarBtn || !dropdown) return;

  const applyUserData = (user) => {
    if (!user) return;
    const email = user.email || "admin@cra.local";
    let fullName = user.fullName || user.username || "Admin";
    if (nameEl) nameEl.textContent = fullName;
    if (emailEl) emailEl.textContent = email;
    const firstLetter = fullName.trim().charAt(0).toUpperCase() || "A";
    if (avatarInitial) avatarInitial.textContent = firstLetter;
    const topbarInitial = document.getElementById("user-avatar-initial");
    if (topbarInitial) {
      topbarInitial.textContent = firstLetter;
      topbarInitial.style.display = "block";
    }
  };

  fetch("/api/auth/me", { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : null))
    .then((res) => {
      if (res?.user) applyUserData(res.user);
    })
    .catch(() => {});

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
    if (!profileMenu.contains(e.target)) toggleDropdown(false);
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

  signoutBtn?.addEventListener("click", () => {
    localStorage.removeItem("agenticCouncilIdToken");
    localStorage.removeItem("agenticCouncilUser");
    sessionStorage.clear();
    window.location.href = "/logout";
  });
};

// ── Transparent Video Canvas Avatar ─────────────────────────────────────────

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
      const cropSize = Math.min(vw, vh) * 0.68;
      const sHeight = cropSize;
      const sWidth = cropSize * 0.86;
      const sx = (vw - sWidth) / 2;
      const sy = (vh - sHeight) / 2;

      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(video, sx, sy, sWidth, sHeight, 0, 0, w, h);
      const imgData = ctx.getImageData(0, 0, w, h);
      const data = imgData.data;
      for (let i = 0; i < data.length; i += 4) {
        const brightness = Math.max(data[i], data[i + 1], data[i + 2]);
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

// ── Markdown Parser & Progressive DOM Streamer ──────────────────────────────

const renderMarkdown = (text) => {
  if (!text) return "";

  let src = text.replace(/\r\n/g, "\n").trim();
  src = src.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  src = src.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  src = src.replace(/`([^`]+)`/g, "<code>$1</code>");

  // Handle blockquotes: transform lines starting with > into customer quotation box and strip >
  src = src.replace(/((?:^|\n)(?:>[^\n]*\n?)+)/g, (match) => {
    const lines = match
      .split("\n")
      .map((line) => line.replace(/^\s*>\s?/, "").trim())
      .filter((line, idx, arr) => line || (idx > 0 && idx < arr.length - 1 && arr[idx - 1]));
    return `\n\n<blockquote class="customer-quote-box">\n${lines.join("<br/>")}\n</blockquote>\n\n`;
  });
  // Strip any remaining > at line starts or standalone
  src = src.replace(/^\s*>\s*/gm, "");

  // Normalize headings by ensuring double newlines around them
  src = src.replace(/^(#{1,6}\s+[^\n]+)/gm, "\n\n$1\n\n").trim();

  const rawBlocks = src.split(/\n{2,}/);
  const formattedBlocks = [];

  for (const block of rawBlocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;

    if (trimmed.startsWith("<blockquote") && trimmed.endsWith("</blockquote>")) {
      formattedBlocks.push(trimmed);
      continue;
    }

    if (/^###\s+(.*)$/.test(trimmed)) {
      formattedBlocks.push(`<h4>${trimmed.replace(/^###\s+/, "")}</h4>`);
      continue;
    }
    if (/^##\s+(.*)$/.test(trimmed)) {
      formattedBlocks.push(`<h3>${trimmed.replace(/^##\s+/, "")}</h3>`);
      continue;
    }
    if (/^#\s+(.*)$/.test(trimmed)) {
      formattedBlocks.push(`<h2>${trimmed.replace(/^#\s+/, "")}</h2>`);
      continue;
    }

    if (/^[-*]\s+/m.test(trimmed)) {
      const items = trimmed
        .split(/\n/)
        .map((line) => line.replace(/^[-*]\s+/, "").trim())
        .filter(Boolean)
        .map((item) => `<li>${item}</li>`)
        .join("");
      formattedBlocks.push(`<ul>${items}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/m.test(trimmed)) {
      const items = trimmed
        .split(/\n/)
        .map((line) => line.replace(/^\d+\.\s+/, "").trim())
        .filter(Boolean)
        .map((item) => `<li>${item}</li>`)
        .join("");
      formattedBlocks.push(`<ol>${items}</ol>`);
      continue;
    }

    const paragraphContent = trimmed.replace(/\n/g, "<br/>");
    formattedBlocks.push(`<p>${paragraphContent}</p>`);
  }

  return formattedBlocks.join("\n");
};

const parseInlineSpans = (line) => {
  const spans = [];
  // Strip any leading > or whitespace
  const cleanLine = line.replace(/^\s*>\s*/, "");
  const regex = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|[^*`]+)/g;
  let match;
  while ((match = regex.exec(cleanLine)) !== null) {
    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      spans.push({ type: "strong", text: token.slice(2, -2) });
    } else if (token.startsWith("*") && token.endsWith("*")) {
      spans.push({ type: "em", text: token.slice(1, -1) });
    } else if (token.startsWith("`") && token.endsWith("`")) {
      spans.push({ type: "code", text: token.slice(1, -1) });
    } else {
      // Remove any dangling > characters in plain text
      const cleanToken = token.replace(/(?:^|\s)>\s*/g, " ");
      spans.push({ type: "text", text: cleanToken });
    }
  }
  return spans;
};

const parseMarkdownToStreamItems = (text) => {
  if (!text) return [];
  const src = text.replace(/\r\n/g, "\n").trim();
  // Ensure double newlines around blockquotes and headings
  let normalized = src.replace(/((?:^|\n)(?:>[^\n]*\n?)+)/g, "\n\n$1\n\n");
  normalized = normalized.replace(/^(#{1,6}\s+[^\n]+)/gm, "\n\n$1\n\n").trim();
  const rawBlocks = normalized.split(/\n{2,}/);
  const items = [];

  for (const block of rawBlocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;

    if (/^###\s+(.*)$/.test(trimmed)) {
      items.push({ tag: "h4", text: trimmed.replace(/^###\s+/, "").trim() });
      continue;
    }
    if (/^##\s+(.*)$/.test(trimmed)) {
      items.push({ tag: "h3", text: trimmed.replace(/^##\s+/, "").trim() });
      continue;
    }
    if (/^#\s+(.*)$/.test(trimmed)) {
      items.push({ tag: "h2", text: trimmed.replace(/^#\s+/, "").trim() });
      continue;
    }

    // Blockquote handling (customer communication draft)
    if (/^>\s*/m.test(trimmed)) {
      const quoteLines = trimmed
        .split(/\n/)
        .map((line) => line.replace(/^\s*>\s?/, "").trim())
        .filter((line, idx, arr) => line || (idx > 0 && idx < arr.length - 1 && arr[idx - 1]));
      const quoteSpans = [];
      for (let i = 0; i < quoteLines.length; i++) {
        if (i > 0) quoteSpans.push({ type: "br" });
        quoteSpans.push(...parseInlineSpans(quoteLines[i]));
      }
      items.push({ tag: "blockquote", className: "customer-quote-box", spans: quoteSpans });
      continue;
    }

    if (/^[-*]\s+/m.test(trimmed)) {
      const listLines = trimmed.split(/\n/).map((l) => l.replace(/^[-*]\s+/, "").trim()).filter(Boolean);
      items.push({ tag: "ul", listItems: listLines.map(parseInlineSpans) });
      continue;
    }

    if (/^\d+\.\s+/m.test(trimmed)) {
      const listLines = trimmed.split(/\n/).map((l) => l.replace(/^\d+\.\s+/, "").trim()).filter(Boolean);
      items.push({ tag: "ol", listItems: listLines.map(parseInlineSpans) });
      continue;
    }

    const lines = trimmed.split(/\n/);
    const paraSpans = [];
    for (let i = 0; i < lines.length; i++) {
      if (i > 0) paraSpans.push({ type: "br" });
      paraSpans.push(...parseInlineSpans(lines[i]));
    }
    items.push({ tag: "p", spans: paraSpans });
  }

  return items;
};

const buildStreamingActions = (items) => {
  const actions = [];

  for (const item of items) {
    actions.push({ type: "create_block", tag: item.tag, className: item.className });

    if (item.text) {
      const words = item.text.split(/\s+/).filter(Boolean);
      for (let i = 0; i < words.length; i++) {
        actions.push({ type: "append_text", text: (i > 0 ? " " : "") + words[i] });
      }
    } else if (item.spans) {
      for (const span of item.spans) {
        if (span.type === "br") {
          actions.push({ type: "append_br" });
        } else if (span.type === "strong" || span.type === "em" || span.type === "code") {
          actions.push({ type: "enter_inline", tag: span.type });
          const words = span.text.split(/\s+/).filter(Boolean);
          for (let i = 0; i < words.length; i++) {
            actions.push({ type: "append_text", text: (i > 0 ? " " : "") + words[i] });
          }
          actions.push({ type: "exit_inline" });
        } else {
          const words = span.text.split(/(\s+)/).filter(Boolean);
          for (const w of words) {
            actions.push({ type: "append_text", text: w });
          }
        }
      }
    } else if (item.listItems) {
      for (const liSpans of item.listItems) {
        actions.push({ type: "enter_li" });
        for (const span of liSpans) {
          if (span.type === "strong" || span.type === "em" || span.type === "code") {
            actions.push({ type: "enter_inline", tag: span.type });
            const words = span.text.split(/\s+/).filter(Boolean);
            for (let i = 0; i < words.length; i++) {
              actions.push({ type: "append_text", text: (i > 0 ? " " : "") + words[i] });
            }
            actions.push({ type: "exit_inline" });
          } else {
            const words = span.text.split(/(\s+)/).filter(Boolean);
            for (const w of words) {
              actions.push({ type: "append_text", text: w });
            }
          }
        }
        actions.push({ type: "exit_li" });
      }
    }
  }

  return actions;
};

// ── CRA Troy UI State & Console ─────────────────────────────────────────────

let isResolving = false;
let isTypingTransformed = false;
let pendingResolutionEnd = false;
let streamActionQueue = [];
let streamTickerTimer = null;
let streamedSpeechText = "";

const deliberationList = document.getElementById("deliberation-list");
const topicInput = document.getElementById("topic-input");
const fetchNextBtn = document.getElementById("fetch-next");
const topicText = document.getElementById("topic-text");
const feedBody = document.getElementById("feed-body");
const feedEmpty = document.getElementById("feed-empty");

const setStatusTopic = (title) => {
  if (topicText) {
    topicText.textContent = `Current Complaint: ${title || "Awaiting update"}`;
  }
};

const updateFeedEmptyState = () => {
  const cards = feedBody?.querySelectorAll(".feed-card");
  if (feedEmpty) {
    feedEmpty.style.display = cards && cards.length > 0 ? "none" : "block";
  }
};

const showSummarizingFeedCard = (complaintText) => {
  let card = document.getElementById("active-feed-card");
  if (!card) {
    card = document.createElement("div");
    card.id = "active-feed-card";
    card.className = "feed-card active processing";
    feedBody?.prepend(card);
  }
  card.className = "feed-card active processing";
  card.innerHTML = `
    <div class="feed-card__header">
      <span class="badge processing">Processing...</span>
    </div>
    <h3 class="summarizing-pulse">Analyzing complaint ticket...</h3>
    <p>${complaintText ? complaintText.slice(0, 140) + "..." : "Extracting intent & details..."}</p>
  `;
  updateFeedEmptyState();
};

const setActiveFeedCard = (story) => {
  let card = document.getElementById("active-feed-card");
  if (!card) {
    card = document.createElement("div");
    card.id = "active-feed-card";
    feedBody?.prepend(card);
  }
  card.className = "feed-card active";
  const title = story.title || "Customer Complaint";
  const summary = story.summary || story.description || title;
  const time = story.publishedAt ? new Date(story.publishedAt).toLocaleTimeString() : "Just now";

  card.innerHTML = `
    <div class="feed-card__header">
      <span class="badge">Active</span>
    </div>
    <h3>${title}</h3>
    <p>${summary}</p>
    <div style="font-size:10px; color:var(--muted); margin-top:6px;">${story.source || "Customer Ticket"} • ${time}</div>
  `;
  updateFeedEmptyState();
};

const markFeedCardResolved = () => {
  const card = document.getElementById("active-feed-card");
  if (card) {
    card.classList.remove("active", "processing");
    const badge = card.querySelector(".badge");
    if (badge) {
      badge.textContent = "Resolved";
      badge.className = "badge completed";
    }
  }
};

// ── Troy Deliberation / Resolution Panel ────────────────────────────────────

let currentBlockEl = null;
let currentInlineEl = null;
let currentLiEl = null;

const stopTransformedStreamTicker = () => {
  if (streamTickerTimer) {
    clearInterval(streamTickerTimer);
    streamTickerTimer = null;
  }
};

const executeStreamAction = (action, container) => {
  if (!action || !container) return;

  switch (action.type) {
    case "create_block":
      currentBlockEl = document.createElement(action.tag);
      if (action.className) currentBlockEl.className = action.className;
      currentInlineEl = null;
      currentLiEl = null;
      container.appendChild(currentBlockEl);
      break;

    case "enter_inline":
      if (currentBlockEl) {
        currentInlineEl = document.createElement(action.tag);
        const parent = currentLiEl || currentBlockEl;
        parent.appendChild(currentInlineEl);
      }
      break;

    case "exit_inline":
      currentInlineEl = null;
      break;

    case "enter_li":
      if (currentBlockEl) {
        currentLiEl = document.createElement("li");
        currentInlineEl = null;
        currentBlockEl.appendChild(currentLiEl);
      }
      break;

    case "exit_li":
      currentLiEl = null;
      currentInlineEl = null;
      break;

    case "append_br":
      const brParent = currentLiEl || currentBlockEl || container;
      brParent.appendChild(document.createElement("br"));
      break;

    case "append_text":
      const textParent = currentInlineEl || currentLiEl || currentBlockEl || container;
      textParent.appendChild(document.createTextNode(action.text));
      break;
  }
};

const finishTransformedStream = (fullText = "") => {
  stopTransformedStreamTicker();
  const troySpeech = document.getElementById("troy-speech");
  const troyCard = document.getElementById("troy-card") || document.getElementById("booting-card");

  if (troyCard) troyCard.classList.remove("speaking");
  if (troySpeech) {
    troySpeech.className = "agent-speech troy-speech";
    // Execute all remaining queued actions instantly
    if (streamActionQueue.length > 0) {
      while (streamActionQueue.length > 0) {
        const action = streamActionQueue.shift();
        executeStreamAction(action, troySpeech);
      }
    } else if (fullText) {
      troySpeech.innerHTML = renderMarkdown(fullText);
    }
  }

  isTypingTransformed = false;
  updateLiveStatus("Resolution complete & formulated by Troy.", false);

  if (pendingResolutionEnd || !isResolving) {
    pendingResolutionEnd = false;
    isResolving = false;
    markFeedCardResolved();
    if (fetchNextBtn) {
      fetchNextBtn.disabled = false;
      fetchNextBtn.classList.remove("is-loading");
      fetchNextBtn.textContent = "Begin Resolution";
    }
  }
};

const startTransformedStream = (rawText) => {
  stopTransformedStreamTicker();

  const textToParse = (rawText || streamedSpeechText || "").trim();
  if (!textToParse) {
    finishTransformedStream("");
    return;
  }

  // If still cycling through the 5-second ML messages, store for onMlCycleComplete!
  if (isMlCycling) {
    cachedResolutionText = textToParse;
    return;
  }

  // Ensure preliminary assessment has been presented before resolution starts
  if (!hasDeliveredPreliminary && cachedCraReport) {
    deliverPreliminaryAssessment(cachedCraReport);
  }

  const troySpeech = document.getElementById("troy-speech");
  const troyCard = document.getElementById("troy-card") || document.getElementById("booting-card");
  if (!troySpeech) return;

  if (troyCard) troyCard.classList.add("speaking");
  troySpeech.className = "agent-speech troy-speech streaming";
  troySpeech.innerHTML = "";

  const items = parseMarkdownToStreamItems(textToParse);
  streamActionQueue = buildStreamingActions(items);
  isTypingTransformed = true;
  updateLiveStatus("Troy is delivering resolution assessment...", true);

  // Speed adjustments: faster for longer texts so users never wait more than ~4 seconds
  const totalActions = streamActionQueue.length;
  const actionsPerTick = totalActions > 300 ? 3 : totalActions > 140 ? 2 : 1;
  const tickIntervalMs = 15;

  streamTickerTimer = setInterval(() => {
    if (!isTypingTransformed || streamActionQueue.length === 0) {
      finishTransformedStream();
      return;
    }

    for (let i = 0; i < actionsPerTick && streamActionQueue.length > 0; i++) {
      const action = streamActionQueue.shift();
      executeStreamAction(action, troySpeech);
    }

    if (deliberationList) {
      deliberationList.scrollTop = deliberationList.scrollHeight;
    }

    if (streamActionQueue.length === 0) {
      finishTransformedStream();
    }
  }, tickIntervalMs);
};

// ── ML Model Analyzing Animation & Preliminary Assessment ──────────────────

const ML_ANALYZING_STEPS = [
  "Running Fine-Tuned DistilBERT Category Classifier...",
  "Extracting ticket entities, order references, and monetary values...",
  "Executing SBERT Vector Semantic Search across 248K historical precedents...",
  "Evaluating XGBoost SLA timeline estimation & breach risk...",
  "Synthesizing historical resolution patterns & department routing rules...",
];

let mlCycleInterval = null;
let mlCycleIndex = 0;
let mlCycleTimer = null;
let cachedCraReport = null;
let cachedResolutionText = null;
let hasDeliveredPreliminary = false;
let isMlCycling = false;

const startMlAnalyzingCycle = () => {
  stopMlAnalyzingCycle();
  mlCycleIndex = 0;
  isMlCycling = true;
  hasDeliveredPreliminary = false;
  cachedResolutionText = null;

  const troySpeech = document.getElementById("troy-speech");
  if (troySpeech) {
    troySpeech.className = "agent-speech troy-speech formulating";
    troySpeech.innerHTML = `
      <div class="troy-deliberating">
        <span class="troy-pulse-orb"></span>
        <span class="troy-analyzing-text" id="troy-analyzing-msg">${ML_ANALYZING_STEPS[0]}</span>
      </div>
    `;
  }
  updateLiveStatus("Troy: " + ML_ANALYZING_STEPS[0], true);

  // Cycle messages every 1000ms
  mlCycleInterval = setInterval(() => {
    mlCycleIndex++;
    const msg = ML_ANALYZING_STEPS[mlCycleIndex % ML_ANALYZING_STEPS.length];
    const msgEl = document.getElementById("troy-analyzing-msg");
    if (msgEl) {
      msgEl.textContent = msg;
    }
    updateLiveStatus("Troy: " + msg, true);
  }, 1000);

  // Complete cycling after 5 seconds exactly
  mlCycleTimer = setTimeout(() => {
    onMlCycleComplete();
  }, 5000);
};

const stopMlAnalyzingCycle = () => {
  if (mlCycleInterval) {
    clearInterval(mlCycleInterval);
    mlCycleInterval = null;
  }
  if (mlCycleTimer) {
    clearTimeout(mlCycleTimer);
    mlCycleTimer = null;
  }
  isMlCycling = false;
};

const onMlCycleComplete = () => {
  stopMlAnalyzingCycle();
  deliverPreliminaryAssessment(cachedCraReport);

  // If LM Studio has already finished generating during the 5s cycle, stream resolution now!
  if (cachedResolutionText) {
    startTransformedStream(cachedResolutionText);
  } else {
    // Still waiting on LM Studio: show final formulation status in Troy's speech bubble
    const troySpeech = document.getElementById("troy-speech");
    if (troySpeech && !isTypingTransformed) {
      troySpeech.className = "agent-speech troy-speech formulating";
      troySpeech.innerHTML = `
        <div class="troy-deliberating">
          <span class="troy-pulse-orb"></span>
          <span>Synthesizing formal resolution assessment & customer communication...</span>
        </div>
      `;
    }
    updateLiveStatus("Troy is finalizing resolution assessment with LM Studio...", true);
  }
};

const deliverPreliminaryAssessment = (report) => {
  if (hasDeliveredPreliminary) return;
  hasDeliveredPreliminary = true;

  // Remove old preliminary card and old intelligence card if present
  const oldPrelim = document.getElementById("troy-preliminary-card");
  if (oldPrelim) oldPrelim.remove();
  const oldMlCard = document.getElementById("cra-ml-card");
  if (oldMlCard) oldMlCard.remove();

  const cat = report?.predicted_category || "Subscription";
  const conf = Math.round((report?.category_confidence || 0.65) * 100);
  const team = report?.recommended_team || "Billing Team";
  const sla = report?.sla?.estimated_hours || 18.0;
  const slaRisk = report?.sla?.sla_risk || "Low";
  const similarCount = report?.similar_cases?.length || 3;
  const topMatch = report?.similar_cases?.[0] ? `${Math.round(report.similar_cases[0].similarity * 100)}% match` : "72.5% match";

  const refs = report?.extracted_entities?.references || [];
  const dates = report?.extracted_entities?.dates || [];
  const amounts = report?.extracted_entities?.amounts || [];
  const entitiesList = [...refs, ...dates, ...amounts];

  const entitiesHtml = entitiesList.length > 0
    ? `<div class="preliminary-entities">
        <span>Identified Parameters:</span>
        ${entitiesList.map(e => `<span class="preliminary-entity-tag">${e}</span>`).join("")}
       </div>`
    : "";

  const prelimCard = document.createElement("div");
  prelimCard.id = "troy-preliminary-card";
  prelimCard.className = "agent-card troy preliminary-card";
  prelimCard.innerHTML = `
    <div class="agent-avatar">
      <video class="agent-avatar-video" autoplay loop muted playsinline>
        <source src="/assets/images/AI_spin.webm" type="video/webm" />
      </video>
      <canvas class="agent-avatar-canvas" width="96" height="96"></canvas>
    </div>
    <div>
      <div class="agent-name">Troy</div>
      <div class="agent-speech troy-speech preliminary-speech" id="troy-preliminary-speech">
        <div class="preliminary-assessment-card">
          <div class="preliminary-header">
            <div class="preliminary-title">Preliminary Case Assessment</div>
            <span class="preliminary-models-badge">DistilBERT + SBERT + XGBoost</span>
          </div>
          <div class="preliminary-desc">
            Autonomous case intake assessment synthesized via Fine-Tuned DistilBERT Classifier and vector similarity search.
          </div>
          <div class="preliminary-grid">
            <div class="preliminary-item">
              <span class="preliminary-label">Predicted Category</span>
              <span class="preliminary-val">${cat} <span class="preliminary-highlight">(${conf}% confidence)</span></span>
            </div>
            <div class="preliminary-item">
              <span class="preliminary-label">Department Routing</span>
              <span class="preliminary-val">${team} <span class="preliminary-highlight">(Direct Routing)</span></span>
            </div>
            <div class="preliminary-item">
              <span class="preliminary-label">Target SLA Timeline</span>
              <span class="preliminary-val">${sla} Hours <span class="preliminary-highlight">(Risk: ${slaRisk})</span></span>
            </div>
            <div class="preliminary-item">
              <span class="preliminary-label">Historical Precedents</span>
              <span class="preliminary-val">${similarCount} Matched Cases <span class="preliminary-highlight">(${topMatch})</span></span>
            </div>
          </div>
          ${entitiesHtml}
        </div>
      </div>
    </div>
  `;

  // Insert immediately before Troy's main resolution card
  const troyCard = document.getElementById("troy-card") || document.getElementById("booting-card");
  if (troyCard && deliberationList) {
    deliberationList.insertBefore(prelimCard, troyCard);
  } else if (deliberationList) {
    deliberationList.appendChild(prelimCard);
  }

  // Initialize transparent video canvas for the preliminary avatar
  const avatarEl = prelimCard.querySelector(".agent-avatar");
  if (avatarEl) {
    initTransparentVideoCanvas(avatarEl);
  }
};

const resetTroyConsole = () => {
  stopTransformedStreamTicker();
  stopMlAnalyzingCycle();
  isTypingTransformed = false;
  pendingResolutionEnd = false;
  streamActionQueue = [];
  streamedSpeechText = "";
  cachedCraReport = null;
  cachedResolutionText = null;
  hasDeliveredPreliminary = false;

  // Ensure live status banner exists
  let statusBanner = document.getElementById("cra-live-status");
  if (!statusBanner) {
    statusBanner = document.createElement("div");
    statusBanner.id = "cra-live-status";
    statusBanner.className = "cra-live-status";
    deliberationList?.prepend(statusBanner);
  }
  statusBanner.className = "cra-live-status running";
  statusBanner.innerHTML = `<span class="status-dot"></span><span>Processing Complaint with CRA ML Engine...</span>`;

  // Remove old preliminary card and ML card if any
  const oldPrelim = document.getElementById("troy-preliminary-card");
  if (oldPrelim) oldPrelim.remove();
  const oldMlCard = document.getElementById("cra-ml-card");
  if (oldMlCard) oldMlCard.remove();

  // Reset Troy's card
  const troySpeech = document.getElementById("troy-speech");
  const troyCard = document.getElementById("troy-card") || document.getElementById("booting-card");
  if (troyCard) {
    troyCard.id = "troy-card";
    troyCard.classList.add("speaking");
  }
  if (troySpeech) {
    troySpeech.className = "agent-speech troy-speech formulating";
    troySpeech.innerHTML = `
      <div class="troy-deliberating">
        <span class="troy-pulse-orb"></span>
        <span class="troy-analyzing-text" id="troy-analyzing-msg">Initializing Fine-Tuned DistilBERT Category Classifier...</span>
      </div>
    `;
  }
};

const updateLiveStatus = (message, isRunning = true) => {
  const statusBanner = document.getElementById("cra-live-status");
  if (!statusBanner) return;
  statusBanner.className = `cra-live-status ${isRunning ? "running" : "completed"}`;
  statusBanner.innerHTML = `<span class="status-dot"></span><span>${message}</span>`;
};

// ── Resolution Execution Flow ───────────────────────────────────────────────

const startResolutionFlow = async () => {
  if (isResolving) return;

  const query = topicInput?.value?.trim() || "";
  if (!query) {
    triggerInputError(topicInput, "Please enter the details of the complaint.");
    return;
  }

  const providerSelect = document.getElementById("provider-select");
  const provider = providerSelect?.value || "";
  if (!provider) {
    triggerInputError(providerSelect, "Please select an LLM provider.");
    return;
  }

  const modelInput = document.getElementById("model-input");
  const modelSelect = document.getElementById("model-select");
  const model = (provider === "lmstudio" ? modelInput?.value?.trim() : modelSelect?.value?.trim()) || "";
  if (!model) {
    triggerInputError(provider === "lmstudio" ? modelInput : modelSelect, "Please select or enter a model.");
    return;
  }

  const pConfig = PROVIDERS[provider];
  const requiresKey = pConfig ? pConfig.requiresKey !== false : true;
  const apiKeyInput = document.getElementById("api-key-input");
  const apiKey = apiKeyInput?.value?.trim() || "";
  if (requiresKey && !apiKey) {
    triggerInputError(apiKeyInput, "API key has not been entered.");
    return;
  }

  isResolving = true;
  if (fetchNextBtn) {
    fetchNextBtn.disabled = true;
    fetchNextBtn.classList.add("is-loading");
    fetchNextBtn.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span> Resolving...`;
  }

  showSummarizingFeedCard(query);
  resetTroyConsole();
  startMlAnalyzingCycle();
  setStatusTopic(query.slice(0, 40) + "...");

  try {
    const res = await fetch("/api/next", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ query, provider, model, apiKey }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || "Failed to start complaint resolution.");
    }
  } catch (err) {
    isResolving = false;
    stopMlAnalyzingCycle();
    showTopWarning(err.message || "Failed to initiate resolution.");
    if (fetchNextBtn) {
      fetchNextBtn.disabled = false;
      fetchNextBtn.classList.remove("is-loading");
      fetchNextBtn.textContent = "Begin Resolution";
    }
    updateLiveStatus("Resolution failed to start.", false);
  }
};

// ── SSE Stream Connection ───────────────────────────────────────────────────

let activeEventSource = null;

const connectLiveStream = () => {
  if (activeEventSource) {
    try {
      activeEventSource.close();
    } catch (_) {}
  }

  const token = localStorage.getItem("agenticCouncilIdToken");
  const streamUrl = token ? `/api/stream?token=${encodeURIComponent(token)}` : "/api/stream";
  const stream = new EventSource(streamUrl);
  activeEventSource = stream;

  stream.addEventListener("open", () => {
    console.log("[SSE] Connected to CRA Troy event stream.");
  });

  stream.addEventListener("topic", (e) => {
    try {
      const data = JSON.parse(e.data);
      setStatusTopic(data.title);
    } catch (_) {}
  });

  const handleSummarizing = (e) => {
    try {
      const data = JSON.parse(e.data);
      showSummarizingFeedCard(data.query);
    } catch (_) {}
  };
  stream.addEventListener("ticket_summarizing", handleSummarizing);
  stream.addEventListener("policy_summarizing", handleSummarizing);

  stream.addEventListener("feed", (e) => {
    try {
      const data = JSON.parse(e.data);
      setActiveFeedCard(data);
    } catch (_) {}
  });

  stream.addEventListener("resolution_start", () => {
    if (!isMlCycling && !isTypingTransformed) {
      resetTroyConsole();
      startMlAnalyzingCycle();
    }
  });

  stream.addEventListener("council_start", () => {
    if (!isMlCycling && !isTypingTransformed) {
      resetTroyConsole();
      startMlAnalyzingCycle();
    }
  });

  stream.addEventListener("system", (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.message && !isMlCycling) {
        updateLiveStatus(data.message, true);
      }
    } catch (_) {}
  });

  stream.addEventListener("cra_report", (e) => {
    try {
      const report = JSON.parse(e.data);
      cachedCraReport = report;
      // If cycling has already finished, deliver preliminary assessment immediately
      if (!isMlCycling && !hasDeliveredPreliminary) {
        deliverPreliminaryAssessment(report);
      }
    } catch (_) {}
  });

  stream.addEventListener("agent_start", (e) => {
    try {
      streamedSpeechText = "";
    } catch (_) {}
  });

  stream.addEventListener("agent_delta", (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.delta) {
        // Accumulate tokens silently while LM Studio finishes generating
        streamedSpeechText += data.delta;
      }
    } catch (_) {}
  });

  stream.addEventListener("agent", (e) => {
    try {
      const data = JSON.parse(e.data);
      const fullText = data.message || streamedSpeechText;
      cachedResolutionText = fullText;
      // If 5s ML cycling is already complete, start progressive transformed stream now
      if (!isMlCycling) {
        startTransformedStream(fullText);
      }
    } catch (_) {}
  });

  const onComplete = () => {
    if (isTypingTransformed) {
      pendingResolutionEnd = true;
    } else {
      isResolving = false;
      markFeedCardResolved();
      if (fetchNextBtn) {
        fetchNextBtn.disabled = false;
        fetchNextBtn.classList.remove("is-loading");
        fetchNextBtn.textContent = "Begin Resolution";
      }
    }
  };

  stream.addEventListener("resolution_end", onComplete);
  stream.addEventListener("council_end", onComplete);

  stream.addEventListener("error", () => {
    console.warn("[SSE] Connection interrupted, reconnecting...");
  });
};

// ── Input & Event Listeners ─────────────────────────────────────────────────

const autoResizeTopicInput = () => {
  if (!topicInput) return;
  topicInput.style.height = "auto";
  topicInput.style.height = `${Math.min(topicInput.scrollHeight, 140)}px`;
};

topicInput?.addEventListener("input", () => {
  autoResizeTopicInput();
  if (topicInput.value.trim()) {
    clearInputError(topicInput);
    dismissTopWarning();
  }
});

topicInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    startResolutionFlow();
  }
});

fetchNextBtn?.addEventListener("click", () => {
  startResolutionFlow();
});

// Click Troy's card while streaming to instantly reveal the full formatted text
document.getElementById("troy-card")?.addEventListener("click", () => {
  if (isTypingTransformed) {
    finishTransformedStream();
  }
});

// ── Application Bootstrap ───────────────────────────────────────────────────

let appBootstrapped = false;
const bootstrapApp = () => {
  if (appBootstrapped) return;
  appBootstrapped = true;

  initTheme();
  initProviderConfig();
  initUserProfileMenu();
  autoResizeTopicInput();
  updateFeedEmptyState();

  // Initialize glowing AI avatar canvas
  document.querySelectorAll(".agent-avatar").forEach((avatar) => {
    initTransparentVideoCanvas(avatar);
  });

  // Connect live event stream
  connectLiveStream();
};

document.addEventListener("DOMContentLoaded", bootstrapApp);
if (document.readyState === "complete" || document.readyState === "interactive") {
  bootstrapApp();
}
