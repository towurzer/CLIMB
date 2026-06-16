import { useState } from "react";

function VqaAnswer({ apiUrl, selectedResult, onSubmitted }) {
    const [answer, setAnswer] = useState("");
    const [confirmVqa, setConfirmVqa] = useState(false);
    const [vqaStatus, setVqaStatus] = useState(null);

    const handleVqaSubmit = async () => {
        if (!answer.trim()) return;
        setVqaStatus("submitting");
        setConfirmVqa(false);

        try {
            const res = await fetch(`${apiUrl}/api/dres/submit-vqa`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text_answer: answer.trim(),
                    // Optionally include video segment if one is selected
                    video_id: selectedResult?.video_id || null,
                    start_time_ms: selectedResult?.start_time_ms || null,
                    end_time_ms: selectedResult?.end_time_ms || null,
                }),
            });
            const data = await res.json();
            setVqaStatus("success");
            console.log("VQA DRES response:", data);
            if (onSubmitted) onSubmitted(answer.trim(), "success");
            setTimeout(() => {
                setVqaStatus(null);
                setAnswer("");
            }, 3000);
        } catch (err) {
            console.error("VQA submit failed:", err);
            setVqaStatus("error");
            if (onSubmitted) onSubmitted(answer.trim(), "error");
            setTimeout(() => setVqaStatus(null), 3000);
        }
    };

    return (
        <div className="vqa-section">
            <div className="vqa-label">VQA Answer</div>
            <div className="vqa-input-row">
                <input
                    type="text"
                    className="vqa-input"
                    placeholder="Type your answer..."
                    value={answer}
                    onChange={(e) => {
                        setAnswer(e.target.value);
                        setConfirmVqa(false);
                        setVqaStatus(null);
                    }}
                    onKeyDown={(e) => {
                        // Enter to trigger confirm, not submit directly
                        if (e.key === "Enter" && answer.trim() && !confirmVqa) {
                            setConfirmVqa(true);
                        }
                    }}
                    disabled={vqaStatus === "submitting"}
                />
            </div>

            {!confirmVqa ? (
                <button
                    className={`vqa-submit-btn ${vqaStatus || ""}`}
                    onClick={() => {
                        if (answer.trim() && !vqaStatus) {
                            setConfirmVqa(true);
                        }
                    }}
                    disabled={!answer.trim() || vqaStatus === "submitting" || vqaStatus === "success"}
                >
                    {vqaStatus === "submitting"
                        ? "Submitting..."
                        : vqaStatus === "success"
                            ? "Submitted!"
                            : vqaStatus === "error"
                                ? "Error - try again?"
                                : "Submit VQA answer"}
                </button>
            ) : (
                <div className="confirm-row">
                    <button
                        className="vqa-submit-btn confirm"
                        onClick={handleVqaSubmit}
                    >
                        Yes, submit "{answer}"
                    </button>
                    <button
                        className="vqa-submit-btn cancel"
                        onClick={() => setConfirmVqa(false)}
                    >
                        Cancel
                    </button>
                </div>
            )}
        </div>
    );
}

export default VqaAnswer;