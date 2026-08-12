const HOUR_MS = 3600000;
const MINUTE_MS = 60000;

const pad2 = (n) => String(n).padStart(2, "0");

// ms -> "(h:)mm:ss.cc", the hour part is only shown when there actually is one
export function formatTimecode(ms) {
    const total = Math.round(Math.max(0, ms) / 10) * 10;
    const hours = Math.floor(total / HOUR_MS);
    const minutes = Math.floor((total % HOUR_MS) / MINUTE_MS);
    const seconds = Math.floor((total % MINUTE_MS) / 1000);
    const hundredths = (total % 1000) / 10;

    const base = `${pad2(minutes)}:${pad2(seconds)}.${pad2(hundredths)}`;
    return hours > 0 ? `${hours}:${base}` : base;
}

// hour and the fraction are optional, a comma works just as well as a dot
const TIMECODE_PATTERN = /^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$/;

// "(h:)mm:ss.cc" -> ms, null when the user typed something we cannot make sense of
export function parseTimecode(text) {
    const match = TIMECODE_PATTERN.exec((text || "").trim());
    if (!match) return null;

    const [, hourPart, minutePart, secondPart, fractionPart] = match;
    const hours = Number(hourPart || 0);
    const minutes = Number(minutePart);
    const seconds = Number(secondPart);

    if (seconds > 59) return null;
    // without an hour part "90:00" is a legit 90 minutes (1:30), but if we have an hour part its wrong
    if (hourPart !== undefined && minutes > 59) return null;

    // the fraction is a decimal fraction of a second, so ".5" is 500ms and ".05" is 50ms
    const millis = fractionPart ? Number(fractionPart.padEnd(3, "0")) : 0;

    return hours * HOUR_MS + minutes * MINUTE_MS + seconds * 1000 + millis;
}

// Round a time onto a real frame, so we never submit a moment that sits between two frames
export function snapMsToFrame(ms, fps) {
    if (!fps) return Math.round(ms);
    const frame = Math.round((ms / 1000) * fps);
    return Math.round((frame / fps) * 1000);
}

// The keyframe is what we submit, not the scene bounds.
//
// `keyframe_time_ms` is what the API sends (keyframes.ts_ms, measured at extraction time), so it
// is preferred; `frame_number` is the same instant expressed in frames and is the fallback for a
// payload that carries only that.
export function keyframeMs(result) {
    if (!result) return null;
    if (result.keyframe_time_ms != null) return Math.round(result.keyframe_time_ms);
    if (result.frame_number != null && result.fps) {
        return Math.round((result.frame_number / result.fps) * 1000);
    }
    return null;
}
