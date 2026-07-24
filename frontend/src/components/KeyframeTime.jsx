function KeyframeTime({from, to, fromValid, toValid, onChange, disabled}) {
    return (
        <div className="keyframe-time">
            <div className="keyframe-time-header">
                <span className="keyframe-time-label">Submission Time</span>
                <span className="keyframe-time-hint">(h:)mm:ss.cc</span>
            </div>
            <div className="keyframe-time-row">
                <label className="keyframe-time-field">
                    <span>From</span>
                    <input
                        type="text"
                        className={fromValid ? "" : "invalid"}
                        value={from}
                        onChange={(e) => onChange("from", e.target.value)}
                        disabled={disabled}
                        placeholder="00:00.00"
                        title="Start time submitted to DRES"
                    />
                </label>
                <label className="keyframe-time-field">
                    <span>To</span>
                    <input
                        type="text"
                        className={toValid ? "" : "invalid"}
                        value={to}
                        onChange={(e) => onChange("to", e.target.value)}
                        disabled={disabled}
                        placeholder="00:00.00"
                        title="End time submitted to DRES"
                    />
                </label>
            </div>
            {(!fromValid || !toValid) && (
                <div className="keyframe-time-error">Use (h:)mm:ss.cc, e.g. 1:23.45</div>
            )}
        </div>
    );
}

export default KeyframeTime;
