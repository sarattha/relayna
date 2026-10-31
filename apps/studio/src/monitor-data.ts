import { useEffect, useRef, useState } from "react";

// Ownership is checked during render as well as completion, so old task data
// cannot flash under a new identity before an effect has had time to run.
export function useMonitorRead<T>(
  requestKey: string,
  enabled: boolean,
  revision: number,
  read: () => Promise<T>,
  providerKey = "",
) {
  const key = providerKey ? JSON.stringify([providerKey, requestKey]) : requestKey;
  const cooldown = useRef({ key: providerKey, until: 0, failures: 0, error: "" });
  const [probe, setProbe] = useState(0);
  if (cooldown.current.key !== providerKey) {
    cooldown.current = { key: providerKey, until: 0, failures: 0, error: "" };
  }
  const [state, setState] = useState<{
    key: string;
    data: T | null;
    error: string | null;
    loading: boolean;
    updatedAt: string | null;
  }>({ key: "", data: null, error: null, loading: false, updatedAt: null });
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    function waitForRecovery() {
      retry = setTimeout(() => { if (alive) setProbe((old) => old + 1); }, Math.max(1, cooldown.current.until - Date.now()));
    }
    if (providerKey && cooldown.current.until > Date.now()) {
      setState((old) => ({ ...old, key, data: old.key === key ? old.data : null, updatedAt: old.key === key ? old.updatedAt : null, error: cooldown.current.error, loading: false }));
      waitForRecovery();
      return () => { alive = false; clearTimeout(retry); };
    }
    setState((old) => ({
      key,
      data: old.key === key ? old.data : null,
      updatedAt: old.key === key ? old.updatedAt : null,
      error: null,
      loading: true,
    }));
    void read()
      .then((data) => {
        if (alive) {
          cooldown.current = { key: providerKey, until: 0, failures: 0, error: "" };
          setState({
            key,
            data,
            error: null,
            loading: false,
            updatedAt: new Date().toISOString(),
          });
        }
      })
      .catch((failure) => {
        if (alive) {
          const error = failure instanceof Error ? failure.message : "Unable to load monitoring data.";
          const status = failure && typeof failure === "object" && "status" in failure ? Number(failure.status) : 0;
          if (providerKey && (!status || status >= 500)) {
            cooldown.current.failures++;
            const delay = Math.min(300_000, 45_000 * 2 ** Math.min(cooldown.current.failures - 1, 3));
            cooldown.current.until = Date.now() + Math.min(300_000, delay * (0.9 + Math.random() * 0.2));
            cooldown.current.error = error;
            waitForRecovery();
          }
          setState((old) => ({
            ...old,
            error,
            loading: false,
          }));
        }
      });
    return () => {
      alive = false;
      clearTimeout(retry);
    };
    // The key describes every request input. read is intentionally not an
    // effect dependency: its inline identity changes on every render.
  }, [key, enabled, revision, providerKey, probe]);
  const result = state.key === key
    ? state
    : { key, data: null, error: null, loading: enabled, updatedAt: null };
  return { ...result, paused: Boolean(providerKey && cooldown.current.until > Date.now()) };
}
