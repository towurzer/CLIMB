import { useState, useEffect, useRef } from "react";

function VideoBrowser({ apiUrl, onSelectShot }) {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [perPage] = useState(24);
  const [total, setTotal] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [shots, setShots] = useState([]);
  const [shotsLoading, setShotsLoading] = useState(false);
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

  const loadMore = async () => {
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
  };

  // Infinite scroll sentinel
  const sentinelRef = useRef(null);
  useEffect(() => {
    if (!sentinelRef.current) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !loadingMore && !loading && (total === null || videos.length < total)) {
            loadMore();
          }
        });
      },
      { root: null, rootMargin: "400px", threshold: 0.1 }
    );

    obs.observe(sentinelRef.current);
    return () => obs.disconnect();
  }, [loadingMore, loading, videos.length, total]);

  // Fetch shots when a video is selected
  const handleVideoClick = (video) => {
    setSelectedVideo(video);
    setShotsLoading(true);

    fetch(`${apiUrl}/climb/videos/${video.video_id}/shots`)
      .then((res) => res.json())
      .then((data) => {
        setShots(data.shots);
        setShotsLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load shots:", err);
        setShotsLoading(false);
      });
  };

  // Go back to video grid
  const handleBack = () => {
    setSelectedVideo(null);
    setShots([]);
  };

  // When user clicks a shot, convert to result format and pass up
  const handleShotClick = (shot) => {
    onSelectShot({
      video_id: selectedVideo.video_id,
      shot_id: shot.shot_id,
      score: 0,
      start_frame: shot.start_frame,
      end_frame: shot.end_frame,
      fps: shot.fps || selectedVideo.fps || 25,
      start_time_ms: Math.round((shot.start_frame / (shot.fps || selectedVideo.fps || 25)) * 1000),
      end_time_ms: Math.round((shot.end_frame / (shot.fps || selectedVideo.fps || 25)) * 1000),
      thumbnail_url: shot.thumbnail_url,
    });
  };

  const filtered = filter ? videos.filter((v) => v.video_id.includes(filter)) : videos;

  if (loading) {
    return <div className="browse-loading">Loading video list...</div>;
  }

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
              {shots.length} shots · {Math.round(selectedVideo.duration_sec)}s · {selectedVideo.fps}fps
            </span>
          </span>
        </div>
        {shotsLoading ? (
          <div className="browse-loading">Loading shots...</div>
        ) : (
          <div className="browse-grid">
            {shots.map((shot) => (
              <div key={shot.shot_id} className="browse-card" onClick={() => handleShotClick(shot)}>
                <div className="browse-card-thumb">
                  <img src={shot.thumbnail_url} alt={`Shot ${shot.shot_id}`} loading="lazy" />
                  <span className="browse-card-badge">#{shot.shot_id}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
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
              <img src={video.thumbnail_url} alt={video.video_id} loading="lazy" />
              <span className="browse-card-badge">{video.num_shots} shots</span>
            </div>
            <div className="browse-card-label">
              <span className="browse-card-id">{video.video_id}</span>
              <span className="browse-card-meta">{Math.round(video.duration_sec)}s</span>
            </div>
          </div>
        ))}
      </div>

      <div ref={sentinelRef} className="browse-sentinel" />
      {loadingMore && <div className="browse-load-more">Loading more videos…</div>}
    </div>
  );
}

export default VideoBrowser;
