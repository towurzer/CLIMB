import {useState, useEffect, useCallback, useRef} from "react";
import {sceneKey} from "../sceneKey";
import {formatDuration, formatTimecode} from "../timecode";
import {COLLECTIONS} from "../sources";
import {clipLabel} from "../useRecording";

const SHOTS_PER_PAGE = 60;

// Sentinel div for infinite scroll, backed by a state ref so the effect
// re-attaches whenever the DOM node itself changes (e.g. it gets
// unmounted/remounted when switching between the video grid and shot view),
// not just when unrelated state changes.
function useSentinel(onVisible, enabled) {
    const [node, setNode] = useState(null);
    useEffect(() => {
        if (!node || !enabled) return;
        const obs = new IntersectionObserver(
            (entries) => entries.forEach((entry) => entry.isIntersecting && onVisible()),
            {rootMargin: "400px", threshold: 0.1}
        );
        obs.observe(node);
        return () => obs.disconnect();
    }, [node, enabled, onVisible]);
    return setNode;
}

const EMPTY_SET = new Set();

const containsKeyframe = (scenes, keyframeId) =>
    scenes.some((scene) => (scene.keyframes || []).some((k) => k.keyframe_id === keyframeId));

function VideoBrowser({
    apiUrl,
    onSelectShot,
    openVideoId,
    onOpenVideoHandled,
    onExitBrowse,
    submittedScenes = EMPTY_SET,
    coveredVideos = EMPTY_SET,
    collection = "",
    onChangeCollection = () => {},
    selectedKeyframeId = null, // the keyframe selected in the side panel, outlined in the grid
    focusKeyframeId = null, // the keyframe a deep link opens on
}) {
    const [videos, setVideos] = useState([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [perPage] = useState(24);
    const [total, setTotal] = useState(null);
    const [loadingMore, setLoadingMore] = useState(false);
    const [selectedVideo, setSelectedVideo] = useState(null);
    const [shots, setShots] = useState([]);
    const [shotsLoading, setShotsLoading] = useState(false);
    const [shotsPage, setShotsPage] = useState(1);
    const [shotsTotal, setShotsTotal] = useState(null);
    const [shotsLoadingMore, setShotsLoadingMore] = useState(false);
    // The shot view shows the selected video's whole recording: every clip of an MVK dive.
    const [recording, setRecording] = useState({recordingId: null, clips: []});
    const [scrollTarget, setScrollTarget] = useState(null);
    const grid = useRef(null);
    const [filter, setFilter] = useState("");
    // True when the shot view was opened by a deep link (Browse in Fullscreen) rather than by
    // clicking a card in the grid. The back arrow follows the way in, so it leads back out.
    const [openedByDeepLink, setOpenedByDeepLink] = useState(false);

    // Only this dataset's videos; shares its value with the search bar's dropdown.
    const collectionParam = collection ? `&collection=${collection}` : "";

    // Fetch the first page on mount, and again whenever the dataset changes
    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const res = await fetch(`${apiUrl}/climb/videos?page=1&per_page=${perPage}${collectionParam}`);
                const data = await res.json();
                setVideos(data.videos || []);
                setTotal(data.total ?? null);
                setPage(1);
            } catch (err) {
                console.error("Failed to load videos:", err);
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [apiUrl, perPage, collectionParam]);

    const loadMore = useCallback(async () => {
        if (loadingMore || loading) return;
        if (total !== null && videos.length >= total) return;

        const next = page + 1;
        setLoadingMore(true);
        try {
            const res = await fetch(`${apiUrl}/climb/videos?page=${next}&per_page=${perPage}${collectionParam}`);
            const data = await res.json();
            setVideos((prev) => [...prev, ...(data.videos || [])]);
            setPage(next);
            setTotal(data.total ?? total);
        } catch (err) {
            console.error("Failed to load more videos:", err);
        } finally {
            setLoadingMore(false);
        }
    }, [loadingMore, loading, total, videos.length, page, apiUrl, perPage, collectionParam]);

    const videoSentinelRef = useSentinel(
        loadMore,
        !loading && !loadingMore && (total === null || videos.length < total)
    );

    // The first page of a video's scenes, and further pages until `focus` (a keyframe id) is among
    // them, so the shot view can open on the selected keyframe however deep into the video it is.
    const fetchShotsUntil = async (videoId, focus) => {
        let page = 0;
        let scenes = [];
        let data = {};
        do {
            page += 1;
            const res = await fetch(`${apiUrl}/climb/videos/${encodeURIComponent(videoId)}/scenes?page=${page}&per_page=${SHOTS_PER_PAGE}`);
            data = await res.json();
            scenes = scenes.concat(data.scenes || []);
        } while (focus != null && !containsKeyframe(scenes, focus) && (data.scenes || []).length
                 && scenes.length < (data.total ?? 0));
        return {data, scenes, page};
    };

    // Open a video's shot view. A deep link (Browse in Fullscreen, a hint) may name a keyframe to
    // open on: it is scrolled to the middle once the grid is there. Returns a cancel function.
    const openShots = (video, viaDeepLink, focus = null) => {
        let cancelled = false;
        setSelectedVideo(video);
        setOpenedByDeepLink(viaDeepLink);
        setRecording({recordingId: null, clips: []});
        setShots([]);
        setShotsPage(1);
        setShotsTotal(null);
        setShotsLoading(true);
        setScrollTarget(null);

        fetchShotsUntil(video.video_id, focus)
            .then(({data, scenes, page}) => {
                if (cancelled) return;
                setShots(scenes);
                setShotsPage(page);
                setShotsTotal(data.total ?? null);
                setRecording({recordingId: data.recording_id, clips: data.clips || []});
                setSelectedVideo((prev) => viaDeepLink ? {
                    ...prev,
                    fps: data.fps || scenes[0]?.fps || 25,
                    duration_sec: data.duration_sec || 0,
                    video_url: data.video_url,
                } : {...prev, video_url: data.video_url ?? prev.video_url});
                setScrollTarget(focus);
            })
            .catch((err) => {
                if (!cancelled) console.error("Failed to load shots:", err);
            })
            .finally(() => {
                if (cancelled) return;
                setShotsLoading(false);
                if (viaDeepLink) onOpenVideoHandled?.();
            });
        return () => {
            cancelled = true;
        };
    };

    const handleVideoClick = (video) => openShots(video, false);

    const loadMoreShots = useCallback(() => {
        if (!selectedVideo || shotsLoadingMore || shotsLoading) return;
        if (shotsTotal !== null && shots.length >= shotsTotal) return;

        const next = shotsPage + 1;
        setShotsLoadingMore(true);
        fetch(`${apiUrl}/climb/videos/${selectedVideo.video_id}/scenes?page=${next}&per_page=${SHOTS_PER_PAGE}`)
            .then((res) => res.json())
            .then((data) => {
                setShots((prev) => [...prev, ...(data.scenes || [])]);
                setShotsPage(next);
                setShotsTotal(data.total ?? shotsTotal);
            })
            .catch((err) => {
                console.error("Failed to load more shots:", err);
            })
            .finally(() => {
                setShotsLoadingMore(false);
            });
    }, [selectedVideo, shotsLoadingMore, shotsLoading, shotsTotal, shots.length, shotsPage, apiUrl]);

    const shotsSentinelRef = useSentinel(
        loadMoreShots,
        !shotsLoading && !shotsLoadingMore && (shotsTotal === null || shots.length < shotsTotal)
    );

    // Jump straight to a video's shot grid (Browse in Fullscreen), on the selected keyframe if any
    useEffect(() => {
        if (!openVideoId) return;
        const existing = videos.find((v) => v.video_id === openVideoId);
        return openShots(existing || {video_id: openVideoId, fps: 25, duration_sec: 0}, true, focusKeyframeId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [openVideoId, apiUrl]);

    // Once the grid holds the keyframe to open on, put it in the middle of the view
    useEffect(() => {
        if (scrollTarget == null || shotsLoading) return;
        grid.current?.querySelector(`[data-keyframe-id="${scrollTarget}"]`)?.scrollIntoView({block: "center"});
        setScrollTarget(null);
    }, [scrollTarget, shotsLoading, shots]);

    // Back retraces the way in: out to the results we were sent from if this view was deep-linked,
    // to the video grid if the user got here by clicking a card.
    const exitsToCaller = openedByDeepLink && Boolean(onExitBrowse);
    const handleBack = () => {
        if (exitsToCaller) {
            onExitBrowse();
            return;
        }
        setSelectedVideo(null);
        setOpenedByDeepLink(false);
        setShots([]);
        setShotsPage(1);
        setShotsTotal(null);
    };

    // When user clicks a shot, convert to result format and pass up. The shot names its clip,
    // which is what gets submitted. keyframe_time_ms has to come along, it is the time we submit to DRES
    const handleShotClick = (shot, clicked) => {
        const fps = shot.fps || selectedVideo.fps || 25;
        const keyframes = shot.keyframes || [];
        const keyframe = clicked || keyframes[Math.floor(keyframes.length / 2)] || {};
        onSelectShot({
            scene_id: shot.scene_id,
            keyframe_id: keyframe.keyframe_id,
            video_id: shot.video_id || selectedVideo.video_id,
            shot_index: shot.shot_index,
            kf_index: keyframe.kf_index,
            keyframes,
            score: 0,
            start_frame: shot.start_frame,
            end_frame: shot.end_frame,
            frame_number: keyframe.frame_number,
            fps: fps,
            start_time_ms: shot.start_time_ms,
            end_time_ms: shot.end_time_ms,
            keyframe_time_ms: keyframe.keyframe_time_ms,
            thumbnail_url: keyframe.thumbnail_url || shot.thumbnail_url,
            keyframe_url: keyframe.keyframe_url,
            // The player builds no URLs of its own; the sharded path comes from the API.
            video_url: shot.video_url || selectedVideo.video_url,
        });
    };

    // One card per recording; a filter on a clip id finds its dive
    const filtered = filter
        ? videos.filter((v) => v.video_id.includes(filter) || (v.recording_id || "").includes(filter))
        : videos;
    const multiClip = recording.clips.length > 1;

    // ── Shot view: showing shots of a selected video ──
    if (selectedVideo) {
        return (
            <div className="browse-view">
                <div className="browse-top-bar">
                    <button className="browse-back" onClick={handleBack}>
                        ← Back to {exitsToCaller ? "results" : "videos"}
                    </button>
                    <span className="browse-video-title">
            {multiClip ? recording.recordingId : selectedVideo.video_id}
                        <span className="browse-video-meta">
              {multiClip && `${recording.clips.length} clips · `}
              {shotsTotal ?? shots.length} shots · {formatDuration(multiClip
                  ? recording.clips.reduce((sum, clip) => sum + clip.duration_sec, 0)
                  : selectedVideo.duration_sec)} · {selectedVideo.fps}fps
            </span>
          </span>
                </div>
                {shotsLoading ? (
                    <div className="browse-loading">Loading shots...</div>
                ) : (
                    <>
                        {/* Every keyframe, not one per scene.*/}
                        <div className="browse-grid" ref={grid}>
                            {shots.flatMap((shot, s) => {
                                const submitted = submittedScenes.has(sceneKey(shot.scene_id));
                                const covered = coveredVideos.has(shot.video_id || selectedVideo.video_id);
                                const clipStart = multiClip && (s === 0 || shots[s - 1].video_id !== shot.video_id);
                                const clip = clipStart && recording.clips.find((c) => c.video_id === shot.video_id);
                                const divider = clipStart ? [
                                    <div key={`clip-${shot.video_id}`} className="browse-clip-divider">
                                        Clip {clipLabel(recording.recordingId, shot.video_id)}
                                        {clip && <span> · {formatDuration(clip.duration_sec)} · starts {formatTimecode(clip.recording_offset_ms)} into the dive</span>}
                                    </div>
                                ] : [];
                                return [...divider, ...(shot.keyframes || []).map((keyframe) => (
                                    <div
                                        key={keyframe.keyframe_id}
                                        data-keyframe-id={keyframe.keyframe_id}
                                        className={`browse-card ${keyframe.keyframe_id === selectedKeyframeId ? "selected" : ""} ${submitted ? "submitted" : ""} ${covered ? "covered" : ""}`}
                                        onClick={() => handleShotClick(shot, keyframe)}
                                    >
                                        <div className="browse-card-thumb">
                                            <img src={keyframe.thumbnail_url}
                                                 alt={`Scene ${shot.scene_id} keyframe ${keyframe.kf_index}`}
                                                 loading="lazy"/>
                                            <span className="browse-card-badge">#{shot.shot_index}</span>
                                            {keyframe.keyframe_id === selectedKeyframeId && (
                                                <span className="browse-card-selected">Selected</span>
                                            )}
                                            <span className="browse-card-time">
                                                {formatTimecode(keyframe.keyframe_time_ms)}
                                            </span>
                                            {submitted && <span className="submitted-badge">✓</span>}
                                        </div>
                                    </div>
                                ))];
                            })}
                        </div>
                        <div ref={shotsSentinelRef} className="browse-sentinel"/>
                        {shotsLoadingMore && <div className="browse-load-more">Loading more shots…</div>}
                    </>
                )}
            </div>
        );
    }

    if (loading) {
        return <div className="browse-loading">Loading video list...</div>;
    }

    // ── Video grid: showing all videos ──
    return (
        <div className="browse-view">
            <div className="browse-top-bar">
                <select
                    className={`collection-picker${collection ? " active" : ""}`}
                    value={collection}
                    onChange={(e) => onChangeCollection(e.target.value)}
                    title="Show only this dataset"
                >
                    {COLLECTIONS.map(({key, label}) => (
                        <option key={key || "all"} value={key}>{label}</option>
                    ))}
                </select>
                <input
                    type="text"
                    className="browse-filter"
                    placeholder="Filter by video ID..."
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                />
                <span className="browse-count">{filtered.length} videos</span>
            </div>
            <div className="browse-grid">
                {filtered.map((video) => {
                    const covered = coveredVideos.has(video.video_id);
                    return (
                    <div key={video.video_id} className={`browse-card ${covered ? "covered" : ""}`} onClick={() => handleVideoClick(video)}>
                        <div className="browse-card-thumb">
                            <img src={video.thumbnail_url} alt={video.video_id} loading="lazy"/>
                            <span className="browse-card-badge">
                                {video.num_clips > 1 && `${video.num_clips} clips · `}{video.num_scenes} shots
                            </span>
                            {covered && <span className="covered-badge" title="Already has a correct hit">covered</span>}
                        </div>
                        <div className="browse-card-label">
                            <span className="browse-card-id">{video.recording_id || video.video_id}</span>
                            <span className="browse-card-meta">{formatDuration(video.duration_sec)}</span>
                        </div>
                    </div>
                    );
                })}
            </div>

            <div ref={videoSentinelRef} className="browse-sentinel"/>
            {loadingMore && <div className="browse-load-more">Loading more videos…</div>}
        </div>
    );
}

export default VideoBrowser;
