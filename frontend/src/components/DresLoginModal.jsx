import {useEffect, useRef} from "react";

function DresLoginModal({
    open,
    onClose,
    url,
    name,
    username,
    password,
    onUrlChange,
    onNameChange,
    onUsernameChange,
    onPasswordChange,
    onSubmit,
    loading,
    connected,
    status,
    evaluation,
}) {
    const urlInput = useRef(null);
    const usernameInput = useRef(null);

    // Focus the first field that still needs filling in, so a reconnect goes
    // straight to the password-ish part instead of the URL every time
    useEffect(() => {
        if (!open) return;
        const target = username.trim() ? urlInput.current : usernameInput.current;
        target?.focus();
    }, [open]);
    
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

    const submitOnEnter = (e) => {
        if (e.key === "Enter" && !loading) onSubmit();
    };

    return (
        <div className="modal-backdrop" onMouseDown={onClose}>
            {/* stop clicks inside the dialog from reaching the backdrop's close */}
            <div className="modal dres-login-modal" onMouseDown={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2>Login to DRES</h2>
                    <button className="modal-close" onClick={onClose} title="Close">×</button>
                </div>

                <label className="modal-field">
                    <span>Server URL</span>
                    <input
                        ref={urlInput}
                        type="text"
                        value={url}
                        onChange={(e) => onUrlChange(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="https://vbs.videobrowsing.org"
                    />
                </label>

                <label className="modal-field">
                    <span>Evaluation name</span>
                    <input
                        type="text"
                        value={name}
                        onChange={(e) => onNameChange(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="e.g. IVADL26"
                    />
                </label>

                <label className="modal-field">
                    <span>Username</span>
                    <input
                        ref={usernameInput}
                        type="text"
                        value={username}
                        onChange={(e) => onUsernameChange(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="DRES username"
                    />
                </label>

                <label className="modal-field">
                    <span>Password</span>
                    <input
                        type="password"
                        value={password}
                        onChange={(e) => onPasswordChange(e.target.value)}
                        onKeyDown={submitOnEnter}
                        placeholder="DRES password"
                    />
                </label>

                {/* which evaluation we ended up on - the name alone is ambiguous when
                    DRES has to fall back to a different one than the one requested */}
                {connected && evaluation && (
                    <div className="dres-evaluation">
                        <span className="dres-evaluation-name">{evaluation.name || "unnamed evaluation"}</span>
                        {evaluation.id && <span className="dres-evaluation-id">{evaluation.id}</span>}
                    </div>
                )}

                <div className={`dres-status ${connected ? "connected" : ""}`}>{status}</div>

                <div className="modal-actions">
                    <button className="modal-btn ghost" onClick={onClose}>Cancel</button>
                    <button className="modal-btn primary" onClick={onSubmit} disabled={loading}>
                        {loading ? "Connecting..." : connected ? "Reconnect" : "Login"}
                    </button>
                </div>
            </div>
        </div>
    );
}

export default DresLoginModal;
