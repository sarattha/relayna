import { useEffect, useState } from "react";

import { listStudioUsers, updateStudioUser } from "../api";
import { useStudioAuth } from "../auth-context";
import type { StudioMember, StudioMemberStatus, StudioRole } from "../types";
import { NoticeBanner, SectionCard, formatTimestamp, inputStyle, mutedTextStyle, secondaryButtonStyle } from "../ui";

export function AccessPage() {
  const auth = useStudioAuth();
  const [users, setUsers] = useState<StudioMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const payload = await listStudioUsers();
      setUsers(payload.users);
      setError(null);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load Studio users.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function update(user: StudioMember, updatePayload: { role?: StudioRole; status?: StudioMemberStatus }) {
    try {
      const updated = await updateStudioUser(user.user_id, updatePayload);
      setUsers((current) => current.map((item) => (item.user_id === updated.user_id ? updated : item)));
      setError(null);
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update the Studio user.");
    }
  }

  if (!auth.isAdmin) {
    return <NoticeBanner tone="error">Studio administrator access is required.</NoticeBanner>;
  }

  return (
    <div className="studio-stack-lg">
      {error ? <NoticeBanner tone="error">{error}</NoticeBanner> : null}
      <SectionCard title="Studio Access" subtitle="Approve Entra identities and assign the minimum required role.">
        {loading ? <p style={mutedTextStyle}>Loading Studio users…</p> : null}
        <div className="studio-stack-sm">
          {users.map((user) => {
            const self = user.user_id === auth.user.user_id;
            return (
              <article className="studio-subcard studio-access-user" key={user.user_id}>
                <div>
                  <strong>{user.display_name}</strong>
                  <span>{user.email}</span>
                  <small>Last sign-in: {formatTimestamp(user.last_sign_in_at)}</small>
                </div>
                <label>
                  Role
                  <select value={user.role} disabled={self} style={inputStyle} onChange={(event) => void update(user, { role: event.target.value as StudioRole })}>
                    <option value="readonly">Read-only</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <label>
                  Status
                  <select value={user.status} disabled={self} style={inputStyle} onChange={(event) => void update(user, { status: event.target.value as StudioMemberStatus })}>
                    <option value="pending">Pending</option>
                    <option value="active">Active</option>
                    <option value="blocked">Blocked</option>
                  </select>
                </label>
                <button type="button" style={secondaryButtonStyle} onClick={() => void load()}>Refresh</button>
              </article>
            );
          })}
        </div>
      </SectionCard>
    </div>
  );
}
