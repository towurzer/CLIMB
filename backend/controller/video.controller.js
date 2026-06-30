const queries = require('../models/queries');
const path = require('path');
const { createClient } = require('redis');

// Caching Videos to reduce load time
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const VIDEOS_CACHE_TTL_SECONDS = process.env.VIDEOS_CACHE_TTL_SECONDS ||30;

const redisClient = createClient({ url: REDIS_URL, socket: { connectTimeout: 3000, reconnectStrategy: false } });
redisClient.on('error', (err) => {
    // We don't like errors
});
(async () => {
    try {
        await Promise.race([
            redisClient.connect(),
            new Promise((_, reject) => setTimeout(() => reject(new Error('Redis connect timeout')), 3000))
        ]);
        console.log('Connected to Redis');
    } catch (err) {
        console.log('Redis not available, continuing without caching');
    }
})();

exports.listVideos = async (req, res) => {
    console.log('all videos queried')

    const page = parseInt(req.query.page || '1');
    const perPage = Math.min(parseInt(req.query.per_page || '25'), 100);

    const cacheKey = `videos:page:${page}:perPage:${perPage}`;

    // Let's look at our cache
    try {
        if (redisClient && redisClient.isOpen) {
            const cached = await redisClient.get(cacheKey);
            if (cached) {
                const parsed = JSON.parse(cached);

                if (parsed.count !== undefined && parsed.total === undefined) {
                    parsed.total = parsed.count;
                    delete parsed.count;
                }
                console.log(`Returning cached videos page=${page} perPage=${perPage} videos=${(parsed.videos||[]).length}`);
                return res.status(200).json({ ...parsed, cached: true });
            }
        }
    } catch (err) {
        console.warn('Redis get failed, continuing without cache:', err.message || err);
    }

    const result = await queries.getAllVideos(page, perPage);

    // Cache miss, load video into cach and use this :)
    try {
        if (redisClient && redisClient.isOpen) {
            await redisClient.setEx(cacheKey, VIDEOS_CACHE_TTL_SECONDS, JSON.stringify(result));
        }
    } catch (err) {
        console.warn('Redis set failed:', err.message || err);
    }

    console.log(`Returning fresh videos page=${page} perPage=${perPage} videos=${(result.videos||[]).length}`);
    res.status(200).json(result);
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