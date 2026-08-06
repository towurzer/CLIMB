import {formatTimecode} from "../timecode";

function SubmissionLog({submissions}) {
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
                            <span className="log-text">
                                VQA ({sub.mode === "text" ? "text only" : "text + shot"}): "{sub.text_answer}"
                            </span>
                        ) : (
                            <span className="log-text">
                                {sub.video_id} / scene {sub.scene_id} ({formatTimecode(sub.start_time_ms)})
                            </span>
                        )}
                        <span className={`log-status ${sub.status}`}>
                            {sub.status === "success" ? "✓"
                                : sub.status === "error" ? "✗"
                                    : sub.status === "pending" ? "?"
                                        : "..."}
                        </span>
                        <span className="log-time">{sub.time}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

export default SubmissionLog;