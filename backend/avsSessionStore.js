const IDLE_TIMEOUT_MS = 5 * 60 * 1000; // delete a session after 5 min with no interaction
const SWEEP_INTERVAL_MS = 30 * 1000;   // how often the idle sweeper runs
const CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ";
const CODE_LENGTH = 4;

/** code -> { code, name, createdAt, lastActivity, scenes: Map<sceneKey, entry> } */
const sessions = new Map();

const sceneKey = (videoId, startFrame, endFrame) => `${videoId}_${startFrame}_${endFrame}`;

const holdsVideo = (status) => status === "CORRECT" || status === "INDETERMINATE";

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

function createSession(name) {
    const now = Date.now();
    const session = {
        code: randomCode(),
        name: name || null,
        createdAt: now,
        lastActivity: now,
        scenes: new Map() // sceneKey -> { video_id, shot_id, status, user, ts }
    };
    sessions.set(session.code, session);
    return session;
}

function recordScene(code, {video_id, start_frame, end_frame, status, user}) {
    if (start_frame === undefined || start_frame === null) return false;
    const session = getSession(code);
    if (!session) return false;
    session.scenes.set(sceneKey(video_id, start_frame, end_frame), {
        video_id,
        start_frame,
        end_frame,
        status: status || "INDETERMINATE",
        user: user || null,
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

// Public snapshot for the frontend: what to hide (scenes), what to dim
// (coveredVideos), the counter, and how long until this session idles out.
function serializeSession(session) {
    const scenes = [...session.scenes.values()];
    const coveredVideos = [...new Set(
        scenes.filter((s) => holdsVideo(s.status)).map((s) => s.video_id)
    )];
    const distinctVideos = new Set(scenes.map((s) => s.video_id)).size;
    return {
        code: session.code,
        name: session.name,
        scenes: scenes.map((s) => ({video_id: s.video_id, start_frame: s.start_frame, end_frame: s.end_frame, status: s.status})),
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
    createSession,
    getSession,
    recordScene,
    recordSceneSafe,
    serializeSession,
    listSessions
};
