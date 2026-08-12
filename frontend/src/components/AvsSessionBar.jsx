import {useState, useCallback} from "react";

const expiryLabel = (ms) => {
    const total = Math.max(0, Math.ceil((ms || 0) / 1000));
    if (total >= 3600) return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
    if (total >= 60) return `${Math.floor(total / 60)}m`;
    return `${total}s`;
};

function AvsSessionBar({
    apiUrl,
    session,
    expiredCode,
    collabOffline,
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
        const result = await onJoin(clean);
        if (result === "notfound") {
            setJoinError(`Session ${clean} not found or expired.`);
        } else if (result === "offline") {
            setJoinError("Can't reach the session service — collaboration is offline.");
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
            if (res.status === 503) {
                setJoinError("Can't reach the session service — can't list sessions.");
                setSessions([]);
                return;
            }
            setJoinError("");
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
                    {collabOffline ? (
                        <span
                            className="avs-session-offline"
                            title={"Can't reach the shared session service. You keep the scenes already known, " +
                                "but teammates' new submissions won't show up until it's back. Search and DRES submission are unaffected."}
                        >
                            collab offline
                        </span>
                    ) : (
                        <span className="avs-session-expiry" title="Deleted after 2 h with no activity">
                            expires in {expiryLabel(session.expiresInMs)}
                        </span>
                    )}
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
                    {collabOffline && (
                        <span className="avs-session-offline" title="The shared session service is unreachable. Search and DRES submission still work.">
                            collab offline
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
