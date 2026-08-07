const {Pool, types} = require('pg');
const axios = require('axios');
const path = require('path');
require('dotenv').config({path: path.resolve(__dirname, '../../.env')});

const {SEARCH_ENGINE_URL} = require('../serviceUrls');
const media = require('../mediaPaths');

types.setTypeParser(types.builtins.INT8, (value) => parseInt(value, 10));

const pool = new Pool({
    user: process.env.POSTGRES_USER || 'postgres',
    password: process.env.POSTGRES_PASSWORD,
    host: process.env.DB_HOST || 'localhost',
    port: process.env.DB_PORT || 5432,
    database: process.env.POSTGRES_DB_NAME || 'climb_db'
});

pool.on('error', (err) => console.error('Postgres pool error:', err.message));

/**
 * Shape a worker result for the frontend.
 *
 * The worker returns scenes; every URL is derived from (video_id, shot_index, kf_index) rather
 * than read out of the database.
 */
const toResult = (hit) => ({
    scene_id: hit.scene_id,
    keyframe_id: hit.keyframe_id,
    video_id: hit.video_id,
    shot_index: hit.shot_index,
    kf_index: hit.kf_index,
    score: hit.score,
    signals: hit.signals || {},
    start_frame: hit.start_frame,
    end_frame: hit.end_frame,
    frame_number: hit.frame_number,
    fps: hit.fps,
    start_time_ms: hit.start_ms,
    end_time_ms: hit.end_ms,
    keyframe_time_ms: hit.ts_ms,
    damaged: hit.damaged,
    thumbnail_url: media.thumbnailUrl(hit.video_id, hit.shot_index, hit.kf_index),
    keyframe_url: media.keyframeUrl(hit.video_id, hit.shot_index, hit.kf_index),
    ...(hit.temporal_partners ? {
        temporal_partners: hit.temporal_partners.map(toResult),
        temporal_gaps_ms: hit.temporal_gaps_ms
    } : {})
});

