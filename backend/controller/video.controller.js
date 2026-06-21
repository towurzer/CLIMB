const queries = require('../models/queries');

exports.listVideos = async (req, res) => {
    console.log('all videos queried')
    const videos = await queries.getAllVideos();
    res.status(200).json({ count: videos.length, videos });
};

exports.getVideoInfo = async (req, res) => {
    const { video_id } = req.params;
    //TODO check
    console.log(`Querying video details for video ${video_id}`)
    const details = await queries.getVideoDetails(video_id);
    res.status(200).json(details);
};

exports.getVideoShots = async (req, res) => {
    const { video_id } = req.params;
    console.log(`Querying video shots for video ${video_id}`)
    const shots = await queries.getVideoShots(video_id);
    res.status(200).json({ video_id, shots });
};

exports.findSimilar = async (req, res) => {
    const { video_id, shot_id } = req.params;
    console.log(`Searching for similar videos just as ${video_id} and ${shot_id}`)
    const results = await queries.getSimilarShots(video_id, parseInt(shot_id));
    res.status(200).json({ source_video: video_id, source_shot: parseInt(shot_id), results });
};

exports.askShotVQA = async (req, res) => {
    const { video_id, shot_id } = req.params;
    const { question } = req.body;

    if (!question) return res.status(400).json({ error: "Question is required in body" });
    console.log(`Asking the question ${question} (shot based)`)
    // TODO check
    const answer = await queries.askVQA(video_id, shot_id, question);
    res.status(200).json({ question, answer });
};

exports.askVideoVQA = async (req, res) => {
    const { video_id } = req.params;
    const { question } = req.body;


    if (!question) return res.status(400).json({ error: "Question is required in body" });
    //TODO check
    console.log(`Asking the question ${question} (video based)`)
    const answer = await queries.askVQA(video_id, null, question);
    res.status(200).json({ question, answer });
};