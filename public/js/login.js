const loginForm = document.getElementById("login-form");
const loginFeedback = document.getElementById("login-feedback");
const loginButton = loginForm?.querySelector(".login-btn");
const googleButton = document.getElementById("google-signin-btn");

const setFeedback = (message, type = "error") => {
  if (!loginFeedback) return;
  loginFeedback.textContent = message || "";
  loginFeedback.classList.remove("is-error", "is-success");
  if (message) {
    loginFeedback.classList.add(type === "success" ? "is-success" : "is-error");
  }
};

if (loginForm) {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFeedback("");

    const username = document.getElementById("username")?.value?.trim() || "";
    const password = document.getElementById("password")?.value || "";

    if (!username || !password) {
      setFeedback("Please enter your username and password.");
      return;
    }

    const setLoginBtnState = (disabled, text) => {
      if (!loginButton) return;
      loginButton.disabled = disabled;
      const labelEl = loginButton.querySelector(".btn-text");
      const arrowEl = loginButton.querySelector(".btn-arrow-icon");
      if (labelEl) {
        labelEl.textContent = text;
        if (arrowEl) arrowEl.style.display = disabled ? "none" : "";
      } else {
        loginButton.textContent = text;
      }
    };

    setLoginBtnState(true, "Signing in...");

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json();

      if (!response.ok || !payload?.ok) {
        setFeedback(payload?.error || "Login failed.");
        return;
      }

      if (payload?.user) {
        localStorage.setItem("agenticCouncilUser", JSON.stringify(payload.user));
      }

      if (payload?.idToken) {
        localStorage.setItem("agenticCouncilIdToken", payload.idToken);
      }

      setFeedback("Login successful. Redirecting...", "success");
      setTimeout(() => {
        window.location.href = "index.html";
      }, 600);
    } catch (error) {
      setFeedback("Unable to reach server. Please try again.");
    } finally {
      setLoginBtnState(false, "Sign In");
    }
  });
}

const setGoogleBtnText = (text) => {
  if (!googleButton) return;
  const labelEl = googleButton.querySelector(".google-btn-text");
  if (labelEl) {
    labelEl.textContent = text;
  } else {
    googleButton.textContent = text;
  }
};

if (googleButton) {
  googleButton.addEventListener("click", async () => {
    setFeedback("");

    if (typeof window.agenticCouncilGoogleSignIn !== "function") {
      setFeedback("Google sign-in is not available right now.");
      return;
    }

    googleButton.disabled = true;
    const originalLabel = googleButton.querySelector(".google-btn-text")?.textContent || "Sign in with Google";
    setGoogleBtnText("Connecting to Google...");

    try {
      await window.agenticCouncilGoogleSignIn((statusText) => {
        setFeedback(statusText || "Signing in...", "success");
        setGoogleBtnText(statusText || "Signing in...");
      });
    } catch (error) {
      const message = String(error?.message || "");
      if (message === "GOOGLE_SIGN_IN_UNAVAILABLE") {
        setFeedback("Google sign-in is unavailable: Firebase is not configured on this server.");
      } else if (message.includes("popup-closed-by-user") || message.includes("cancelled")) {
        setFeedback("Google sign-in was cancelled.");
      } else if (message.includes("network-request-failed")) {
        setFeedback("Could not reach Google. Check your connection and try again.");
      } else {
        setFeedback(`Google sign-in failed. ${message || "Please try again."}`);
      }
      googleButton.disabled = false;
      setGoogleBtnText(originalLabel);
    }
  });
}

if (typeof window.agenticCouncilResumeGoogleSignIn === "function") {
  const hasRedirectMarker = Boolean(sessionStorage.getItem("agenticCouncilGoogleRedirect"));
  if (hasRedirectMarker) {
    setFeedback("Completing Google sign-in...");
    if (googleButton) {
      googleButton.disabled = true;
      setGoogleBtnText("Completing sign-in...");
    }
  }

  window.agenticCouncilResumeGoogleSignIn().then(({ user, error }) => {
    if (user) return; // navigation is in flight

    if (error) {
      setFeedback(`Google sign-in failed: ${error.message}`, "error");
    } else {
      setFeedback("");
    }
    if (googleButton) {
      googleButton.disabled = false;
      setGoogleBtnText("Sign in with Google");
    }
  });
}

const initThemeToggle = () => {
  const toggleBtn = document.getElementById("theme-toggle-btn");
  if (!toggleBtn) return;
  toggleBtn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("policy_sim_theme", next);
    } catch (e) {}
  });
};

initThemeToggle();

const initTypographicRotator = () => {
  const textEl = document.getElementById("brand-title-text");
  if (!textEl) return;

  const phrases = [
    "Welcome back to Agentic complaint resolution system.",
    "Pick up right where you left off.",
  ];

  let phraseIndex = 0;
  let charIndex = phrases[0].length;
  let isDeleting = false;
  let isRunning = true;

  let timer = setTimeout(() => {
    isDeleting = true;
    tick();
  }, 3200);

  function tick() {
    if (!isRunning) return;
    const current = phrases[phraseIndex];

    if (isDeleting) {
      charIndex--;
      textEl.textContent = current.substring(0, charIndex);

      if (charIndex === 0) {
        isDeleting = false;
        phraseIndex = (phraseIndex + 1) % phrases.length;
        timer = setTimeout(tick, 320);
        return;
      }
      timer = setTimeout(tick, 26);
    } else {
      charIndex++;
      textEl.textContent = current.substring(0, charIndex);

      if (charIndex === current.length) {
        isDeleting = true;
        timer = setTimeout(tick, 2800);
        return;
      }
      const speed = 45 + Math.random() * 30;
      timer = setTimeout(tick, speed);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(timer);
    } else {
      clearTimeout(timer);
      timer = setTimeout(tick, 500);
    }
  });
};

initTypographicRotator();

const brandLogoVideo = document.querySelector("video.brand-logo");
if (brandLogoVideo) {
  brandLogoVideo.muted = true;
  brandLogoVideo.play().catch(() => {});
}
