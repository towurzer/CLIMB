import {useState, useEffect} from "react";

const POLL_MS = 5000;
const TIMEOUT_MS = 4000;

// "down"     - backend unreachable, nothing works
// "degraded" - backend fine, embedding service missing, so search is dead but browsing is not
// "ok"       - everything answered
const STATE_TITLE = {
    unknown: "Checking backend...",
    down: "Backend unreachable",
    degraded: "Backend connected, embedding service unavailable",
    ok: "Backend and embedding service connected"
};

function BackendStatusDot({apiUrl}) {
    const [state, setState] = useState("unknown");
    const [detail, setDetail] = useState("");

    useEffect(() => {
        let cancelled = false;

        const probe = async () => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
            try {
                const res = await fetch(`${apiUrl}/climb/health`, {signal: controller.signal});
                if (!res.ok) throw new Error(`health returned ${res.status}`);
                const data = await res.json();
                if (cancelled) return;
                setState(data.embedding_service?.ready ? "ok" : "degraded");
                setDetail(data.embedding_service?.detail || "");
            } catch (err) {
                if (!cancelled) {
                    setState("down");
                    setDetail("");
                }
            } finally {
                clearTimeout(timer);
            }
        };

        probe();
        const id = setInterval(probe, POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, [apiUrl]);

    return (
        <span
            className={`backend-dot ${state}`}
            title={detail || STATE_TITLE[state]}
        />
    );
}

export default BackendStatusDot;
