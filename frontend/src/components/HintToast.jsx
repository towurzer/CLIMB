import {hintLine} from "../hints";

/**
 * A teammate's hint, bottom centre, with a red frame around the whole screen. It stays until it is
 * opened or dismissed: someone deep in their own search has to decide to ignore it, not miss it.
 * Clicking anywhere but the × opens it; it stays in the inbox either way. Several at once are shown
 * one after the other.
 */
function HintToast({hint, pending = 1, onOpen, onDismiss}) {
    if (!hint) return null;

    return (
        <>
            <div className="hint-frame" aria-hidden="true"/>
            <div className="hint-toast" role="alertdialog" tabIndex={0}
                 onClick={() => onOpen(hint)}
                 onKeyDown={(e) => e.key === "Enter" && onOpen(hint)}>
                <div className="hint-toast-text">
                    <div className="hint-toast-title">
                        Hint from your team{pending > 1 ? ` (${pending} waiting)` : ""}
                    </div>
                    <div className="hint-toast-line">{hintLine(hint)}</div>
                    {hint.note && <div className="hint-toast-note">“{hint.note}”</div>}
                    <div className="hint-toast-action">Click to open</div>
                </div>
                <button
                    className="hint-toast-close"
                    title="Ignore this hint (it stays in the inbox)"
                    onClick={(e) => {
                        e.stopPropagation();
                        onDismiss(hint.id);
                    }}
                >
                    ×
                </button>
            </div>
        </>
    );
}

export default HintToast;
