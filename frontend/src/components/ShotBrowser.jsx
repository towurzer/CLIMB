import {useState, useEffect, useRef} from "react";

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

    // Scroll active shot into view
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
          {videoId} – {shots.length} scenes
        </span>
            </div>
            <div className="shot-strip">
                {shots.map((shot) => {
                    const isActive = shot.keyframes?.some((k) => k.keyframe_id === currentKeyframeId);
                    // Mark scenes already submitted in the AVS session so nobody resubmits.
                    const submitted = submittedScenes.has(String(shot.scene_id));
                    const covered = coveredVideos.has(videoId);
                    return (
                        <div
                            key={shot.scene_id}
                            ref={isActive ? activeRef : null}
                            className={`shot-thumb ${isActive ? "active" : ""} ${submitted ? "submitted" : ""} ${covered ? "covered" : ""}`}
                            onClick={() => onSelectShot(shot)}
                        >
                            <img
                                src={shot.thumbnail_url}
                                alt={`Scene ${shot.scene_id}`}
                                loading="lazy"
                            />
                            <span className="shot-label">{shot.shot_index}</span>
                            {submitted && <span className="submitted-badge">✓</span>}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export default ShotBrowser;
