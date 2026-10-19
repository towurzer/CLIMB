const teamClient = require('../teamClient');
const queries = require('../models/queries');

const UNREACHABLE = "Can't reach the team service. Search and DRES submission are unaffected.";

// A covered MVK clip covers its whole recording, see queries.expandToRecordings.
const withRecordings = async (team) => {
    if (!team?.session) return team;
    const coveredVideos = await queries.expandToRecordings(team.session.coveredVideos || []);
    return {...team, session: {...team.session, coveredVideos}};
};

// 404 from the service means the team is gone (expired, or recreated with another password): the
// connection has been dropped, and the frontend shows the button grey again with a reason.
const send = async (res, result) => {
    if (result.status === 404) {
        return res.status(200).json({connected: false, lost: true});
    }
    if (result.status === 503 && !result.team) return res.status(503).json({error: UNREACHABLE});
    if (result.status >= 400 && result.status !== 503) {
        return res.status(result.status).json({error: result.error || "Request refused."});
    }
    return res.status(200).json(await withRecordings(result.team));
};

exports.listTeams = async (req, res) => {
    const result = await teamClient.list();
    if (result.status === 503) return res.status(503).json({error: UNREACHABLE, teams: []});
    res.status(200).json({teams: result.teams});
};

exports.connect = async (req, res) => {
    const {team, password, user} = req.body || {};
    if (!String(user || "").trim()) return res.status(400).json({error: "Enter a username."});
    const result = await teamClient.connect(team, password, user);
    if (result.status === 503) return res.status(503).json({error: UNREACHABLE});
    if (result.status >= 400) return res.status(result.status).json({error: result.error});
    console.log(`Connected to team ${result.team.name} as ${result.team.user}`);
    res.status(result.status).json(await withRecordings(result.team));
};

exports.disconnect = (req, res) => res.status(200).json(teamClient.disconnect());

exports.getTeam = async (req, res) => send(res, await teamClient.get());

exports.newSession = async (req, res) => send(res, await teamClient.newSession());

exports.sendHint = async (req, res) => {
    const {video_id, recording_id, keyframe_id, scene_id, keyframe_time_ms, note} = req.body || {};
    if (!video_id) return res.status(400).json({error: "video_id is required."});
    send(res, await teamClient.sendHint({video_id, recording_id, keyframe_id, scene_id, keyframe_time_ms, note}));
};
