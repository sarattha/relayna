import { useEffect, useState } from "react";

// Ownership is checked during render as well as completion, so old task data
// cannot flash under a new identity before an effect has had time to run.
export function useMonitorRead<T>(
  key: string,
  enabled: boolean,
  revision: number,
  read: () => Promise<T>,
) {
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
    setState((old) => ({
      key,
      data: old.key === key ? old.data : null,
      updatedAt: old.key === key ? old.updatedAt : null,
      error: null,
      loading: true,
    }));
    void read()
      .then((data) => {
        if (alive)
          setState({
            key,
            data,
            error: null,
            loading: false,
            updatedAt: new Date().toISOString(),
          });
      })
      .catch((failure) => {
        if (alive)
          setState((old) => ({
            ...old,
            error:
              failure instanceof Error
                ? failure.message
                : "Unable to load monitoring data.",
            loading: false,
          }));
      });
    return () => {
      alive = false;
    };
    // The key describes every request input. read is intentionally not an
    // effect dependency: its inline identity changes on every render.
  }, [key, enabled, revision]);
  return state.key === key
    ? state
    : { key, data: null, error: null, loading: enabled, updatedAt: null };
}
