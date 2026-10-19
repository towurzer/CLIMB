import {useEffect, useRef} from "react";
import {sceneKey} from "../sceneKey";
import {formatTimecode} from "../timecode";
import {clipLabel} from "../useRecording";

const EMPTY_SET = new Set();

/** Every keyframe of the selected video's recording: all clips of an MVK dive, in order. */
function ShotBrowser({
    videoId,
    recording,
    currentKeyframeId,
    onSelectShot,
    onBrowseAll,
    submittedScenes = EMPTY_SET,
    coveredVideos = EMPTY_SET,
}) {
    const activeRef = useRef(null);
    const {recordingId, clips, scenes: shots, loading} = recording;
    const multiClip = clips.length > 1;

    // Scroll active keyframe into view
    useEffect(() => {
        if (activeRef.current) {
            activeRef.current.scrollIntoView({
                behavior: "smooth",
                block: "nearest",
                inline: "center",
            });
        }
    }, [currentKeyframeId, shots]);

    if (loading) {
        return <div className="shot-browser-loading">Loading shots...</div>;
    }

    if (shots.length === 0) return null;

    const keyframeCount = shots.reduce((n, shot) => n + (shot.keyframes?.length || 0), 0);

    return (
        <div className="shot-browser">
            <div className="shot-browser-header">
                {onBrowseAll && (
                    <button
                        className="shot-browser-browse-all-btn"
                        onClick={() => onBrowseAll(videoId)}
                    >
                        Browse in Fullscreen
                    </button>
                )}
                <span className="shot-browser-title">
          {multiClip ? `${recordingId} (${clips.length} clips)` : videoId} – {keyframeCount} keyframes in {shots.length} scenes
        </span>
            </div>
            {/* One thumb per keyframe. Scene boundaries stay visible as a gap plus the shot index,
                clip boundaries additionally as the clip number. */}
            <div className="shot-strip">
                {shots.flatMap((shot, s) => {
                    const clipStart = multiClip && (s === 0 || shots[s - 1].video_id !== shot.video_id);
                    // Mark scenes already submitted in the AVS session so nobody resubmits.
                    const submitted = submittedScenes.has(sceneKey(shot.scene_id));
                    const covered = coveredVideos.has(shot.video_id);
                    const otherClip = multiClip && shot.video_id !== videoId;
                    return (shot.keyframes || []).map((keyframe, i) => {
                        const isActive = keyframe.keyframe_id === currentKeyframeId;
                        return (
                            <div
                                key={keyframe.keyframe_id}
                                ref={isActive ? activeRef : null}
                                className={`shot-thumb ${i === 0 ? "scene-start" : ""} ${clipStart && i === 0 ? "clip-start" : ""} ${otherClip ? "other-clip" : ""} ${isActive ? "active" : ""} ${submitted ? "submitted" : ""} ${covered ? "covered" : ""}`}
                                onClick={() => onSelectShot(shot, keyframe)}
                                title={`${multiClip ? `Clip ${shot.video_id}, ` : ""}Scene ${shot.shot_index}, keyframe ${keyframe.kf_index} — ${formatTimecode(keyframe.keyframe_time_ms)}`}
                            >
                                <img
                                    src={keyframe.thumbnail_url}
                                    alt={`Scene ${shot.scene_id} keyframe ${keyframe.kf_index}`}
                                    loading="lazy"
                                />
                                {clipStart && i === 0 && (
                                    <span className="clip-label">{clipLabel(recordingId, shot.video_id)}</span>
                                )}
                                <span className="shot-label">{shot.shot_index}</span>
                                <span className="shot-time">{formatTimecode(keyframe.keyframe_time_ms)}</span>
                                {submitted && <span className="submitted-badge">✓</span>}
                            </div>
                        );
                    });
                })}
            </div>
        </div>
    );
}

export default ShotBrowser;
