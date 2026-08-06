import {useEffect, useRef} from "react";

const EMPTY_SET = new Set();

function ResultsGrid({
    results,
    selectedResult,
    onSelect,
    onFindSimilar,
    onExcludeVideo,
    hiddenScenes = EMPTY_SET,
    coveredVideos = EMPTY_SET,
}) {
    const selectedRef = useRef(null);

    // Scroll selected card into view when it changes
    useEffect(() => {
        if (selectedRef.current) {
            selectedRef.current.scrollIntoView({
                behavior: "smooth",
                block: "nearest",
            });
        }
    }, [selectedResult]);

    // Hide scenes already submitted in the AVS session (client-side safety net on
    // top of the server filter; also covers scenes just submitted this session).
    // Scenes are keyed by frame range, so every keyframe of a submitted scene hides.
    const visible = hiddenScenes.size
        ? results.filter((r) => !hiddenScenes.has(`${r.video_id}_${r.start_frame}_${r.end_frame}`))
        : results;

    if (visible.length === 0) {
        return null;
    }

    return (
        <div className="results-grid">
            {visible.map((result, index) => {
                const isSelected =
                    selectedResult &&
                    selectedResult.video_id === result.video_id &&
                    selectedResult.keyframe_id === result.keyframe_id;
                const isCovered = coveredVideos.has(result.video_id); // a keyframe from the same scene has already been submitted

                return (
                    <div
                        key={result.keyframe_id ?? `${result.video_id}_${result.scene_id}`}
                        ref={isSelected ? selectedRef : null}
                        className={`result-card ${isSelected ? "selected" : ""} ${isCovered ? "covered" : ""}`}
                        onClick={() => onSelect(result)}
                    >
                        <div className="thumbnail-wrapper">
                            <img
                                src={result.thumbnail_url}
                                alt={`${result.video_id} scene ${result.scene_id}`}
                                loading="lazy"
                            />
                            <span className="score-badge">
                {(result.score * 100).toFixed(0)}%
              </span>
                            <span className="rank-badge">{index + 1}</span>
                            {isCovered && <span className="covered-badge" title="Video already has a correct hit">covered</span>}
                            {onFindSimilar && (
                                <button
                                    className="similar-icon"
                                    title="Find similar"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onFindSimilar(result);
                                    }}
                                >
                                    ⟲
                                </button>
                            )}
                            {onExcludeVideo && (
                                <button
                                    className="exclude-icon"
                                    title="Exclude video from search"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onExcludeVideo(result);
                                    }}
                                >
                                    ✕
                                </button>
                            )}
                        </div>
                        <div className="result-label">
                            {result.video_id} / scene {result.scene_id}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

export default ResultsGrid;