module.exports = {
    /**
     * Ask the worker for a deep result set.
     *
     * Depth, not page: the controller caches this once and pages from the cache. Previously each
     * page re-ran the whole search with topK = page * perPage and threw the earlier rows away, so
     * page three cost three searches.
     *
     * Returns the whole payload rather than a bare array because `temporal` describes the result
     * set, not any one row, and it has to survive the controller's cache along with it.
     */
    searchByText: async (queryText, exclude = [], depth = 500) => {
        const res = await axios.post(`${SEARCH_ENGINE_URL}/api/search`, {
            prompt: queryText,
            exclude,
            top_k: depth
        });
        return {
            results: (res.data.results || []).map(toResult),
            temporal: res.data.temporal || null
        };
    },

    /**
     * Scenes similar to a keyframe.
     *
     * Also the worker's job: it owns the one implementation of oversample-then-rerank, and a
     * second copy here would drift silently into a sequential scan.
     */
    findSimilarByKeyframe: async (keyframeId, exclude = [], depth = 500) => {
        const res = await axios.post(`${SEARCH_ENGINE_URL}/api/similar`, {
            keyframe_id: keyframeId,
            exclude,
            top_k: depth
        });
        // Same payload shape as searchByText; find-similar is never a sequence.
        return {results: (res.data.results || []).map(toResult), temporal: null};
    },

    getAllVideos: async (page = 1, perPage = 20) => {
        const offset = (page - 1) * perPage;

        const countSql = `SELECT COUNT(*)::int AS total FROM videos;`;
        // One representative keyframe per video: the first keyframe of its first shot.
        const videosSql = `
            SELECT v.video_id,
                   v.fps,
                   v.duration_ms,
                   v.width,
                   v.height,
                   v.damaged,
                   (SELECT COUNT(*)::int FROM scenes s WHERE s.video_id = v.video_id) AS num_scenes,
                   cover.shot_index,
                   cover.kf_index
            FROM videos v
                     LEFT JOIN LATERAL (
                SELECT s.shot_index, k.kf_index
                FROM scenes s
                         JOIN keyframes k ON k.scene_id = s.scene_id
                WHERE s.video_id = v.video_id
                ORDER BY s.shot_index, k.kf_index
                LIMIT 1) cover ON TRUE
            ORDER BY v.video_id
            LIMIT $1 OFFSET $2;
        `;

        const [countRes, videosRes] = await Promise.all([
            pool.query(countSql),
            pool.query(videosSql, [perPage, offset])
        ]);

        const videos = videosRes.rows.map((row) => ({
            video_id: row.video_id,
            fps: row.fps,
            duration_sec: Math.round((row.duration_ms || 0) / 1000),
            width: row.width,
            height: row.height,
            damaged: row.damaged,
            num_scenes: row.num_scenes,
            thumbnail_url: row.shot_index === null
                ? null
                : media.thumbnailUrl(row.video_id, row.shot_index, row.kf_index)
        }));

        return {total: countRes.rows[0].total || 0, videos};
    },

    getVideoDetails: async (videoId) => {
        const {rows} = await pool.query(
            `SELECT video_id, fps, duration_ms, width, height, damaged, collection
             FROM videos
             WHERE video_id = $1`, [videoId]);
        if (rows.length === 0) return null;
        const v = rows[0];
        return {
            video_id: v.video_id,
            fps: v.fps,
            duration_sec: Math.round((v.duration_ms || 0) / 1000),
            width: v.width,
            height: v.height,
            damaged: v.damaged,
            collection: v.collection,
            video_url: media.videoUrl(v.video_id)
        };
    },

    /** Scenes of a video, with their keyframes -- the filmstrip. */
    getVideoScenes: async (videoId, page = null, perPage = null) => {
        const usePagination = page !== null && perPage !== null;
        const offset = usePagination ? (page - 1) * perPage : null;

        const scenesSql = `
            SELECT s.scene_id,
                   s.shot_index,
                   s.start_frame,
                   s.end_frame,
                   s.start_ms,
                   s.end_ms,
                   k.keyframe_id,
                   k.kf_index,
                   k.frame_number,
                   k.ts_ms
            FROM scenes s
                     LEFT JOIN keyframes k ON k.scene_id = s.scene_id
            WHERE s.video_id = $1
            ORDER BY s.shot_index, k.kf_index
            ${usePagination ? 'LIMIT $2 OFFSET $3' : ''};
        `;

        const [scenesRes, countRes, videoRes] = await Promise.all([
            pool.query(scenesSql, usePagination ? [videoId, perPage, offset] : [videoId]),
            pool.query(`SELECT COUNT(*)::int AS total FROM scenes WHERE video_id = $1;`, [videoId]),
            pool.query(`SELECT fps FROM videos WHERE video_id = $1`, [videoId])
        ]);

        const fps = videoRes.rows.length > 0 ? videoRes.rows[0].fps : 25.0;

        // Collapse the scene/keyframe join into one entry per scene.
        const byScene = new Map();
        for (const row of scenesRes.rows) {
            if (!byScene.has(row.scene_id)) {
                byScene.set(row.scene_id, {
                    scene_id: row.scene_id,
                    shot_index: row.shot_index,
                    start_frame: row.start_frame,
                    end_frame: row.end_frame,
                    start_time_ms: row.start_ms,
                    end_time_ms: row.end_ms,
                    fps,
                    keyframes: []
                });
            }
            if (row.keyframe_id !== null) {
                byScene.get(row.scene_id).keyframes.push({
                    keyframe_id: row.keyframe_id,
                    kf_index: row.kf_index,
                    frame_number: row.frame_number,
                    keyframe_time_ms: row.ts_ms,
                    thumbnail_url: media.thumbnailUrl(videoId, row.shot_index, row.kf_index),
                    keyframe_url: media.keyframeUrl(videoId, row.shot_index, row.kf_index)
                });
            }
        }

        const scenes = [...byScene.values()].map((scene) => ({
            ...scene,
            thumbnail_url: scene.keyframes[0]?.thumbnail_url || null
        }));

        return {total: countRes.rows[0].total || 0, scenes};
    },

    /** Frame range of a scene, so a submission can be recorded against it. */
    getScene: async (sceneId) => {
        const {rows} = await pool.query(
            `SELECT scene_id, video_id, shot_index, start_frame, end_frame, start_ms, end_ms
             FROM scenes
             WHERE scene_id = $1`, [sceneId]);
        return rows[0] || null;
    }
};
