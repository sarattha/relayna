import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { fetchStudioSession, logoutStudio, setStudioCsrfToken, StudioApiError } from "./api";
import type { StudioMember } from "./types";
import relaynaMarkUrl from "./assets/relayna-mark.png";

type AuthContextValue = {
  user: StudioMember;
  isAdmin: boolean;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function AuthSurface({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <main className="studio-auth-shell">
      <section className="studio-auth-card">
        <img src={relaynaMarkUrl} alt="" aria-hidden="true" />
        <span className="studio-auth-eyebrow">Relayna Studio</span>
        <h1>{title}</h1>
        <p>{body}</p>
        {action}
      </section>
    </main>
  );
}

export function StudioAuthProvider({ children }: { children: ReactNode }) {
  const callbackError = new URLSearchParams(window.location.search).get("auth_error");
  const [user, setUser] = useState<StudioMember | null>(null);
  const [loading, setLoading] = useState(true);
  const [signedOut, setSignedOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [permissionMessage, setPermissionMessage] = useState<string | null>(null);

  const loadSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await fetchStudioSession();
      setStudioCsrfToken(session.csrf_token);
      setUser(session.user);
      setSignedOut(false);
    } catch (fetchError) {
      setStudioCsrfToken(null);
      setUser(null);
      if (fetchError instanceof StudioApiError && fetchError.status === 401) {
        setSignedOut(true);
      } else {
        setError(fetchError instanceof Error ? fetchError.message : "Unable to load the Studio session.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSession();
    function handleApiError(event: Event) {
      const detail = (event as CustomEvent<{ status?: number; input?: string; message?: string }>).detail;
      const status = detail?.status;
      if (status === 401) {
        setStudioCsrfToken(null);
        setUser(null);
        if (detail?.input === "/studio/auth/session") {
          setSignedOut(true);
        } else {
          const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.assign(`/studio/auth/login?return_to=${returnTo}`);
        }
      }
      if (status === 403) {
        setPermissionMessage(detail?.message || "You do not have permission to perform that action.");
        void loadSession();
      }
    }
    window.addEventListener("relayna:api-error", handleApiError);
    return () => window.removeEventListener("relayna:api-error", handleApiError);
  }, [loadSession]);

  const value = useMemo<AuthContextValue | null>(
    () =>
      user
        ? {
            user,
            isAdmin: user.role === "admin" && user.status === "active",
            async signOut() {
              await logoutStudio();
              setStudioCsrfToken(null);
              setUser(null);
              setSignedOut(true);
            },
          }
        : null,
    [user],
  );

  if (loading) {
    return <AuthSurface title="Signing you in" body="Checking your secure Studio session…" />;
  }
  if ((error || callbackError) && !user) {
    return <AuthSurface title="Authorization error" body={error || callbackError || "Unable to authorize Studio."} action={<button className="studio-auth-button" onClick={() => void loadSession()}>Try again</button>} />;
  }
  if (signedOut || !user) {
    return (
      <AuthSurface
        title="Sign in to Studio"
        body="Use your organization’s Microsoft Entra account to continue."
        action={<a className="studio-auth-button" href={`/studio/auth/login?return_to=${encodeURIComponent(window.location.pathname + window.location.search)}`}>Sign in with Microsoft Entra</a>}
      />
    );
  }
  if (user.status === "pending") {
    return <AuthSurface title="Access pending" body="A Studio administrator must approve your account before you can continue." action={<button className="studio-auth-button" onClick={() => void value?.signOut()}>Sign out</button>} />;
  }
  if (user.status === "blocked") {
    return <AuthSurface title="Access blocked" body="Your Studio account has been blocked. Contact a Studio administrator." action={<button className="studio-auth-button" onClick={() => void value?.signOut()}>Sign out</button>} />;
  }
  return (
    <AuthContext.Provider value={value}>
      {permissionMessage ? (
        <div className="studio-permission-banner" role="alert">
          <span>{permissionMessage}</span>
          <button type="button" onClick={() => setPermissionMessage(null)}>Dismiss</button>
        </div>
      ) : null}
      {children}
    </AuthContext.Provider>
  );
}

export function useStudioAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useStudioAuth must be used within StudioAuthProvider.");
  }
  return value;
}
