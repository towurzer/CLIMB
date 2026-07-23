import {useState, useEffect} from "react";

function VqaAnswer({apiUrl, selectedResult, onSubmitted}) {
    const [answer, setAnswer] = useState("");
    const [confirmVqa, setConfirmVqa] = useState(false);
    const [vqaStatus, setVqaStatus] = useState(null);
    const [vqaMessage, setVqaMessage] = useState("");

    useEffect(() => {
        setAnswer("");
        setConfirmVqa(false);
        setVqaStatus(null);
        setVqaMessage("");
    }, [selectedResult]);

    // Auto-fill shot info when selection changes
    const shotInfo = selectedResult
        ? `${selectedResult.video_id} / shot ${selectedResult.shot_id}`
        : "No shot selected";

    const submitText = answer.trim();

    // Submit answer to DRES
    const handleVqaSubmit = async () => {
        if (!submitText) return;
        setVqaStatus("submitting");
        setConfirmVqa(false);
        setVqaMessage("");

        try {
            const res = await fetch(`${apiUrl}/climb/dres/submit/vqa`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    text_answer: submitText,
                    video_id: selectedResult?.video_id || null,
                    start_time_ms: selectedResult?.start_time_ms || null,
                    end_time_ms: selectedResult?.end_time_ms || null,
                }),
            });
            const data = await res.json();

            if (!res.ok) {
                const errorText = data.error || data.message || "DRES VQA Submission failed";
                const detailsText = data.details ? ` ${data.details}` : "";
                throw new Error(`${errorText}${detailsText}`);
            }

            const submissionDetails = data.message || "VQA Answer submitted successfully.";
            setVqaStatus("success");
            setVqaMessage(submissionDetails);
            if (onSubmitted) onSubmitted(submitText, "success");
        } catch (err) {
            console.error("VQA submit failed:", err);
            setVqaStatus("error");
            setVqaMessage(err.message || "DRES VQA Submission failed.");
            if (onSubmitted) onSubmitted(submitText, "error");
        }
    };

    return (
        <div className="vqa-section">
            <div className="vqa-label">VQA</div>

            {/* Shot reference - auto-filled */}
            <div className="vqa-shot-info">
                <span className="vqa-field-label">Keyframe:</span>
                <span className={`vqa-shot-id ${selectedResult ? "" : "empty"}`}>
                    {shotInfo}
                </span>
            </div>

            {/* Answer field - the text submitted to DRES alongside the selected scene */}
            <div className="vqa-field-label">VQA-Answer:</div>
            <div className="vqa-input-row">
                <input
                    type="text"
                    className="vqa-input"
                    placeholder="Enter the answer to submit with the selected scene..."
                    value={answer}
                    onChange={(e) => {
                        setAnswer(e.target.value);
                        setConfirmVqa(false);
                        setVqaStatus(null);
                        setVqaMessage("");
                    }}
                    disabled={vqaStatus === "submitting"}
                />
            </div>

            {/* Submit to DRES */}
            {!confirmVqa ? (
                <button
                    className={`vqa-submit-btn ${vqaStatus || ""}`}
                    onClick={() => {
                        if (submitText && !vqaStatus) setConfirmVqa(true);
                        if (vqaStatus === "error") setConfirmVqa(true);
                    }}
                    disabled={!submitText || vqaStatus === "submitting" || vqaStatus === "success"}
                >
                    {vqaStatus === "submitting"
                        ? "Submitting..."
                        : vqaStatus === "success"
                            ? "Submitted!"
                            : vqaStatus === "error"
                                ? "Error - try again?"
                                : "Submit answer to DRES"}
                </button>
            ) : (
                <div className="confirm-row">
                    <button className="vqa-submit-btn confirm" onClick={handleVqaSubmit}>
                        {`Yes, submit "${submitText}"`}
                    </button>
                    <button className="vqa-submit-btn cancel" onClick={() => setConfirmVqa(false)}>
                        Cancel
                    </button>
                </div>
            )}

            {vqaMessage && (
                <div className={`submit-result-box ${vqaStatus || ""}`}>
                    {vqaMessage}
                </div>
            )}
        </div>
    );
}

export default VqaAnswer;
