/**
 * This backend's membership in a team, like the DRES login: one connection per local stack.
 *
 * Same two modes as avsSessionClient: without AVS_SESSION_SERVICE_URL the team store runs
 * in-process (a team of one), with it the service holds the teams and this keeps the last snapshot.
 * The same three rules apply: the snapshot refreshes the session mirror search filters against, a
 * 404 means the team is gone (disconnect), and an unreachable service keeps the last snapshot.
 */

const avs = require('./avsSessionClient');
const store = require('../climb-avs-service/teamStore');

// {name, token, user, snapshot, fetchedAt} while connected
let connection = null;

const teamPath = (name) => `/team/${encodeURIComponent(name)}`;
const teamHeaders = () => ({'X-Team-Token': connection.token});

// Remember a fresh snapshot, and hand its session to the mirror search filters against.
function keep(snapshot) {
    connection.snapshot = snapshot;
    connection.fetchedAt = Date.now();
    if (avs.mode === "remote" && snapshot.session) avs.putMirror(snapshot.session);
    return snapshot;
}

/** What the frontend shows. */
function view(collab = avs.collabState()) {
    if (!connection) return {connected: false, collab};
    return {connected: true, user: connection.user, ...connection.snapshot, collab};
}

const currentUser = () => connection?.user || null;

async function list() {
    if (avs.mode === "local") return {status: 200, teams: store.listTeams()};
    const res = await avs.call('get', '/teams');
    if (res.ok) return {status: 200, teams: res.data.teams || []};
    return {status: 503, teams: []};
}

/** Join the team, or create it. {status: 200|201} connected, 400/401 refused, 503 unreachable. */
async function connect(name, password, user) {
    let result;
    if (avs.mode === "local") {
        const outcome = store.connect(name, password);
        result = outcome.team
            ? {ok: true, status: outcome.status, token: outcome.team.token, snapshot: store.serializeTeam(outcome.team)}
            : {ok: false, status: outcome.status, error: outcome.error};
    } else {
        const res = await avs.call('post', '/teams/connect', {name, password});
        result = res.ok
            ? {ok: true, status: res.status, token: res.data.token, snapshot: res.data.team}
            : {ok: false, status: res.status || 503, error: res.detail};
    }
    if (!result.ok) return {status: result.status, error: result.error};

    connection = {name: result.snapshot.name, token: result.token, user: String(user || "").trim() || null};
    keep(result.snapshot);
    return {status: result.status, team: view()};
}

function disconnect() {
    connection = null;
    return view();
}

// One authorised call against the team, local or remote. A 404 ends the connection.
async function teamCall(method, suffix = "", body) {
    if (!connection) return {status: 404};

    if (avs.mode === "local") {
        const team = store.authorize(connection.name, connection.token);
        if (!team) {
            connection = null;
            return {status: 404};
        }
        if (suffix === "/session") store.newSession(team);
        if (suffix === "/hint" && !store.addHint(team, body)) return {status: 400};
        keep(store.serializeTeam(team));
        return {status: 200, team: view()};
    }

    const res = await avs.call(method, `${teamPath(connection.name)}${suffix}`, body, teamHeaders());
    if (res.ok) {
        keep(res.data);
        return {status: 200, team: view()};
    }
    if (res.status === 404) {
        connection = null;
        return {status: 404};
    }
    if (res.status) return {status: res.status, error: res.detail};
    // Unreachable: the last snapshot is still the best there is
    return {status: 503, team: view()};
}

/** The frontend poll; also the heartbeat that keeps the team and its session alive. */
async function get() {
    if (!connection) return {status: 200, team: view()};
    const fresh = Date.now() - connection.fetchedAt <= avs.MIRROR_TTL_MS;
    if (fresh) return {status: 200, team: view()};
    const result = await teamCall('get');
    return result.status === 503 ? {status: 200, team: result.team} : result;
}

const newSession = () => teamCall('post', '/session');

const sendHint = (hint) => teamCall('post', '/hint', {...hint, user: currentUser()});

module.exports = {list, connect, disconnect, get, newSession, sendHint, currentUser};
