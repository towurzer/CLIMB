import {clipLabel} from "./useRecording";
import {formatTimecode} from "./timecode";

/**
 * Where a hint points, as one line: "00191", "00191 · 00:12.40", or for an MVK dive
 * "Ambon_Apr2012" and "Ambon_Apr2012 · clip 0003 · 00:04.00". Without a keyframe a hint is the
 * whole video, so an MVK one names only the dive.
 */
export function hintTarget(hint) {
    const multiClip = hint.recording_id && hint.recording_id !== hint.video_id;
    if (hint.keyframe_time_ms == null) return multiClip ? hint.recording_id : hint.video_id;
    const time = formatTimecode(hint.keyframe_time_ms);
    return multiClip
        ? `${hint.recording_id} · clip ${clipLabel(hint.recording_id, hint.video_id)} · ${time}`
        : `${hint.video_id} · ${time}`;
}

/** "Anna: have a look at 00191 · 00:12.40" */
export const hintLine = (hint) => `${hint.user || "A teammate"}: have a look at ${hintTarget(hint)}`;
