const signupForm = document.getElementById("signup-form");
const signupFeedback = document.getElementById("signup-feedback");
const signupButton = signupForm?.querySelector(".signup-btn");
const googleButton = document.getElementById("google-signin-btn");

const setSignupFeedback = (message, type = "error") => {
  if (!signupFeedback) return;
  signupFeedback.textContent = message || "";
  signupFeedback.classList.remove("is-error", "is-success");
  if (message) {
    signupFeedback.classList.add(type === "success" ? "is-success" : "is-error");
  }
};

if (signupForm) {
  signupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setSignupFeedback("");

    const fullName = document.getElementById("full-name")?.value?.trim() || "";
    const username = document.getElementById("username")?.value?.trim() || "";
    const email = document.getElementById("email")?.value?.trim() || "";
    const password = document.getElementById("password")?.value || "";
    const confirmPassword = document.getElementById("confirm-password")?.value || "";
    const termsAccepted = Boolean(document.getElementById("terms")?.checked);

    if (!fullName || !username || !email || !password || !confirmPassword) {
      setSignupFeedback("Please fill in all required fields.");
      return;
    }

    if (!termsAccepted) {
      setSignupFeedback("Please accept the terms to continue.");
      return;
    }

    if (password !== confirmPassword) {
      setSignupFeedback("Passwords do not match.");
      return;
    }

    const setSignupBtnState = (disabled, text) => {
      if (!signupButton) return;
      signupButton.disabled = disabled;
      const labelEl = signupButton.querySelector(".btn-text");
      const arrowEl = signupButton.querySelector(".btn-arrow-icon");
      if (labelEl) {
        labelEl.textContent = text;
        if (arrowEl) arrowEl.style.display = disabled ? "none" : "";
      } else {
        signupButton.textContent = text;
      }
    };

    setSignupBtnState(true, "Creating account...");

    try {
      const response = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fullName, username, email, password, confirmPassword }),
      });
      const payload = await response.json();

      if (!response.ok || !payload?.ok) {
        setSignupFeedback(payload?.error || "Signup failed.");
        return;
      }

      setSignupFeedback("Account created. Redirecting to login...", "success");
      setTimeout(() => {
        window.location.href = "login.html";
      }, 800);
    } catch (error) {
      setSignupFeedback("Unable to reach server. Please try again.");
    } finally {
      setSignupBtnState(false, "Create Account");
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
    setSignupFeedback("");

    if (typeof window.agenticCouncilGoogleSignIn !== "function") {
      setSignupFeedback("Google sign-in is not available right now.");
      return;
    }

    googleButton.disabled = true;
    const originalLabel = googleButton.querySelector(".google-btn-text")?.textContent || "Sign in with Google";
    setGoogleBtnText("Connecting to Google...");

    try {
      await window.agenticCouncilGoogleSignIn((statusText) => {
        setSignupFeedback(statusText || "Signing in...", "success");
        setGoogleBtnText(statusText || "Signing in...");
      });
    } catch (error) {
      const message = String(error?.message || "");
      if (message === "GOOGLE_SIGN_IN_UNAVAILABLE") {
        setSignupFeedback("Google sign-in is unavailable: Firebase is not configured on this server.");
      } else if (message.includes("network-request-failed")) {
        setSignupFeedback("Could not reach Google. Check your connection and try again.");
      } else {
        setSignupFeedback(`Google sign-in failed. ${message || "Please try again."}`);
      }
      googleButton.disabled = false;
      setGoogleBtnText(originalLabel);
    }
  });
}

if (typeof window.agenticCouncilResumeGoogleSignIn === "function") {
  const hasRedirectMarker = Boolean(sessionStorage.getItem("agenticCouncilGoogleRedirect"));
  if (hasRedirectMarker) {
    setSignupFeedback("Completing Google sign-in...");
    if (googleButton) {
      googleButton.disabled = true;
      setGoogleBtnText("Completing sign-in...");
    }
  }

  window.agenticCouncilResumeGoogleSignIn().then(({ user, error }) => {
    if (user) return; // navigation is in flight

    if (error) {
      setSignupFeedback(`Google sign-in failed: ${error.message}`);
    } else {
      setSignupFeedback("");
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
    "Welcome to Agentic complaint resolution system.",
    "Simulate any complaint resolution.",
    "Sign up to Continue.",
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
