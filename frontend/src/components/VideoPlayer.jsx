import {useRef, useEffect, useState} from "react";
import {formatTimecode, keyframeMs} from "../timecode";

function VideoPlayer({result, previousClip = null, nextClip = null, clipName, onStepClip}) {
    const videoRef = useRef(null);
    const [loopSegment, setLoopSegment] = useState(true);

    // Start on the keyframe when we know it, on the scene otherwise.
    const keyframeAt = keyframeMs(result);
    const startSec = (keyframeAt != null ? keyframeAt : result.start_time_ms) / 1000;
    const endSec = startSec + 5;
    const videoUrl = result.video_url; // Always from the API: mediaPaths.js is the one place that knows the sharded layout.
    // Why the video did not load, from the backend's 404 text; null while it plays.
    const [loadError, setLoadError] = useState(null);
    // Played to the end of the clip (only possible with the loop off). The next clip of the
    // recording is offered, never loaded on its own: switching clips changes what is submitted.
    const [ended, setEnded] = useState(false);

    const explainLoadError = async () => {
        try {
            const res = await fetch(videoUrl, {headers: {Range: "bytes=0-0"}});
            setLoadError(res.ok ? "The browser cannot play this video file." : await res.text());
        } catch {
            setLoadError("The backend is not reachable.");
        }
    };

    // When video_id changes, reload the video. When only the segment changes, just seek.
    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;
        setLoadError(null);
        setEnded(false);

        // Force reload when source changes
        video.load();
        video.currentTime = startSec;
        video.play().catch(() => {
        });
    }, [result.video_id]);

    // When segment changes (same video, different shot), just seek
    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;
        video.currentTime = startSec;
    }, [result.keyframe_id, startSec]);

    // Loop segment: when video time passes end, jump back to start
    useEffect(() => {
        const video = videoRef.current;
        if (!video || !loopSegment) return;

        const handleTimeUpdate = () => {
            if (video.currentTime >= endSec || video.currentTime < startSec - 0.5) {
                video.currentTime = startSec;
            }
        };

        video.addEventListener("timeupdate", handleTimeUpdate);
        return () => video.removeEventListener("timeupdate", handleTimeUpdate);
    }, [loopSegment, startSec, endSec]);

    return (
        <div className="video-player">
            <div className="video-container">
                <video
                    ref={videoRef}
                    controls
                    preload="metadata"
                    onEnded={() => {
                        // A segment within 5 s of the clip's end reaches the end before endSec
                        const video = videoRef.current;
                        if (!loopSegment || !video) return setEnded(true);
                        video.currentTime = startSec;
                        video.play().catch(() => {
                        });
                    }}
                    onPlay={() => setEnded(false)}
                >
                    {/* A failed <source> fires error on itself, not on the <video> */}
                    <source src={videoUrl} type="video/mp4" onError={explainLoadError}/>
                </video>
                {ended && nextClip && (
                    <div className="clip-end-overlay">
                        <button className="clip-step-btn" onClick={() => onStepClip(nextClip, true)}>
                            Next clip {clipName(nextClip)} ▶
                        </button>
                    </div>
                )}
                {loadError && (
                    <div className="video-fallback">
                        <img src={result.keyframe_url} alt="Keyframe"/>
                        <div className="fallback-label">{loadError}</div>
                    </div>
                )}
            </div>

            <div className="player-controls">
                <div className="time-info">
                    Preview: {formatTimecode(startSec * 1000)} – {formatTimecode(endSec * 1000)}
                    {result.fps && <span> ({result.fps} fps)</span>}
                </div>
                <label className="loop-toggle">
                    <input
                        type="checkbox"
                        checked={loopSegment}
                        onChange={(e) => setLoopSegment(e.target.checked)}
                    />
                    Loop segment
                </label>
                {(previousClip || nextClip) && (
                    <div className="clip-step">
                        <button className="clip-step-btn" disabled={!previousClip}
                                onClick={() => onStepClip(previousClip, false)}
                                title={previousClip ? `Select the end of clip ${previousClip.video_id}` : "First clip"}>
                            ◀ {previousClip ? clipName(previousClip) : ""}
                        </button>
                        <button className="clip-step-btn" disabled={!nextClip}
                                onClick={() => onStepClip(nextClip, true)}
                                title={nextClip ? `Select the start of clip ${nextClip.video_id}` : "Last clip"}>
                            {nextClip ? clipName(nextClip) : ""} ▶
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}

export default VideoPlayer;