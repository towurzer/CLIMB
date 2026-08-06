import {useState, useEffect} from "react";
import {describeDresResult, describeDresError} from "../dresSubmission";

const SUBMIT_MODES = {
    media: {
        path: "/climb/dres/submit/vqa",
        buttonLabel: "Submit image + text",
        confirmSuffix: "+ shot",
        confirmFirst: true,
    },
    text: {
        path: "/climb/dres/submit/vqa/text",
        buttonLabel: "Submit text only",
        confirmSuffix: "only",
        confirmFirst: false,
    },
};

// [confirm, cancel] in the order that keeps confirm under the submit button that was clicked
const orderConfirmRow = (mode, [confirmButton, cancelButton]) =>
    SUBMIT_MODES[mode].confirmFirst ? [confirmButton, cancelButton] : [cancelButton, confirmButton];

// startTimeMs/endTimeMs come from the keyframe time fields above the submit button,
// so a media submission carries exactly the time the user sees there (null while it does not parse)
function VqaAnswer({apiUrl, selectedResult, startTimeMs, endTimeMs, onSubmitted}) {
    const [answer, setAnswer] = useState("");
    const [confirmMode, setConfirmMode] = useState(null);
    const [vqaStatus, setVqaStatus] = useState(null);
    const [vqaMessage, setVqaMessage] = useState("");

    useEffect(() => {
        setAnswer("");
        setConfirmMode(null);
        setVqaStatus(null);
        setVqaMessage("");
    }, [selectedResult]);

    // Auto-fill shot info when selection changes
    const shotInfo = selectedResult
        ? `${selectedResult.video_id} / scene ${selectedResult.scene_id}`
        : "No shot selected";

    const submitText = answer.trim();
    const canSubmit = Boolean(submitText && selectedResult);
    // text only never carries a time, so only the media submission cares about a broken timecode
    const canSubmitMedia = canSubmit && startTimeMs != null && endTimeMs != null;

    // Submit answer to DRES, either with or without the selected shot
    const handleVqaSubmit = async (mode) => {
        if (!canSubmit) return;
        if (mode === "media" && !canSubmitMedia) return;
        setVqaStatus("submitting");
        setConfirmMode(null);
        setVqaMessage("");

        const body = mode === "text"
            ? {text_answer: submitText}
            : {
                text_answer: submitText,
                video_id: selectedResult?.video_id || null,
                start_time_ms: startTimeMs ?? null,
                end_time_ms: endTimeMs ?? null,
            };

        try {
            const res = await fetch(`${apiUrl}${SUBMIT_MODES[mode].path}`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });
            const data = await res.json();

            const {state, text} = res.ok
                ? describeDresResult(data)
                : describeDresError(data, "DRES VQA Submission failed");

            setVqaStatus(state);
            setVqaMessage(text);
            if (onSubmitted) onSubmitted(submitText, state, mode);
        } catch (err) {
            console.error("VQA submit failed:", err);
            const {state, text} = describeDresError(
                null,
                `DRES VQA Submission failed: ${err.message || "Please check DRES connection."}`
            );
            setVqaStatus(state);
            setVqaMessage(text);
            if (onSubmitted) onSubmitted(submitText, state, mode);
        }
    };

    // A submit button per mode, both need an answer and a selected shot
    const renderSubmitButton = (mode) => (
        <button
            key={mode}
            className={`vqa-submit-btn ${vqaStatus || ""}`}
            onClick={() => {
                if (mode === "media" && !canSubmitMedia) return;
                if (!vqaStatus || vqaStatus === "error") setConfirmMode(mode);
            }}
            disabled={(mode === "media" ? !canSubmitMedia : !canSubmit) || vqaStatus === "submitting" || vqaStatus === "success"}
        >
            {vqaStatus === "submitting"
                ? "Submitting..."
                : vqaStatus === "success"
                    ? "Submitted!"
                    : vqaStatus === "error"
                        ? "Error - try again?"
                        : SUBMIT_MODES[mode].buttonLabel}
        </button>
    );

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

            {/* Answer field - the text submitted to DRES */}
            <div className="vqa-field-label">VQA-Answer:</div>
            <div className="vqa-input-row">
                <input
                    type="text"
                    className="vqa-input"
                    placeholder="Enter the answer to submit with the selected scene..."
                    value={answer}
                    onChange={(e) => {
                        setAnswer(e.target.value);
                        setConfirmMode(null);
                        setVqaStatus(null);
                        setVqaMessage("");
                    }}
                    disabled={vqaStatus === "submitting"}
                />
            </div>

            {/* Submit to DRES */}
            {!confirmMode ? (
                <div className="confirm-row">
                    {renderSubmitButton("media")}
                    {renderSubmitButton("text")}
                </div>
            ) : (
                <div className="confirm-row">
                    {orderConfirmRow(confirmMode, [
                        <button key="confirm" className="vqa-submit-btn confirm"
                                onClick={() => handleVqaSubmit(confirmMode)}>
                            {`Yes, submit "${submitText}" ${SUBMIT_MODES[confirmMode].confirmSuffix}`}
                        </button>,
                        <button key="cancel" className="vqa-submit-btn cancel"
                                onClick={() => setConfirmMode(null)}>
                            Cancel
                        </button>,
                    ])}
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
