const axios = require('axios');

const {AVS_SESSION_SERVICE_URL} = require('./serviceUrls');
// One implementation of the store, owned by the service package. In local mode the backend runs it
// in-process; in remote mode the service runs it and the backend keeps a mirror of its snapshots.
const store = require('../climb-avs-service/sessionStore');

const REMOTE = AVS_SESSION_SERVICE_URL;
const TOKEN = process.env.AVS_SESSION_TOKEN || null;

// Short on purpose. Nothing on this path is allowed to introduce latency, either it works or we work offline, no waiting
const TIMEOUT_MS = parseInt(process.env.AVS_REMOTE_TIMEOUT_MS || '2500', 10);

// How stale a mirror entry may be before a read kicks off a refresh. Smaller than the frontend's
// 4 s poll, so in practice every poll refreshes, which is also what keeps the remote session's idle timer alive.
const MIRROR_TTL_MS = parseInt(process.env.AVS_MIRROR_TTL_MS || '1500', 10);

// Who submitted what.
const USER = process.env.CLIMB_USER || null;

const mode = REMOTE ? "remote" : "local";

if (REMOTE && !TOKEN) {
    console.warn("AVS_SESSION_SERVICE_URL is set but AVS_SESSION_TOKEN is not. " +
        "Every call to the session service will be rejected with 401.");
}

console.log(mode === "remote"
    ? `AVS sessions: shared via ${REMOTE}`
    : "AVS sessions: in-process (set AVS_SESSION_SERVICE_URL to collaborate across machines)");

/** code -> {snapshot, scenes: Map<sceneKey, entry>, fetchedAt} */
const mirror = new Map();

// Last thing we learned about the remote, for /climb/health and the `collab` field.
let remoteReachable = null; // null = not tried yet

const authHeaders = () => (TOKEN ? {Authorization: `Bearer ${TOKEN}`} : {});

const url = (suffix) => `${REMOTE}${suffix}`;

const scenesToMap = (scenes) => new Map(
    (scenes || []).map((s) => [store.sceneKey(s.scene_id), s])
);

// Snapshots come off the wire with `scenes` as an array; the read path wants a Map keyed the same
// way the in-process store keys it, so both modes can share one filter implementation.
function putMirror(snapshot) {
    mirror.set(snapshot.code, {
        snapshot,
        scenes: scenesToMap(snapshot.scenes),
        fetchedAt: Date.now()
    });
    return snapshot;
}

const collabState = () => {
    if (mode === "local") return "local";
    return remoteReachable === false ? "offline" : "online";
};

/**
 * Remote call wrapper. Distinguishes the three outcomes that matter:
 *   {ok: true, data}            - fine
 *   {ok: false, status: 404}    - the session is genuinely gone
 *   {ok: false, status: null}   - we could not reach the service; say nothing about the session
 */
async function call(method, suffix, body) {
    try {
        const res = await axios({
            method,
            url: url(suffix),
            data: body,
            headers: authHeaders(),
            timeout: TIMEOUT_MS
        });
        remoteReachable = true;
        return {ok: true, data: res.data};
    } catch (err) {
        const status = err.response?.status || null;
        // A 4xx/5xx is an answer: the service is up and told us something. Only a transport failure
        // means "unreachable", and only that should flip the collaboration indicator.
        remoteReachable = status !== null;
        if (!status) {
            console.warn(`AVS session service unreachable (${method} ${suffix}):`, err.code || err.message);
        } else if (status === 401) {
            console.error("AVS session service rejected our token (401). Check AVS_SESSION_TOKEN.");
        }
        return {ok: false, status, detail: err.response?.data?.error || err.message};
    }
}

/**
 * Synchronous, never touches the network. This is what search filters against, so it has to stay sync and it has to stay free
 *
 * Returns {scenes: Map} or null.
 */
function getMirror(code) {
    if (!code) return null;
    const key = String(code).toUpperCase();
    if (mode === "local") return store.peekSession(key);
    return mirror.get(key) || null;
}

async function refresh(code) {
    const key = String(code).toUpperCase();
    const res = await call('get', `/avs/session/${key}`);
    if (res.ok) return {ok: true, snapshot: putMirror(res.data)};
    // Only a definite 404 clears the mirror. If we merely could not reach the service, the last
    // known snapshot is still the best information we have and the team keeps working with it.
    if (res.status === 404) mirror.delete(key);
    return {ok: false, status: res.status};
}

/**
 * The frontend poll path.
 *
 * A stale mirror is refreshed *before* answering, not in the background.
 *
 * The mirror is still what makes this safe: if the refresh cannot be completed, the last known
 * snapshot is served anyway and only the collab flag changes.
 *
 * Returns {status: 200|404|503, snapshot?, collab}
 */
