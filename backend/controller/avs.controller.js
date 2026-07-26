const avsStore = require('../avsSessionStore');

const respondWithSession = (req, res) => {
    const session = avsStore.getSession((req.params.code || "").toUpperCase());
    if (!session) return res.status(404).json({error: "AVS session not found or expired."});
    res.status(200).json(avsStore.serializeSession(session));
};

exports.createAvsSession = (req, res) => {
    const session = avsStore.createSession(req.body?.name);
    console.log(`AVS session ${session.code} created${session.name ? ` (${session.name})` : ""}`);
    res.status(201).json(avsStore.serializeSession(session));
};

exports.joinAvsSession = respondWithSession;

exports.getAvsSession = respondWithSession;

exports.listAvsSessions = (req, res) => {
    res.status(200).json({sessions: avsStore.listSessions()});
};
