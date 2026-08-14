import {useState, useEffect, useRef} from "react";
import {sceneKey} from "../sceneKey";
import {formatTimecode} from "../timecode";

const EMPTY_SET = new Set();

function ShotBrowser({
    videoId,
    currentKeyframeId,
    onSelectShot,
    apiUrl,
    onBrowseAll,
    submittedScenes = EMPTY_SET,
    coveredVideos = EMPTY_SET,
}) {
    const [shots, setShots] = useState([]);
    const [loading, setLoading] = useState(false);
    const activeRef = useRef(null);

    // Fetch shots when video changes
    useEffect(() => {
        if (!videoId) return;
        setLoading(true);

        fetch(`${apiUrl}/climb/videos/${videoId}/scenes`)
            .then((res) => res.json())
            .then((data) => {
                setShots(data.scenes || []);
                setLoading(false);
            })
            .catch((err) => {
                console.error("Failed to load shots:", err);
                setLoading(false);
            });
    }, [videoId, apiUrl]);

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
          {videoId} – {keyframeCount} keyframes in {shots.length} scenes
        </span>
            </div>
            {/* One thumb per keyframe. Scene boundaries stay visible as a gap plus the shot index. */}
            <div className="shot-strip">
                {shots.flatMap((shot) => {
                    // Mark scenes already submitted in the AVS session so nobody resubmits.
                    const submitted = submittedScenes.has(sceneKey(shot.scene_id));
                    const covered = coveredVideos.has(videoId);
                    return (shot.keyframes || []).map((keyframe, i) => {
                        const isActive = keyframe.keyframe_id === currentKeyframeId;
                        return (
                            <div
                                key={keyframe.keyframe_id}
                                ref={isActive ? activeRef : null}
                                className={`shot-thumb ${i === 0 ? "scene-start" : ""} ${isActive ? "active" : ""} ${submitted ? "submitted" : ""} ${covered ? "covered" : ""}`}
                                onClick={() => onSelectShot(shot, keyframe)}
                                title={`Scene ${shot.shot_index}, keyframe ${keyframe.kf_index} — ${formatTimecode(keyframe.keyframe_time_ms)}`}
                            >
                                <img
                                    src={keyframe.thumbnail_url}
                                    alt={`Scene ${shot.scene_id} keyframe ${keyframe.kf_index}`}
                                    loading="lazy"
                                />
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