async function getSnapshot(code) {
    const key = String(code).toUpperCase();

    if (mode === "local") {
        const session = store.getSession(key);
        if (!session) return {status: 404, collab: "local"};
        return {status: 200, snapshot: store.serializeSession(session), collab: "local"};
    }

    const entry = mirror.get(key);
    const fresh = entry && Date.now() - entry.fetchedAt <= MIRROR_TTL_MS;

    // Within the TTL there is nothing to gain from asking again; this is what keeps a chatty
    // frontend (or several browser tabs) from multiplying traffic to the service.
    if (fresh) return {status: 200, snapshot: entry.snapshot, collab: collabState()};

    const res = await refresh(key);
    if (res.ok) return {status: 200, snapshot: res.snapshot, collab: collabState()};
    if (res.status === 404) return {status: 404, collab: collabState()};

    // Unreachable. Serve the stale snapshot if we have one.
    if (entry) return {status: 200, snapshot: entry.snapshot, collab: collabState()};
    return {status: 503, collab: collabState()};
}

async function list() {
    if (mode === "local") return {status: 200, sessions: store.listSessions(), collab: "local"};
    const res = await call('get', '/avs/sessions');
    if (res.ok) return {status: 200, sessions: res.data.sessions || [], collab: collabState()};
    return {status: 503, sessions: [], collab: collabState()};
}

async function create(name) {
    if (mode === "local") {
        return {status: 201, snapshot: store.serializeSession(store.createSession(name)), collab: "local"};
    }
    const res = await call('post', '/avs/session', {name: name || null});
    if (res.ok) return {status: 201, snapshot: putMirror(res.data), collab: collabState()};
    return {status: 503, collab: collabState(), detail: res.detail};
}

async function join(code) {
    const key = String(code || "").toUpperCase();

    if (mode === "local") {
        const session = store.getSession(key);
        if (!session) return {status: 404, collab: "local"};
        return {status: 200, snapshot: store.serializeSession(session), collab: "local"};
    }

    const res = await call('post', `/avs/session/${key}/join`);
    if (res.ok) return {status: 200, snapshot: putMirror(res.data), collab: collabState()};
    if (res.status === 404) {
        mirror.delete(key);
        return {status: 404, collab: collabState()};
    }
    return {status: 503, collab: collabState(), detail: res.detail};
}

/**
 * Record a delivered submission. Best-effort by contract: the DRES answer is what scores, and this
 * overlay must never block it, alter it, or throw into it.
 */
async function record(code, scene) {
    if (!code) return false;
    const key = String(code).toUpperCase();
    const payload = {...scene, user: scene.user || USER};

    if (mode === "local") return store.recordSceneSafe(key, payload);

    try {
        const res = await call('post', `/avs/session/${key}/scene`, payload);
        if (res.ok) {
            // The service hands back the fresh snapshot, so the submitter's own mirror is correct
            // immediately rather than one poll later.
            putMirror(res.data);
            return true;
        }
        console.warn(`AVS session record failed (non-fatal, HTTP ${res.status || "unreachable"}):`, res.detail);
        return false;
    } catch (err) {
        console.error("AVS session record failed (non-fatal):", err.message);
        return false;
    }
}

const HEALTH_CACHE_MS = 4000;
let healthCache = {at: 0, value: null};

/**
 * Actively probed rather than inferred from the last call, because when nobody is in AVS mode there
 * are no calls to infer from and a passive flag would report "online" forever.
 */
async function health() {
    if (mode === "local") {
        return {configured: false, reachable: true, detail: "AVS sessions are in-process (no shared service configured)"};
    }
    if (Date.now() - healthCache.at < HEALTH_CACHE_MS && healthCache.value) {
        return healthCache.value;
    }
    let value;
    try {
        await axios.get(url('/health'), {timeout: TIMEOUT_MS});
        remoteReachable = true;
        value = {configured: true, reachable: true, detail: "AVS session service reachable"};
    } catch (err) {
        remoteReachable = false;
        const reason = err.code || (err.response && `HTTP ${err.response.status}`) || err.message;
        value = {
            configured: true,
            reachable: false,
            detail: `AVS session service unreachable: ${reason}. Collaboration is off; search and submit are unaffected.`
        };
    }
    healthCache = {at: Date.now(), value};
    return value;
}

module.exports = {
    mode,
    sceneKey: store.sceneKey,
    holdsVideo: store.holdsVideo,
    getMirror,
    getSnapshot,
    refresh,
    list,
    create,
    join,
    record,
    health,
    collabState
};
