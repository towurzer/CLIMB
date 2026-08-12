const store = require('./sessionStore');

const normalizeCode = (code) => (code || "").toUpperCase();

const withSession = (req, res, fn) => {
    const session = store.getSession(normalizeCode(req.params.code));
    if (!session) return res.status(404).json({error: "AVS session not found or expired."});
    return fn(session);
};

exports.create = (req, res) => {
    const session = store.createSession(req.body?.name);
    console.log(`AVS session ${session.code} created${session.name ? ` (${session.name})` : ""}`);
    res.status(201).json(store.serializeSession(session));
};

exports.list = (req, res) => {
    res.status(200).json({sessions: store.listSessions()});
};

exports.join = (req, res) =>
    withSession(req, res, (session) => res.status(200).json(store.serializeSession(session)));

// Doubles as the heartbeat: getSession refreshes lastActivity.
exports.get = (req, res) =>
    withSession(req, res, (session) => res.status(200).json(store.serializeSession(session)));

exports.recordScene = (req, res) => {
    const code = normalizeCode(req.params.code);
    const {scene_id, video_id, start_frame, end_frame, status, user} = req.body || {};
    if (scene_id === undefined || scene_id === null) {
        return res.status(400).json({error: "scene_id is required."});
    }
    if (!store.recordScene(code, {scene_id, video_id, start_frame, end_frame, status, user})) {
        return res.status(404).json({error: "AVS session not found or expired."});
    }

    return res.status(200).json(store.serializeSession(store.getSession(code)));
};
