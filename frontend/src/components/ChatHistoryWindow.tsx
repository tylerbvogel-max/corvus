import { useCallback, useEffect, useState } from 'react';
import { listSessions, deleteSession, updateSessionTitle, type SessionSummary } from '../api/operator';
import { SessionTitle, relativeTime } from './HomePage';
import {
  CHAT_SESSIONS_CHANGED_EVENT, requestLoadSession, requestNewChat,
  announceSessionsChanged,
} from '../chatBus';

/* Chat History window — the session list that used to be the chat's
   left sidebar. Owns its own fetch; stays in sync with the chat via
   chatBus events. Clicking a session loads it into the Home window. */

export default function ChatHistoryWindow() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    listSessions().then(setSessions).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener(CHAT_SESSIONS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(CHAT_SESSIONS_CHANGED_EVENT, refresh);
  }, [refresh]);

  const filtered = sessions.filter(s => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (s.title || '').toLowerCase().includes(q);
  });

  return (
    <div className="chat-history-window">
      <div className="chat-history-header">
        <button className="chat-new-btn" onClick={requestNewChat}>+ New Chat</button>
      </div>
      <div className="chat-sidebar-search">
        <input
          type="text"
          className="chat-sidebar-search-input"
          placeholder="Search conversations…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          aria-label="Search conversations"
        />
      </div>
      <div className="chat-history-list">
        {loading && <div className="chat-history-empty">Loading…</div>}
        {!loading && filtered.length === 0 && (
          <div className="chat-history-empty">
            {query ? 'No conversations match.' : 'No conversations yet.'}
          </div>
        )}
        {filtered.map(s => (
          <div
            key={s.id}
            className="chat-session-item"
            onClick={() => requestLoadSession(s.id)}
          >
            <SessionTitle
              title={s.title || 'Untitled'}
              onRename={async (newTitle) => {
                await updateSessionTitle(s.id, newTitle);
                setSessions(prev => prev.map(ss => ss.id === s.id ? { ...ss, title: newTitle } : ss));
                announceSessionsChanged();
              }}
            />
            <span className="chat-session-time">{relativeTime(s.updated_at)}</span>
            <button
              className="chat-session-del"
              onClick={async e => {
                e.stopPropagation();
                await deleteSession(s.id).catch(() => {});
                setSessions(prev => prev.filter(ss => ss.id !== s.id));
                announceSessionsChanged();
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
