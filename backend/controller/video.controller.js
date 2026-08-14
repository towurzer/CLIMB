const queries = require('../models/queries');
const cache = require('../cache');

const VIDEOS_CACHE_TTL_SECONDS = parseInt(process.env.VIDEOS_CACHE_TTL_SECONDS || '30', 10);

exports.listVideos = async (req, res) => {
    const page = Math.max(parseInt(req.query.page || '1', 10), 1);
    const perPage = Math.min(Math.max(parseInt(req.query.per_page || '25', 10), 1), 100);
    const key = `videos:page:${page}:perPage:${perPage}`;

    const cached = await cache.get(key);
    if (cached) return res.status(200).json({...cached, cached: true});

    const result = await queries.getAllVideos(page, perPage);
    await cache.set(key, result, VIDEOS_CACHE_TTL_SECONDS);
    res.status(200).json({...result, cached: false});
};

exports.getVideoInfo = async (req, res) => {
    const {video_id} = req.params;
    const details = await queries.getVideoDetails(video_id);
    if (!details) return res.status(404).json({error: `No such video: ${video_id}`});
    res.status(200).json(details);
};

/** Scenes of a video with their keyframes, what the filmstrip and shot browser render. */
exports.getVideoScenes = async (req, res) => {
    const {video_id} = req.params;
    const hasPagination = req.query.page !== undefined || req.query.per_page !== undefined;
    const page = hasPagination ? Math.max(parseInt(req.query.page || '1', 10), 1) : null;
    const perPage = hasPagination
        ? Math.min(Math.max(parseInt(req.query.per_page || '60', 10), 1), 200)
        : null;

    const {total, scenes, fps, duration_sec} = await queries.getVideoScenes(video_id, page, perPage);
    res.status(200).json({video_id, scenes, total, fps, duration_sec});
};
