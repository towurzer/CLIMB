import {useEffect, useRef, useState} from "react";
import {hintLine} from "../hints";

const clock = (ts) => new Date(ts).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});

/** Every hint of the team, newest first; clicking one opens it like the popup does. */
function TeamInbox({hints, onOpen}) {
    const [open, setOpen] = useState(false);
    const box = useRef(null);

    useEffect(() => {
        if (!open) return;
        const close = (e) => {
            if (!box.current?.contains(e.target)) setOpen(false);
        };
        document.addEventListener("mousedown", close);
        return () => document.removeEventListener("mousedown", close);
    }, [open]);

    return (
        <div className="team-inbox" ref={box}>
            <button className="team-inbox-btn" onClick={() => setOpen((v) => !v)} title="Hints from your team">
                {/* inbox tray */}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M22 12h-6l-2 3h-4l-2-3H2"/>
                    <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>
                </svg>
                {hints.length > 0 && <span className="team-inbox-count">{hints.length}</span>}
            </button>
            {open && (
                <div className="team-inbox-panel">
                    {hints.length === 0 ? (
                        <div className="team-inbox-empty">No hints yet.</div>
                    ) : hints.map((hint) => (
                        <button key={hint.id} className="team-inbox-item" onClick={() => {
                            setOpen(false);
                            onOpen(hint);
                        }}>
                            <span className="team-inbox-time">{clock(hint.ts)}</span>
                            <span className="team-inbox-line">{hintLine(hint)}</span>
                            {hint.note && <span className="team-inbox-note">“{hint.note}”</span>}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}

export default TeamInbox;
