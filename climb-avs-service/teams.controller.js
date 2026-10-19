const store = require('./teamStore');

// The token handed out on connect. A team that idled out, or was recreated with another password,
// no longer accepts it: 404 either way, the member has to connect again.
const withTeam = (req, res, fn) => {
    const team = store.authorize(req.params.name, req.get('x-team-token'));
    if (!team) return res.status(404).json({error: "Team not found or expired, connect again."});
    return fn(team);
};

exports.list = (req, res) => {
    res.status(200).json({teams: store.listTeams()});
};

exports.connect = (req, res) => {
    const result = store.connect(req.body?.name, req.body?.password);
    if (!result.team) return res.status(result.status).json({error: result.error});
    if (result.status === 201) console.log(`Team ${result.team.name} created`);
    res.status(result.status).json({token: result.team.token, team: store.serializeTeam(result.team)});
};

// Doubles as the heartbeat for the team and its current session.
exports.get = (req, res) =>
    withTeam(req, res, (team) => res.status(200).json(store.serializeTeam(team)));

exports.newSession = (req, res) =>
    withTeam(req, res, (team) => res.status(200).json(store.serializeTeam(store.newSession(team))));

exports.hint = (req, res) =>
    withTeam(req, res, (team) => {
        if (!store.addHint(team, req.body || {})) return res.status(400).json({error: "video_id is required."});
        return res.status(200).json(store.serializeTeam(team));
    });
