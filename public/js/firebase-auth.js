/**
 * public/js/firebase-auth.js
 *
 * Client-side Firebase Auth bridge.
 *
 * Loaded AFTER the Firebase compat SDK bundles (firebase-app-compat.js and
 * firebase-auth-compat.js) and BEFORE /js/login.js and /js/signup.js.
 *
 * Responsibilities:
 *  - Resolve the Firebase client config from GET /api/auth/firebase-config.
 *    A `window.__FIREBASE_CONFIG__` global is still honoured if a deployment sets
 *    one, but no page in this repo inlines it: doing so would require a CSP hash
 *    or 'unsafe-inline', and would hardcode one Firebase project into the HTML
 *    instead of leaving it as a server-side setting.
 *  - Lazily initialize the Firebase app + auth instance.
 *  - Expose window.agenticCouncilGoogleSignIn(), used by the Google buttons on
 *    login.html and signup.html. On success the resolved user is persisted to
 *    localStorage under "agenticCouncilUser", matching the shape written by the
 *    password login flow in /js/login.js.
 *
 * If the Firebase SDK is unavailable (offline / blocked CDN), the script degrades
 * quietly: window.agenticCouncilGoogleSignIn is left undefined and the calling pages
 * report "Google sign-in is not available right now."
 */

(function initFirebaseAuthBridge(global) {
  "use strict";

  const USER_STORAGE_KEY = "agenticCouncilUser";
  const ID_TOKEN_STORAGE_KEY = "agenticCouncilIdToken";
  const CONFIG_ENDPOINT = "/api/auth/firebase-config";

  /** sessionStorage key marking that a sign-in redirect is in flight. */
  const REDIRECT_MARKER_KEY = "agenticCouncilGoogleRedirect";

  let firebaseConfigPromise = null;
  let authPromise = null;

  /** @returns {string|null} The redirect marker, if present */
  function readRedirectMarker() {
    try {
      return global.sessionStorage.getItem(REDIRECT_MARKER_KEY);
    } catch {
      return null;
    }
  }

  function clearRedirectMarker() {
    try {
      global.sessionStorage.removeItem(REDIRECT_MARKER_KEY);
    } catch {
    }
  }

  /**
   * Fetches the client config from the server, falling back to nothing.
   * @returns {Promise<object|null>}
   */
  async function fetchServerConfig() {
    try {
      const response = await fetch(CONFIG_ENDPOINT, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return null;
      const payload = await response.json();
      return payload && payload.apiKey ? payload : null;
    } catch {
      return null;
    }
  }

  /**
   * Resolves the Firebase client config exactly once.
   * @returns {Promise<object|null>}
   */
  function resolveFirebaseConfig() {
    if (firebaseConfigPromise) return firebaseConfigPromise;

    firebaseConfigPromise = (async () => {
      const inline = global.__FIREBASE_CONFIG__;
      if (inline && inline.apiKey && inline.projectId) {
        return inline;
      }
      return fetchServerConfig();
    })();

    return firebaseConfigPromise;
  }

  /**
   * Initializes (once) and returns the Firebase Auth instance, or null when
   * the SDK or configuration is unavailable.
   * @returns {Promise<object|null>}
   */
  function getFirebaseAuthInstance() {
    if (authPromise) return authPromise;

    authPromise = (async () => {
      const firebase = global.firebase;
      if (!firebase || typeof firebase.initializeApp !== "function") {
        return null;
      }
      if (typeof firebase.auth !== "function") {
        return null;
      }

      const config = await resolveFirebaseConfig();
      if (!config || !config.apiKey || !config.projectId) {
        return null;
      }

      try {
        const app =
          typeof firebase.app === "function" && firebase.apps && firebase.apps.length
            ? firebase.app()
            : firebase.initializeApp(config);
        return app.auth();
      } catch {
        return null;
      }
    })();

    return authPromise;
  }

  /**
   * Starts Google sign-in by redirecting the whole page to Google.
   *
   * WHY REDIRECT RATHER THAN POPUP
   * signInWithPopup calls window.open, which requires (a) transient user
   * activation and (b) a Content-Security-Policy that permits popups. Under a
   * strict `default-src 'self'` the popup is refused outright, and mobile
   * browsers block it even without CSP. The failure is indistinguishable from a
   * user closing the window, so it surfaced as "sign in was cancelled" after the
   * account had already been chosen. Redirect needs neither permission and works
   * identically on desktop and mobile.
   *
   * The trade-off is a visible page load to Google and back, which is why the
   * caller shows a "Redirecting to Google..." state.
   *
   * This does not resolve. The browser navigates away, so the code below runs on
   * return via completeRedirectSignIn().
   *
   * @returns {Promise<never>} resolves by navigating away
   */
  async function googleSignIn(onStatusUpdate) {
    const auth = await getFirebaseAuthInstance();
    if (!auth) {
      throw new Error("GOOGLE_SIGN_IN_UNAVAILABLE");
    }

    const provider = new global.firebase.auth.GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });

    // Primary: signInWithPopup provides immediate execution without Chrome cross-site redirect storage deadlock
    try {
      const credential = await auth.signInWithPopup(provider);
      if (credential && (credential.user || credential.credential)) {
        if (typeof onStatusUpdate === "function") {
          onStatusUpdate("Account selected. Signing in...");
        }
        await finalizeSignIn(credential);
        if (typeof onStatusUpdate === "function") {
          onStatusUpdate("Signed in! Redirecting to council...");
        }
        // Brief 400ms delay so user sees the signing in confirmation
        await new Promise((r) => setTimeout(r, 450));
        continueToApp();
        return;
      }
    } catch (popupErr) {
      const code = String(popupErr?.code || "");
      if (code.includes("popup-closed-by-user") || code.includes("cancelled")) {
        throw popupErr;
      }
      console.warn("Popup sign-in not completed, attempting redirect fallback:", popupErr);
    }

    // Fallback: signInWithRedirect
    try {
      global.sessionStorage.setItem(REDIRECT_MARKER_KEY, "1");
    } catch {
    }

    if (typeof onStatusUpdate === "function") {
      onStatusUpdate("Redirecting to Google...");
    }
    await auth.signInWithRedirect(provider);
  }

  /**
   * Completes a redirect sign-in if one is in flight.
   *
   * Called by login.html / signup.html on load. Returns null when the user
   * arrived by ordinary navigation, so callers can tell whether to show a
   * spinner-and-wait or render the form as normal.
   *
   * @returns {Promise<{ id: string, fullName: string, username: string, email: string }|null>}
   */
  async function completeRedirectSignIn() {
    let auth;
    try {
      auth = await getFirebaseAuthInstance();
    } catch {
      return null;
    }
    if (!auth) {
      return null;
    }

    // If Firebase already restored currentUser on reload, finalize immediately
    if (auth.currentUser) {
      return await finalizeSignIn({ user: auth.currentUser });
    }

    const markerPresent = Boolean(readRedirectMarker());
    let hadPendingRedirect = false;
    try {
      if (typeof auth.isSignInWithRedirect === "function") {
        hadPendingRedirect = auth.isSignInWithRedirect();
      }
    } catch {
    }

    if (!markerPresent && !hadPendingRedirect) {
      return null;
    }

    const credential = await pollForCredential(auth);

    if (!credential) {
      clearRedirectMarker();
      return null;
    }

    return await finalizeSignIn(credential);
  }

  /**
   * Reads getRedirectResult() and checks for a usable user or credential token.
   * Uses Promise.race with onAuthStateChanged and a 6s timeout to prevent deadlocks.
   *
   * @param {object} auth
   * @returns {Promise<object|null>} A usable credential, or null
   */
  async function pollForCredential(auth) {
    const benignCodes = [
      "redirect-idp-not-found",
      "redirect-already-used",
      "operation-not-supported-in-this-environment",
      "auth/cancelled-popup-request",
    ];

    const redirectPromise = (async () => {
      try {
        if (typeof auth.getRedirectResult !== "function") return null;
        return await auth.getRedirectResult();
      } catch (err) {
        const code = String(err?.code || "");
        if (benignCodes.some((c) => code.includes(c))) return null;
        throw err;
      }
    })();

    const authStatePromise = new Promise((resolve) => {
      if (typeof auth.onAuthStateChanged !== "function") return;
      const unsubscribe = auth.onAuthStateChanged((user) => {
        try { unsubscribe(); } catch {}
        if (user) resolve({ user });
      });
    });

    const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve(null), 6000));

    let candidate = null;
    try {
      candidate = await Promise.race([redirectPromise, authStatePromise, timeoutPromise]);
    } catch (e) {
      return null;
    }

    const viaUser = Boolean(
      candidate?.user && typeof candidate.user.getIdToken === "function"
    );
    const viaCredential = Boolean(
      candidate?.credential?.accessToken || candidate?.credential?.idToken
    );
    const usable = viaUser || viaCredential;

    return usable ? candidate : null;
  }

  /**
   * Exchanges a Firebase credential for a server-verified session.
   *
   * @param {object} credential - Result of signInWithPopup or getRedirectResult
   * @returns {Promise<object>} The server's user record
   */
  async function finalizeSignIn(credential) {
    if (!credential?.user && !credential?.credential?.accessToken && !credential?.credential?.idToken) {
      throw new Error("GOOGLE_SIGN_IN_UNAVAILABLE");
    }

    let idToken = "";
    if (credential?.user && typeof credential.user.getIdToken === "function") {
      idToken = await credential.user.getIdToken();
    }
    if (!idToken) {
      idToken = credential?.credential?.accessToken || credential?.credential?.idToken || "";
    }
    if (!idToken) throw new Error("Google sign-in did not return an ID token.");

    const photoURL = credential?.user?.photoURL || "";
    const postBody = {
      idToken,
      email: credential?.user?.email || "",
      displayName: credential?.user?.displayName || "",
      uid: credential?.user?.uid || "",
      photoURL,
    };

    const response = await fetch("/api/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(postBody),
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok || !payload?.ok || !payload?.user) {
      throw new Error(String(payload?.error || "Google sign-in failed."));
    }

    if (photoURL && !payload.user.photoURL) {
      payload.user.photoURL = photoURL;
    }

    localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(payload.user));
    if (payload.idToken) {
      localStorage.setItem(ID_TOKEN_STORAGE_KEY, payload.idToken);
    }

    clearRedirectMarker();
    return payload.user;
  }

  /**
   * Navigates to the main app after a completed sign-in.
   *
   * @param {string} [to]
   */
  function continueToApp(to = "index.html") {
    clearRedirectMarker();
    global.location.replace(to);
  }

  global.agenticCouncilGoogleSignIn = googleSignIn;
  global.agenticCouncilCompleteGoogleSignIn = completeRedirectSignIn;
  global.agenticCouncilContinueAfterSignIn = continueToApp;
  global.agenticCouncilGetFirebaseAuth = getFirebaseAuthInstance;

  /**
   * Called by login.html / signup.html on load.
   *
   * On the return leg of a redirect this resolves to the signed-in user and the
   * page navigates onward. On an ordinary page load it resolves to null and the
   * caller renders the form as normal. Never rejects: a failure here is reported
   * to the user in place rather than left as an unhandled rejection, because
   * there is no caller left to catch it.
   *
   * @returns {Promise<{ user: object|null, error: Error|null }>}
   */
  global.agenticCouncilResumeGoogleSignIn = async function resumeGoogleSignIn() {
    try {
      const user = await completeRedirectSignIn();

      clearRedirectMarker();

      if (user) {
        continueToApp();
        return { user, error: null };
      }
      return { user: null, error: null };
    } catch (err) {
      clearRedirectMarker();
      return { user: null, error: err instanceof Error ? err : new Error(String(err)) };
    }
  };
})(window);
