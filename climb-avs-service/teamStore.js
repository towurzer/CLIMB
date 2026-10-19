const crypto = require('crypto');

const sessions = require('./sessionStore');

const TEAM_IDLE_TIMEOUT_MS = parseInt(process.env.AVS_TEAM_IDLE_TIMEOUT_MS || `${7 * 24 * 60 * 60 * 1000}`, 10);
const SWEEP_INTERVAL_MS = parseInt(process.env.AVS_SWEEP_INTERVAL_MS || `${60 * 1000}`, 10);
const MAX_HINTS = 200;
const MAX_NAME = 40;
const MAX_NOTE = 500;

/**
 * lower-cased name -> { name, salt, hash, token, createdAt, lastActivity, sessionCode, hints: [] }
 *
 * A team is the people working together at a competition. It holds the current AVS session (a new
 * AVS task starts a new one for everybody) and the hints they send each other.
 */
const teams = new Map();

const teamKey = (name) => String(name || "").trim().toLowerCase();

const hashPassword = (password, salt) => crypto.scryptSync(String(password), salt, 32);

const sameBytes = (a, b) => a.length === b.length && crypto.timingSafeEqual(a, b);

const clip = (value, max) => (value === undefined || value === null ? null : String(value).slice(0, max));

const expiresInMs = (team) => Math.max(0, TEAM_IDLE_TIMEOUT_MS - (Date.now() - team.lastActivity));

// The team's current session, refreshed. One that idled out (nobody polled for 2 h) is replaced.
function currentSession(team) {
    const session = sessions.getSession(team.sessionCode);
    if (session) return session;
    const fresh = sessions.createSession(team.name);
    team.sessionCode = fresh.code;
    return fresh;
}

/**
 * Join a team, or create it when the name is free.
 *
 * Returns {status: 200|201, team} on success, {status: 401} for a wrong password and {status: 400}
 * for a missing name or password.
 */
function connect(name, password) {
    const cleanName = String(name || "").trim().slice(0, MAX_NAME);
    if (!cleanName || !password) return {status: 400, error: "Team name and password are required."};

    const existing = teams.get(teamKey(cleanName));
    if (existing) {
        if (!sameBytes(hashPassword(password, existing.salt), existing.hash)) {
            return {status: 401, error: "Wrong password for this team."};
        }
        existing.lastActivity = Date.now();
        return {status: 200, team: existing};
    }

    const salt = crypto.randomBytes(16);
    const now = Date.now();
    const team = {
        name: cleanName,
        salt,
        hash: hashPassword(password, salt),
        token: crypto.randomBytes(24).toString('hex'),
        createdAt: now,
        lastActivity: now,
        sessionCode: null,
        hints: []
    };
    currentSession(team);
    teams.set(teamKey(cleanName), team);
    return {status: 201, team};
}

/** The team for a name and the token handed out on connect, refreshed; null if either is wrong. */
function authorize(name, token) {
    const team = teams.get(teamKey(name));
    if (!team || !token || !sameBytes(Buffer.from(String(token)), Buffer.from(team.token))) return null;
    team.lastActivity = Date.now();
    return team;
}

function newSession(team) {
    team.sessionCode = sessions.createSession(team.name).code;
    return team;
}

function addHint(team, {user, video_id, recording_id, keyframe_id, scene_id, keyframe_time_ms, note}) {
    if (!video_id) return null;
    const hint = {
        id: crypto.randomBytes(8).toString('hex'),
        ts: Date.now(),
        user: clip(user, MAX_NAME),
        video_id: clip(video_id, 100),
        recording_id: clip(recording_id, 100) || clip(video_id, 100),
        keyframe_id: Number.isInteger(keyframe_id) ? keyframe_id : null,
        scene_id: Number.isInteger(scene_id) ? scene_id : null,
        keyframe_time_ms: Number.isFinite(keyframe_time_ms) ? keyframe_time_ms : null,
        note: clip(note, MAX_NOTE) || null
    };
    team.hints.push(hint);
    if (team.hints.length > MAX_HINTS) team.hints.splice(0, team.hints.length - MAX_HINTS);
    return hint;
}

/** What a member sees: the current session (what to hide and dim) and every hint, newest first. */
function serializeTeam(team) {
    return {
        name: team.name,
        expiresInMs: expiresInMs(team),
        session: sessions.serializeSession(currentSession(team)),
        hints: [...team.hints].reverse()
    };
}

function listTeams() {
    return [...teams.values()]
        .map((team) => ({name: team.name, expiresInMs: expiresInMs(team)}))
        .sort((a, b) => a.name.localeCompare(b.name));
}

const sweeper = setInterval(() => {
    const now = Date.now();
    for (const [key, team] of teams) {
        if (now - team.lastActivity > TEAM_IDLE_TIMEOUT_MS) {
            teams.delete(key);
            console.log(`Team ${team.name} expired (idle > ${TEAM_IDLE_TIMEOUT_MS} ms)`);
        }
    }
}, SWEEP_INTERVAL_MS);

if (typeof sweeper.unref === "function") sweeper.unref();

module.exports = {
    TEAM_IDLE_TIMEOUT_MS,
    connect,
    authorize,
    newSession,
    addHint,
    serializeTeam,
    listTeams
};
