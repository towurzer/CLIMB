const IDLE_TIMEOUT_MS = parseInt(process.env.AVS_IDLE_TIMEOUT_MS || `${2 * 60 * 60 * 1000}`, 10);
const SWEEP_INTERVAL_MS = parseInt(process.env.AVS_SWEEP_INTERVAL_MS || `${60 * 1000}`, 10);

const CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ";
const CODE_LENGTH = 4;

/**
 * code -> { code, name, createdAt, lastActivity, scenes: Map<scene_id, entry> }
 */
const sessions = new Map();

const sceneKey = (sceneId) => String(sceneId);

const holdsVideo = (status) => status === "CORRECT" || status === "INDETERMINATE";

// INDETERMINATE means "DRES has not judged this yet". Everything else is a final answer.
const isFinal = (status) => Boolean(status) && status !== "INDETERMINATE";

function randomCode() {
    let code;
    do {
        code = Array.from({length: CODE_LENGTH}, () =>
            CONSONANTS[Math.floor(Math.random() * CONSONANTS.length)]
        ).join("");
    } while (sessions.has(code)); // regenerate on collision
    return code;
}

// refreshes the idle timer, so a session only dies once everyone has left it.
function getSession(code) {
    if (!code) return null;
    const session = sessions.get(code);
    if (!session) return null;
    session.lastActivity = Date.now();
    return session;
}

function peekSession(code) {
    if (!code) return null;
    return sessions.get(code) || null;
}

function createSession(name) {
    const now = Date.now();
    const session = {
        code: randomCode(),
        name: name || null,
        createdAt: now,
        lastActivity: now,
        scenes: new Map() // scene_id -> { scene_id, video_id, start_frame, end_frame, status, user, ts }
    };
    sessions.set(session.code, session);
    return session;
}

function recordScene(code, {scene_id, video_id, start_frame, end_frame, status, user}) {
    if (scene_id === undefined || scene_id === null) return false;
    const session = getSession(code);
    if (!session) return false;

    const key = sceneKey(scene_id);
    const existing = session.scenes.get(key);
    const incoming = status || "INDETERMINATE";


    if (existing && isFinal(existing.status) && !isFinal(incoming)) {
        return true;
    }

    // Merge rather than replace.
    const pick = (next, prev) => (next === undefined || next === null ? prev : next);

    session.scenes.set(key, {
        scene_id,
        video_id: pick(video_id, existing?.video_id),
        start_frame: pick(start_frame, existing?.start_frame),
        end_frame: pick(end_frame, existing?.end_frame),
        status: incoming,
        user: pick(user, existing?.user) || null,
        ts: Date.now()
    });
    return true;
}

function recordSceneSafe(code, scene) {
    try {
        return recordScene(code, scene);
    } catch (err) {
        console.error("AVS session record failed (non-fatal):", err.message);
        return false;
    }
}

/**
 * Public snapshot for the frontend:
 *
 * - what to hide (scenes)
 * - what to dim (coveredVideos)
 * - the counter
 * - how long until this session idles out
 */
function serializeSession(session) {
    const scenes = [...session.scenes.values()];
    const coveredVideos = [...new Set(
        scenes.filter((s) => holdsVideo(s.status)).map((s) => s.video_id)
    )];
    const distinctVideos = new Set(scenes.map((s) => s.video_id)).size;
    return {
        code: session.code,
        name: session.name,
        scenes: scenes.map((s) => ({
            scene_id: s.scene_id,
            video_id: s.video_id,
            start_frame: s.start_frame,
            end_frame: s.end_frame,
            status: s.status,
            user: s.user
        })),
        coveredVideos,
        counts: {instances: scenes.length, distinctVideos},
        expiresInMs: Math.max(0, IDLE_TIMEOUT_MS - (Date.now() - session.lastActivity))
    };
}

function listSessions() {
    return [...sessions.values()].map((s) => ({
        code: s.code,
        name: s.name,
        instances: s.scenes.size,
        expiresInMs: Math.max(0, IDLE_TIMEOUT_MS - (Date.now() - s.lastActivity))
    }));
}

const sweeper = setInterval(() => {
    const now = Date.now();
    for (const [code, session] of sessions) {
        if (now - session.lastActivity > IDLE_TIMEOUT_MS) {
            sessions.delete(code);
            console.log(`AVS session ${code} expired (idle > ${IDLE_TIMEOUT_MS} ms)`);
        }
    }
}, SWEEP_INTERVAL_MS);

if (typeof sweeper.unref === "function") sweeper.unref();

module.exports = {
    IDLE_TIMEOUT_MS,
    sceneKey,
    holdsVideo,
    isTerminal: isFinal,
    createSession,
    getSession,
    peekSession,
    recordScene,
    recordSceneSafe,
    serializeSession,
    listSessions
};
