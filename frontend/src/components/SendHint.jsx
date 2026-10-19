import {useEffect, useState} from "react";
import {hintTarget} from "../hints";
import {keyframeMs} from "../timecode";

/**
 * Send the selected video to the team as a hint. By default only the video (for MVK: the dive);
 * the keyframe and a note are optional.
 */
function SendHint({result, recordingId, onSend}) {
    const [open, setOpen] = useState(false);
    const [withKeyframe, setWithKeyframe] = useState(false);
    const [note, setNote] = useState("");
    const [status, setStatus] = useState(null); // null | "sending" | "sent" | error text

    // A new selection starts a new hint
    useEffect(() => {
        setOpen(false);
        setWithKeyframe(false);
        setNote("");
        setStatus(null);
    }, [result.video_id, result.keyframe_id]);

    const hint = {
        video_id: result.video_id,
        recording_id: recordingId || result.recording_id || result.video_id,
        ...(withKeyframe ? {
            keyframe_id: result.keyframe_id,
            scene_id: result.scene_id,
            keyframe_time_ms: keyframeMs(result),
        } : {}),
        note: note.trim() || null,
    };

    const send = async () => {
        setStatus("sending");
        const outcome = await onSend(hint);
        if (outcome.ok) {
            setOpen(false);
            setNote("");
            setStatus("sent");
        } else {
            setStatus(outcome.error || "Not sent.");
        }
    };

    if (!open) {
        return (
            <button className="send-hint-btn" onClick={() => {
                setOpen(true);
                setStatus(null);
            }}>
                {status === "sent" ? "Sent to team ✓" : "Send to team"}
            </button>
        );
    }

    return (
        <div className="send-hint">
            <div className="send-hint-target">{hintTarget(hint)}</div>
            <label className="send-hint-option">
                <input type="checkbox" checked={withKeyframe} onChange={(e) => setWithKeyframe(e.target.checked)}/>
                with this keyframe
            </label>
            <input
                className="send-hint-note"
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && status !== "sending" && send()}
                placeholder="note (optional)"
                maxLength={500}
                autoFocus
            />
            <div className="send-hint-actions">
                <button className="modal-btn ghost" onClick={() => setOpen(false)}>Cancel</button>
                <button className="modal-btn primary" onClick={send} disabled={status === "sending"}>
                    {status === "sending" ? "Sending..." : "Send"}
                </button>
            </div>
            {status && status !== "sending" && status !== "sent" && <div className="send-hint-error">{status}</div>}
        </div>
    );
}

export default SendHint;
