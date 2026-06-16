function SubmissionLog({ submissions }) {
    if (submissions.length === 0) return null;

    return (
        <div className="submission-log">
            <div className="log-label">
                Submissions ({submissions.length})
            </div>
            <div className="log-list">
                {submissions.map((sub, i) => (
                    <div
                        key={i}
                        className={`log-entry ${sub.type === "vqa" ? "log-vqa" : ""}`}
                    >
                        <span className="log-index">#{submissions.length - i}</span>
                        {sub.type === "vqa" ? (
                            <span className="log-text">VQA: "{sub.text_answer}"</span>
                        ) : (
                            <span className="log-text">
                                {sub.video_id} / shot {sub.shot_id} ({(sub.start_time_ms / 1000).toFixed(1)}s)
                            </span>
                        )}
                        <span className={`log-status ${sub.status}`}>
                            {sub.status === "success" ? "✓" : sub.status === "error" ? "✗" : "..."}
                        </span>
                        <span className="log-time">{sub.time}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

export default SubmissionLog;