import { useState, useEffect } from "react";

function VqaAnswer({ apiUrl, selectedResult, onSubmitted }) {
    const [question, setQuestion] = useState("");
    const [answer, setAnswer] = useState("");
    const [askingStatus, setAskingStatus] = useState(null);
    const [confirmVqa, setConfirmVqa] = useState(false);
    const [vqaStatus, setVqaStatus] = useState(null);
    const [vqaMessage, setVqaMessage] = useState("");

    useEffect(() => {
        setQuestion("");
        setAnswer("");
        setAskingStatus(null);
        setConfirmVqa(false);
        setVqaStatus(null);
        setVqaMessage("");
    }, [selectedResult]);

    // Auto-fill shot info when selection changes
    const shotInfo = selectedResult
        ? `${selectedResult.video_id} / shot ${selectedResult.shot_id}`
        : "No shot selected";

    // Ask VQA question to backend
    const handleAsk = async () => {
        if (!question.trim() || !selectedResult) return;
        setAskingStatus("asking");

        try {
            const res = await fetch(
                `${apiUrl}/climb/videos/${selectedResult.video_id}/${selectedResult.shot_id}/ask`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ question: question.trim() }),
                }
            );
            const data = await res.json();
            const raw = data.answer || "";
            const answerOnly = raw.includes("Answer:")
                ? raw.split("Answer:")[1].trim()
                : raw.trim();
            setAnswer(answerOnly);
            setAskingStatus("done");
        } catch (err) {
            console.error("VQA ask failed:", err);
            setAskingStatus("error");
            setTimeout(() => setAskingStatus(null), 3000);
        }
    };

    const submitText = answer.trim() || question.trim();

    // Submit answer to DRES
    const handleVqaSubmit = async () => {
        if (!submitText) return;
        setVqaStatus("submitting");
        setConfirmVqa(false);
        setVqaMessage("");

        try {
            const res = await fetch(`${apiUrl}/climb/dres/submit/vqa`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text_answer: submitText,
                    question: question.trim() || null,
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

            {/* Question input */}
            <div className="vqa-field-label">Question:</div>
            <div className="vqa-input-row">
                <input
                    type="text"
                    className="vqa-input"
                    placeholder="Ask a question about this keyframe..."
                    value={question}
                    onChange={(e) => {
                        setQuestion(e.target.value);
                        setAskingStatus(null);
                    }}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && question.trim() && selectedResult) {
                            handleAsk();
                        }
                    }}
                    disabled={!selectedResult || askingStatus === "asking"}
                />
                <button
                    className="vqa-ask-btn"
                    onClick={handleAsk}
                    disabled={!question.trim() || !selectedResult || askingStatus === "asking"}
                >
                    {askingStatus === "asking" ? "..." : "Ask"}
                </button>
            </div>

            {/* Answer field - editable, auto-filled by VQA response */}
            <div className="vqa-field-label">Answer / text to submit to DRES:</div>
            <div className="vqa-input-row">
                <input
                    type="text"
                    className="vqa-input"
                    placeholder="Enter text to submit with the selected scene..."
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
                                : answer.trim()
                                    ? "Submit answer to DRES"
                                    : "Submit text to DRES"}
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