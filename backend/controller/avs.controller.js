const avsClient = require('../avsSessionClient');

// 404 and 503 mean very different things to the operator and must never be collapsed:
//   404 - the session is genuinely gone (idled out, or the code never existed)
//   503 - we cannot reach the session service, so we have nothing to say about the session
// Telling a team their session expired when the truth is "your teammate's server is unreachable"
// would have them throw away a perfectly good exclusion list mid-task.
const UNREACHABLE = {
    error: "AVS session service unreachable.",
    collab: "offline",
    hint: "Collaboration is paused. Search and DRES submission are unaffected."
};

const sendSnapshot = (res, result, okStatus = 200) => {
    if (result.status === 404) {
        return res.status(404).json({error: "AVS session not found or expired.", collab: result.collab});
    }
    if (result.status === 503 || !result.snapshot) {
        return res.status(503).json({...UNREACHABLE, detail: result.detail});
    }
    return res.status(okStatus).json({...result.snapshot, collab: result.collab});
};

exports.createAvsSession = async (req, res) => {
    const result = await avsClient.create(req.body?.name);
    if (result.snapshot) {
        console.log(`AVS session ${result.snapshot.code} created` +
            `${result.snapshot.name ? ` (${result.snapshot.name})` : ""} [${avsClient.mode}]`);
    }
    return sendSnapshot(res, result, 201);
};

exports.joinAvsSession = async (req, res) =>
    sendSnapshot(res, await avsClient.join(req.params.code));

exports.getAvsSession = async (req, res) =>
    sendSnapshot(res, await avsClient.getSnapshot(req.params.code));

exports.listAvsSessions = async (req, res) => {
    const result = await avsClient.list();
    if (result.status === 503) return res.status(503).json({...UNREACHABLE, sessions: []});
    return res.status(200).json({sessions: result.sessions, collab: result.collab});
};
