const joinParts = (parts) => parts.filter(Boolean).join(" · ");

// Turns a successful backend response into a box state and a one line summary
export function describeDresResult(data) {
    const verdict = data.verdict || data.dres_response?.submission || null;
    const code = data.dres_status;
    const summary = data.status_summary || "";
    const detail = data.dres_response?.description || data.message || "";

    let state;
    if (verdict === "CORRECT") {
        state = "success";
    } else if (verdict === "WRONG" || verdict === "UNDECIDABLE") {
        state = "error";
    } else if (verdict === "INDETERMINATE" || code === 202) {
        state = "pending";
    } else {
        // graded without a verdict field - trust the status code
        state = code === 200 ? "success" : "error";
    }

    const header = code ? `${code} · ${summary}` : summary;
    return {state, text: joinParts([header, verdict, detail])};
}

export function describeDresError(data, fallbackMessage) {
    const code = data?.dres_status;
    const summary = data?.status_summary || "";
    const header = code ? `${code} · ${summary}` : summary;
    const detail = data?.details || data?.error || fallbackMessage;

    return {state: "error", text: joinParts([header, detail])};
}
