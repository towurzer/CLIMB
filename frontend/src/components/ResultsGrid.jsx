import { useEffect, useRef } from "react";

function ResultsGrid({ results, selectedResult, onSelect, onFindSimilar }) {
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

  if (results.length === 0) {
    return null;
  }

  return (
    <div className="results-grid">
      {results.map((result, index) => {
        const isSelected =
          selectedResult &&
          selectedResult.video_id === result.video_id &&
          selectedResult.shot_id === result.shot_id;

        return (
          <div
            key={`${result.video_id}_${result.shot_id}`}
            ref={isSelected ? selectedRef : null}
            className={`result-card ${isSelected ? "selected" : ""}`}
            onClick={() => onSelect(result)}
          >
            <div className="thumbnail-wrapper">
              <img
                src={result.thumbnail_url}
                alt={`${result.video_id} shot ${result.shot_id}`}
                loading="lazy"
              />
              <span className="score-badge">
                {(result.score * 100).toFixed(0)}%
              </span>
              <span className="rank-badge">{index + 1}</span>
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
            </div>
            <div className="result-label">
              {result.video_id} / shot {result.shot_id}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default ResultsGrid;