import {useEffect, useRef} from "react";
import {sceneKey} from "../sceneKey";
import {SIGNAL_LABELS} from "../sources";

const EMPTY_SET = new Set();

// A gap between two scenes of an `A >> B` chain, not a position in the video, so it reads as a
// duration ("+12s"), not as a timecode.
function formatGap(ms) {
    const seconds = Math.round((ms || 0) / 1000);
    if (seconds < 60) return `+${seconds}s`;
    return `+${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

/**
 * Which encoders found this scene, most responsible first.
 *
 * Ordered by contribution rather than by rank, because the RRF weights differ
 */
function describeSignals(signals, contributions) {
    const shares = contributions || {};
    const merged = new Map();
    for (const [name, rank] of Object.entries(signals || {})) {
        const label = SIGNAL_LABELS[name] || name;
        const share = shares[name] || 0;
        const previous = merged.get(label);
        merged.set(label, previous
            ? {label, rank: Math.min(previous.rank, rank), share: previous.share + share}
            : {label, rank, share});
    }
    return [...merged.values()].sort((a, b) => b.share - a.share || a.rank - b.rank);
}

const isSameScene = (a, b) =>
    Boolean(a && b && a.video_id === b.video_id && a.keyframe_id === b.keyframe_id);

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
    // Keyed by scene_id, so every keyframe of a submitted scene hides with it.
    const visible = hiddenScenes.size
        ? results.filter((r) => !hiddenScenes.has(sceneKey(r.scene_id)))
        : results;

    if (visible.length === 0) {
        return null;
    }

    return (
        <div className="results-grid">
            {visible.map((result, index) => {
                // For a sequence hit the card is the anchor scene; the rest of the chain rides
                // along and any of them can be the one selected, so selection is per thumbnail.
                const partners = result.temporal_partners || [];
                const gaps = result.temporal_gaps_ms || [];
                const anchorSelected = isSameScene(selectedResult, result);
                const isSelected =
                    anchorSelected || partners.some((p) => isSameScene(selectedResult, p));
                const isCovered = coveredVideos.has(result.video_id); // a keyframe from the same scene has already been submitted
                // Not for a chain: the anchor's own signals explain why *it* matched, which says nothing about why the sequence did
                const found = partners.length
                    ? []
                    : describeSignals(result.signals, result.contributions);

                return (
                    <div
                        key={result.keyframe_id ?? `${result.video_id}_${result.scene_id}`}
                        ref={isSelected ? selectedRef : null}
                        className={`result-card ${isSelected ? "selected" : ""} ${isCovered ? "covered" : ""}`}
                        onClick={() => onSelect(result)}
                    >
                        <div className={`thumbnail-wrapper${partners.length && anchorSelected ? " temporal-active" : ""}`}>
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
                        {partners.length > 0 && (
                            <div className="temporal-strip">
                                {partners.map((partner, stage) => (
                                    <button
                                        key={partner.keyframe_id ?? `${partner.video_id}_${partner.scene_id}`}
                                        type="button"
                                        className={`temporal-partner${isSameScene(selectedResult, partner) ? " temporal-active" : ""}`}
                                        title={`Scene ${partner.scene_id}, ${formatGap(gaps[stage])} after the previous match`}
                                        onClick={(e) => {
                                            // Partners are complete results, so selecting one moves
                                            // the player and the submission fields with no special case.
                                            e.stopPropagation();
                                            onSelect(partner);
                                        }}
                                    >
                                        <img
                                            src={partner.thumbnail_url}
                                            alt={`${partner.video_id} scene ${partner.scene_id}`}
                                            loading="lazy"
                                        />
                                        <span className="temporal-gap">{formatGap(gaps[stage])}</span>
                                    </button>
                                ))}
                            </div>
                        )}
                        <div className="result-label">
                            {result.video_id} / scene {result.scene_id}
                        </div>
                        {found.length > 0 && (
                            <div
                                className="result-signals"
                                title={found.map((s) => `${s.label} rank ${s.rank}`).join(" · ")}
                            >
                                {found.map((signal) => (
                                    <span className="signal-badge" key={signal.label}>
                                        {signal.label} ({signal.rank})
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}

export default ResultsGrid;