import { useRef, useEffect, useState } from "react";

function VideoPlayer({ result, apiUrl }) {
  const videoRef = useRef(null);
  const [loopSegment, setLoopSegment] = useState(true);

  const startSec = result.middle_frame
    ? result.middle_frame / result.fps
    : result.start_time_ms / 1000;
  const endSec = startSec + 5;
  const videoUrl = `${apiUrl}/videos/${result.video_id}.mp4`;
  console.log("middle_frame:", result.middle_frame, "startSec:", startSec, "fps:", result.fps);

  // When video_id changes, reload the video. When only the segment changes, just seek.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    // Force reload when source changes
    video.load();
    video.currentTime = startSec;
    video.play().catch(() => { });
  }, [result.video_id]);

  // When segment changes (same video, different shot), just seek
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = startSec;
  }, [result.shot_id, startSec]);

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
          onError={() => { }}
        >
          <source src={videoUrl} type="video/mp4" />
        </video>
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