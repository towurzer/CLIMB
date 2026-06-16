import { useRef, useEffect, useState } from "react";

function VideoPlayer({ result, apiUrl }) {
  const videoRef = useRef(null);
  const [loopSegment, setLoopSegment] = useState(true);

  const startSec = result.start_time_ms / 1000;
  const endSec = result.end_time_ms / 1000;

  // When a new result is selected, seek to the start time
  useEffect(() => {
    if (videoRef.current && result) {
      videoRef.current.currentTime = startSec;
    }
  }, [result, startSec]);

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

  // In production: use real video URL from backend
  const videoUrl = `${apiUrl}/videos/${result.video_id}.mp4`;

  return (
    <div className="video-player">
      <div className="video-container">
        <video
          ref={videoRef}
          controls
          preload="metadata"
          onError={() => { }}
        >
          <source src={videoUrl} type="video/mp4" />
        </video>

        {/* Fallback: show thumbnail when video is not available */}
        <div className="video-fallback">
          <img src={result.thumbnail_url} alt="Preview" />
          <div className="fallback-label">
            Video preview (real videos will be loaded later)
          </div>
        </div>
      </div>

      <div className="player-controls">
        <div className="time-info">
          Segment: {startSec.toFixed(1)}s – {endSec.toFixed(1)}s
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
      </div>
    </div>
  );
}

export default VideoPlayer;