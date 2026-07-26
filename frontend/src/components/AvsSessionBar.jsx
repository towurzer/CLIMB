import {useState, useCallback} from "react";

// ms -> whole seconds, for the idle-expiry countdown
const secs = (ms) => Math.max(0, Math.ceil((ms || 0) / 1000));

function AvsSessionBar({
    apiUrl,
    session,
    expiredCode,
    onCreate,
    onJoin,
    onLeave,
}) {
    const [joinCode, setJoinCode] = useState("");
    const [joinError, setJoinError] = useState("");
    const [sessions, setSessions] = useState(null); // null = list not open

    const submitJoin = useCallback(async () => {
        const clean = joinCode.trim().toUpperCase();
        if (!clean) return;
        const ok = await onJoin(clean);
        if (!ok) {
            setJoinError(`Session ${clean} not found or expired.`);
        } else {
            setJoinError("");
            setJoinCode("");
            setSessions(null);
        }
    }, [joinCode, onJoin]);

    const toggleList = useCallback(async () => {
        if (sessions !== null) {
            setSessions(null);
            return;
        }
        try {
            const res = await fetch(`${apiUrl}/climb/avs/sessions`);
            const data = await res.json();
            setSessions(data.sessions || []);
        } catch (err) {
            console.error("Failed to list AVS sessions:", err);
            setSessions([]);
        }
    }, [apiUrl, sessions]);

    return (
        <div className="avs-session-bar">
            {session ? (
                <>
                    <span className="avs-session-code" title="Active AVS session code">
                        Session <strong>{session.code}</strong>
                        {session.name ? ` · ${session.name}` : ""}
                    </span>
                    <span className="avs-session-stats">
                        {session.counts?.instances ?? 0} instances · {session.counts?.distinctVideos ?? 0} videos
                    </span>
                    <span className="avs-session-expiry" title="Deleted after 5 min with no activity">
                        expires in {secs(session.expiresInMs)}s
                    </span>
                    <button className="avs-session-btn" onClick={onCreate} title="Start a fresh session">
                        New session
                    </button>
                    <button className="avs-session-btn ghost" onClick={onLeave}>
                        Leave
                    </button>
                </>
            ) : (
                <>
                    {expiredCode && (
                        <span className="avs-session-expired">
                            Session {expiredCode} expired
                        </span>
                    )}
                    <span className="avs-session-hint">No AVS session — create one or join to collaborate.</span>
                    <button className="avs-session-btn" onClick={onCreate}>
                        New session
                    </button>
                    <input
                        className="avs-session-join-input"
                        type="text"
                        maxLength={4}
                        placeholder="CODE"
                        value={joinCode}
                        onChange={(e) => {
                            setJoinCode(e.target.value.toUpperCase());
                            setJoinError("");
                        }}
                        onKeyDown={(e) => e.key === "Enter" && submitJoin()}
                    />
                    <button className="avs-session-btn" onClick={submitJoin}>
                        Join
                    </button>
                    <button className="avs-session-btn ghost" onClick={toggleList}>
                        {sessions !== null ? "Hide" : "Active"}
                    </button>
                    {joinError && <span className="avs-session-error">{joinError}</span>}
                    {sessions !== null && (
                        <span className="avs-session-list">
                            {sessions.length === 0
                                ? "no active sessions"
                                : sessions.map((s) => (
                                    <button
                                        key={s.code}
                                        className="avs-session-chip"
                                        onClick={() => onJoin(s.code)}
                                        title={`${s.instances} instances · expires in ${secs(s.expiresInMs)}s`}
                                    >
                                        {s.code}{s.name ? ` (${s.name})` : ""}
                                    </button>
                                ))}
                        </span>
                    )}
                </>
            )}
        </div>
    );
}

export default AvsSessionBar;
