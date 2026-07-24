import {useState, useEffect, useCallback} from "react";

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

function VideoBrowser({apiUrl, onSelectShot, openVideoId, onOpenVideoHandled}) {
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
    const [filter, setFilter] = useState("");

    // Fetch first page on mount
    useEffect(() => {
        const load = async () => {
            try {
                const res = await fetch(`${apiUrl}/climb/videos?page=1&per_page=${perPage}`);
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
    }, [apiUrl, perPage]);

    const loadMore = useCallback(async () => {
        if (loadingMore || loading) return;
        if (total !== null && videos.length >= total) return;

        const next = page + 1;
        setLoadingMore(true);
        try {
            const res = await fetch(`${apiUrl}/climb/videos?page=${next}&per_page=${perPage}`);
            const data = await res.json();
            setVideos((prev) => [...prev, ...(data.videos || [])]);
            setPage(next);
            setTotal(data.total ?? total);
        } catch (err) {
            console.error("Failed to load more videos:", err);
        } finally {
            setLoadingMore(false);
        }
    }, [loadingMore, loading, total, videos.length, page, apiUrl, perPage]);

    const videoSentinelRef = useSentinel(
        loadMore,
        !loading && !loadingMore && (total === null || videos.length < total)
    );

    // Fetch shots (page 1) when a video is selected
    const handleVideoClick = (video) => {
        setSelectedVideo(video);
        setShots([]);
        setShotsPage(1);
        setShotsTotal(null);
        setShotsLoading(true);

        fetch(`${apiUrl}/climb/videos/${video.video_id}/shots?page=1&per_page=${SHOTS_PER_PAGE}`)
            .then((res) => res.json())
            .then((data) => {
                setShots(data.shots || []);
                setShotsTotal(data.total ?? null);
                setShotsLoading(false);
            })
            .catch((err) => {
                console.error("Failed to load shots:", err);
                setShotsLoading(false);
            });
    };

    const loadMoreShots = useCallback(() => {
        if (!selectedVideo || shotsLoadingMore || shotsLoading) return;
        if (shotsTotal !== null && shots.length >= shotsTotal) return;

        const next = shotsPage + 1;
        setShotsLoadingMore(true);
        fetch(`${apiUrl}/climb/videos/${selectedVideo.video_id}/shots?page=${next}&per_page=${SHOTS_PER_PAGE}`)
            .then((res) => res.json())
            .then((data) => {
                setShots((prev) => [...prev, ...(data.shots || [])]);
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

    // Jump straight to a video's shot grid (e.g. requested from the sidebar ShotBrowser)
    useEffect(() => {
        if (!openVideoId) return;

        const existing = videos.find((v) => v.video_id === openVideoId);
        if (existing) {
            handleVideoClick(existing);
            onOpenVideoHandled?.();
            return;
        }

        let cancelled = false;
        setSelectedVideo({video_id: openVideoId, fps: 25, duration_sec: 0});
        setShots([]);
        setShotsPage(1);
        setShotsTotal(null);
        setShotsLoading(true);

        fetch(`${apiUrl}/climb/videos/${openVideoId}/shots?page=1&per_page=${SHOTS_PER_PAGE}`)
            .then((res) => res.json())
            .then((data) => {
                if (cancelled) return;
                const fetchedShots = data.shots || [];
                setShots(fetchedShots);
                setShotsTotal(data.total ?? null);
                setSelectedVideo({
                    video_id: openVideoId,
                    fps: fetchedShots[0]?.fps || 25,
                    duration_sec: 0,
                });
                setShotsLoading(false);
                onOpenVideoHandled?.();
            })
            .catch((err) => {
                if (cancelled) return;
                console.error("Failed to open requested video:", err);
                setShotsLoading(false);
                onOpenVideoHandled?.();
            });

        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [openVideoId, apiUrl]);

    // Go back to video grid
    const handleBack = () => {
        setSelectedVideo(null);
        setShots([]);
        setShotsPage(1);
        setShotsTotal(null);
    };

    // When user clicks a shot, convert to result format and pass up.
    // middle_frame has to come along, it is the time we submit to DRES
    const handleShotClick = (shot) => {
        const fps = shot.fps || selectedVideo.fps || 25;
        onSelectShot({
            video_id: selectedVideo.video_id,
            shot_id: shot.shot_id,
            score: 0,
            start_frame: shot.start_frame,
            end_frame: shot.end_frame,
            middle_frame: shot.middle_frame,
            fps: fps,
            start_time_ms: Math.round((shot.start_frame / fps) * 1000),
            end_time_ms: Math.round((shot.end_frame / fps) * 1000),
            thumbnail_url: shot.thumbnail_url,
        });
    };

    const filtered = filter ? videos.filter((v) => v.video_id.includes(filter)) : videos;

    // ── Shot view: showing shots of a selected video ──
    if (selectedVideo) {
        return (
            <div className="browse-view">
                <div className="browse-top-bar">
                    <button className="browse-back" onClick={handleBack}>
                        ← Back to videos
                    </button>
                    <span className="browse-video-title">
            {selectedVideo.video_id}
                        <span className="browse-video-meta">
              {shotsTotal ?? shots.length} shots · {Math.round(selectedVideo.duration_sec)}s · {selectedVideo.fps}fps
            </span>
          </span>
                </div>
                {shotsLoading ? (
                    <div className="browse-loading">Loading shots...</div>
                ) : (
                    <>
                        <div className="browse-grid">
                            {shots.map((shot) => (
                                <div key={shot.shot_id} className="browse-card" onClick={() => handleShotClick(shot)}>
                                    <div className="browse-card-thumb">
                                        <img src={shot.thumbnail_url} alt={`Shot ${shot.shot_id}`} loading="lazy"/>
                                        <span className="browse-card-badge">#{shot.shot_id}</span>
                                    </div>
                                </div>
                            ))}
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
                {filtered.map((video) => (
                    <div key={video.video_id} className="browse-card" onClick={() => handleVideoClick(video)}>
                        <div className="browse-card-thumb">
                            <img src={video.thumbnail_url} alt={video.video_id} loading="lazy"/>
                            <span className="browse-card-badge">{video.num_shots} shots</span>
                        </div>
                        <div className="browse-card-label">
                            <span className="browse-card-id">{video.video_id}</span>
                            <span className="browse-card-meta">{Math.round(video.duration_sec)}s</span>
                        </div>
                    </div>
                ))}
            </div>

            <div ref={videoSentinelRef} className="browse-sentinel"/>
            {loadingMore && <div className="browse-load-more">Loading more videos…</div>}
        </div>
    );
}

export default VideoBrowser;
