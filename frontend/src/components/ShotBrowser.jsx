import { useState, useEffect, useRef } from "react";

function ShotBrowser({ videoId, currentShotId, onSelectShot, apiUrl }) {
  const [shots, setShots] = useState([]);
  const [loading, setLoading] = useState(false);
  const activeRef = useRef(null);

  // Fetch shots when video changes
  useEffect(() => {
    if (!videoId) return;
    setLoading(true);

    fetch(`${apiUrl}/climb/videos/${videoId}/shots`)
      .then((res) => res.json())
      .then((data) => {
        setShots(data.shots);
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
  }, [currentShotId, shots]);

  if (loading) {
    return <div className="shot-browser-loading">Loading shots...</div>;
  }

  if (shots.length === 0) return null;

  return (
    <div className="shot-browser">
      <div className="shot-browser-header">
        <span className="shot-browser-title">
          {videoId} – {shots.length} shots
        </span>
      </div>
      <div className="shot-strip">
        {shots.map((shot) => {
          const isActive = shot.shot_id === currentShotId;
          return (
            <div
              key={shot.shot_id}
              ref={isActive ? activeRef : null}
              className={`shot-thumb ${isActive ? "active" : ""}`}
              onClick={() => onSelectShot(shot)}
            >
              <img
                src={shot.thumbnail_url}
                alt={`Shot ${shot.shot_id}`}
                loading="lazy"
              />
              <span className="shot-label">{shot.shot_id}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ShotBrowser;
