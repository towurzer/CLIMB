import {useEffect, useRef, useState} from "react";
import {teamExpiryLabel} from "../useTeam";

/**
 * Join a team, or create it: an unknown team name is created with the password given. The username
 * is who sent a hint and who submitted what.
 */
function TeamLoginModal({open, onClose, team, listTeams, onConnect, onDisconnect}) {
    const [name, setName] = useState("");
    const [password, setPassword] = useState("");
    const [user, setUser] = useState("");
    const [teams, setTeams] = useState([]);
    const [status, setStatus] = useState("");
    const [loading, setLoading] = useState(false);
    const nameInput = useRef(null);

    useEffect(() => {
        if (!open) return;
        setStatus("");
        nameInput.current?.focus();
        listTeams().then(({ok, teams: found, error}) => {
            setTeams(found);
            if (!ok) setStatus(error);
        });
    }, [open, listTeams]);

    useEffect(() => {
        if (!open) return;
        const onKeyDown = (e) => {
            if (e.key === "Escape") {
                e.stopPropagation();
                onClose();
            }
        };
        window.addEventListener("keydown", onKeyDown, true);
        return () => window.removeEventListener("keydown", onKeyDown, true);
    }, [open, onClose]);

    if (!open) return null;

    const exists = teams.some((t) => t.name.toLowerCase() === name.trim().toLowerCase());

    const submit = async () => {
        if (!name.trim() || !password || !user.trim()) {
            setStatus("Enter a team name, a password and your username.");
            return;
        }
        setLoading(true);
        const result = await onConnect(name.trim(), password, user.trim());
        setLoading(false);
        if (result.ok) {
            setPassword("");
            onClose();
        } else {
            setStatus(result.error);
        }
    };

    const submitOnEnter = (e) => {
        if (e.key === "Enter" && !loading) submit();
    };

    return (
        <div className="modal-backdrop" onMouseDown={onClose}>
            <div className="modal dres-login-modal" onMouseDown={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>Join Team</h2>
                    <button className="modal-close" onClick={onClose} title="Close">×</button>
                </div>

                {team && (
                    <div className="dres-evaluation">
                        <span className="dres-evaluation-name">{team.name}</span>
                        <span className="dres-evaluation-id">as {team.user} · session {team.session?.code}</span>
                    </div>
                )}

                <label className="modal-field">
                    <span>Team</span>
                    <input
                        ref={nameInput}
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="pick one below or type a new name"
                        maxLength={40}
                    />
                </label>
                {teams.length > 0 && (
                    <div className="team-list">
                        {teams.map((t) => (
                            <button
                                key={t.name}
                                className={`team-chip ${t.name.toLowerCase() === name.trim().toLowerCase() ? "active" : ""}`}
                                onClick={() => setName(t.name)}
                                title="Expires after a week without anyone connected"
                            >
                                {t.name} <span>{teamExpiryLabel(t.expiresInMs)}</span>
                            </button>
                        ))}
                    </div>
                )}

                <label className="modal-field">
                    <span>Password</span>
                    <input
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder={exists || !name.trim() ? "team password" : "password for the new team"}
                    />
                </label>

                <label className="modal-field">
                    <span>Username</span>
                    <input
                        type="text"
                        value={user}
                        onChange={(e) => setUser(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="shown on your hints and submissions"
                        maxLength={40}
                    />
                </label>

                {status && <div className="dres-status">{status}</div>}

                <div className="modal-actions">
                    {team && <button className="modal-btn ghost" onClick={() => onDisconnect().then(onClose)}>Leave team</button>}
                    <button className="modal-btn ghost" onClick={onClose}>Cancel</button>
                    <button className="modal-btn primary" onClick={submit} disabled={loading}>
                        {loading ? "Connecting..." : name.trim() && !exists ? "Create team" : "Connect"}
                    </button>
                </div>
            </div>
        </div>
    );
}

export default TeamLoginModal;
