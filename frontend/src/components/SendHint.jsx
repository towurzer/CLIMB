import {useEffect, useRef, useState} from "react";
import {hintTarget} from "../hints";
import {keyframeMs} from "../timecode";

/**
 * Send the selected keyframe to the team as a hint (untick it to send only the video, for MVK the
 * dive). Built for the keyboard: opening focuses Send, so Enter sends; Tab opens the note, and Tab
 * from the note goes back to Send.
 */
function SendHint({result, recordingId, onSend}) {
    const [open, setOpen] = useState(false);
    const [withKeyframe, setWithKeyframe] = useState(true);
    const [noteOpen, setNoteOpen] = useState(false);
    const [note, setNote] = useState("");
    const [status, setStatus] = useState(null); // null | "sending" | "sent" | error text
    const sendButton = useRef(null);
    const noteInput = useRef(null);

    // A new selection starts a new hint
    useEffect(() => {
        setOpen(false);
        setWithKeyframe(true);
        setNoteOpen(false);
        setNote("");
        setStatus(null);
    }, [result.video_id, result.keyframe_id]);

    useEffect(() => {
        if (open) sendButton.current?.focus();
    }, [open]);

    useEffect(() => {
        if (noteOpen) noteInput.current?.focus();
    }, [noteOpen]);

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
        if (status === "sending") return;
        setStatus("sending");
        const outcome = await onSend(hint);
        if (outcome.ok) {
            setOpen(false);
            setNoteOpen(false);
            setNote("");
            setStatus("sent");
        } else {
            setStatus(outcome.error || "Not sent.");
        }
    };

    const close = () => {
        setOpen(false);
        setNoteOpen(false);
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
        <div className="send-hint" onKeyDown={(e) => e.key === "Escape" && close()}>
            <div className="send-hint-target">{hintTarget(hint)}</div>
            <label className="send-hint-option">
                <input type="checkbox" tabIndex={-1} checked={withKeyframe}
                       onChange={(e) => setWithKeyframe(e.target.checked)}/>
                with this keyframe
            </label>
            {noteOpen ? (
                <input
                    ref={noteInput}
                    className="send-hint-note"
                    type="text"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") send();
                        if (e.key === "Tab" && !e.shiftKey) {
                            e.preventDefault();
                            sendButton.current?.focus();
                        }
                    }}
                    placeholder="note, then Tab and Enter (or just Enter)"
                    maxLength={500}
                />
            ) : (
                <button className="send-hint-add-note" tabIndex={-1} onClick={() => setNoteOpen(true)}>
                    + add a note <span>(Tab)</span>
                </button>
            )}
            <div className="send-hint-actions">
                <button className="modal-btn ghost" tabIndex={-1} onClick={close}>Cancel</button>
                <button
                    ref={sendButton}
                    className="modal-btn primary"
                    onClick={send}
                    onKeyDown={(e) => {
                        if (e.key === "Tab" && !e.shiftKey) {
                            e.preventDefault();
                            if (noteOpen) noteInput.current?.focus();
                            else setNoteOpen(true);
                        }
                    }}
                    disabled={status === "sending"}
                >
                    {status === "sending" ? "Sending..." : "Send ⏎"}
                </button>
            </div>
            {status && status !== "sending" && status !== "sent" && <div className="send-hint-error">{status}</div>}
        </div>
    );
}

export default SendHint;
