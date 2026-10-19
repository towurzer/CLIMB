import {useEffect, useState} from "react";

const expiryLabel = (ms) => {
    const total = Math.max(0, Math.ceil((ms || 0) / 1000));
    if (total >= 3600) return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
    if (total >= 60) return `${Math.floor(total / 60)}m`;
    return `${total}s`;
};

/**
 * The AVS session: which scenes are hidden because someone already submitted them. In a team it is
 * the team's session, and "New session" starts a fresh one for everybody (the next AVS task). The
 * code is shown so people next to each other can see they share it.
 */
function AvsSessionBar({session, teamName, expiredCode, collabOffline, onNewSession, onLeave}) {
    const [confirming, setConfirming] = useState(false);

    useEffect(() => setConfirming(false), [session?.code]);

    const newSession = confirming ? (
        <span className="avs-session-confirm">
            {teamName ? "Fresh session for the whole team?" : "Start a fresh session?"}
            <button className="avs-session-btn" onClick={() => {
                setConfirming(false);
                onNewSession();
            }}>Yes</button>
            <button className="avs-session-btn ghost" onClick={() => setConfirming(false)}>No</button>
        </span>
    ) : (
        <button className="avs-session-btn" onClick={() => (session ? setConfirming(true) : onNewSession())}
                title={teamName ? "Start a fresh session for the whole team, e.g. for the next AVS task" : "Start a fresh session"}>
            New session
        </button>
    );

    return (
        <div className="avs-session-bar">
            {teamName && <span className="avs-session-team">Team <strong>{teamName}</strong></span>}
            {session ? (
                <>
                    <span className="avs-session-code" title="Session code: the same for everyone in it">
                        Session <strong>{session.code}</strong>
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
                    {newSession}
                    {!teamName && (
                        <button className="avs-session-btn ghost" onClick={onLeave}>
                            Leave
                        </button>
                    )}
                </>
            ) : (
                <>
                    {expiredCode && <span className="avs-session-expired">Session {expiredCode} expired</span>}
                    {collabOffline && (
                        <span className="avs-session-offline" title="The shared session service is unreachable. Search and DRES submission still work.">
                            collab offline
                        </span>
                    )}
                    <span className="avs-session-hint">
                        No AVS session. Join a team to share one, or start one for yourself.
                    </span>
                    {newSession}
                </>
            )}
        </div>
    );
}

export default AvsSessionBar;
