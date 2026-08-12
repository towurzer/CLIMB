import {useState, useEffect} from "react";

const POLL_MS = 5000;
const TIMEOUT_MS = 4000;

// Severity tiers, worst first. The colour is chosen to match what the operator has actually lost:
//
// "down"  - red, steady. Backend unreachable, nothing works.
// "degraded"  - yellow. Embedding service missing, so search is dead but browsing is not.
// "collab-offline" - green with a yellow blink. The shared AVS session service is unreachable, so
//                    colleagues' submissions stop syncing, but search, browse and DRES submission
//                    are all completely unaffected.
// "ok"  - green, steady. Everything answered.
const STATE_TITLE = {
    unknown: "Checking backend...",
    down: "Backend unreachable",
    degraded: "Backend connected, embedding service unavailable",
    "collab-offline": "Everything works except AVS collaboration: the shared session service is unreachable",
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
                if (!data.embedding_service?.ready) {
                    setState("degraded");
                    setDetail(data.embedding_service?.detail || "");
                    return;
                }
                const collab = data.avs_collab;
                if (collab?.configured && !collab.reachable) {
                    setState("collab-offline");
                    setDetail(collab.detail || "");
                    return;
                }
                setState("ok");
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
