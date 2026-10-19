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
/** One keyframe of a scene, in the shape the filmstrip wants. Shared by search and browse. */
const toKeyframe = (videoId, shotIndex, kf) => ({
    keyframe_id: kf.keyframe_id,
    kf_index: kf.kf_index,
    frame_number: kf.frame_number,
    keyframe_time_ms: kf.ts_ms ?? kf.keyframe_time_ms,
    thumbnail_url: media.thumbnailUrl(videoId, shotIndex, kf.kf_index),
    keyframe_url: media.keyframeUrl(videoId, shotIndex, kf.kf_index)
});

const toResult = (hit) => ({
    scene_id: hit.scene_id,
    keyframe_id: hit.keyframe_id,
    video_id: hit.video_id,
    recording_id: hit.recording_id ?? hit.video_id,
    recording_offset_ms: hit.recording_offset_ms ?? 0,
    shot_index: hit.shot_index,
    kf_index: hit.kf_index,
    score: hit.score,
    signals: hit.signals || {},
    contributions: hit.contributions || {},
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
    video_url: media.videoUrl(hit.video_id),
    keyframes: (hit.keyframes || []).map((kf) => toKeyframe(hit.video_id, hit.shot_index, kf)),
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
    searchByText: async (queryText, exclude = [], depth = 500, sources = [], collection = null) => {
        const res = await axios.post(`${SEARCH_ENGINE_URL}/api/search`, {
            prompt: queryText,
            exclude,
            top_k: depth,
            collection,
            // Empty means "every source"; the worker treats null and [] the same way.
            ...(sources.length ? {sources} : {})
        });
        return {
            results: (res.data.results || []).map(toResult),
            temporal: res.data.temporal || null,
            filters: res.data.filters || null
        };
    },

    /**
     * Scenes similar to a keyframe.
     *
     * Also the worker's job: it owns the one implementation of oversample-then-rerank, and a
     * second copy here would drift silently into a sequential scan.
     */
    findSimilarByKeyframe: async (keyframeId, exclude = [], depth = 500, collection = null) => {
        const res = await axios.post(`${SEARCH_ENGINE_URL}/api/similar`, {
            keyframe_id: keyframeId,
            exclude,
            top_k: depth,
            collection
        });
        // Same payload shape as searchByText; find-similar is never a sequence.
        return {results: (res.data.results || []).map(toResult), temporal: null};
    },

    /**
     * Every clip of the recordings the given clips belong to.
     *
     * An MVK dive is cut into 10-40 s clips, and a teammate's correct hit in one clip covers the
     * whole dive, not just that clip. The AVS service only knows clips, so the widening happens
     * here, on the way to the frontend. If the lookup fails the clips come back unchanged: dimming
     * too little is better than the session view breaking.
     */
    expandToRecordings: async (videoIds = []) => {
        if (!videoIds.length) return videoIds;
        try {
            const res = await pool.query(
                `SELECT video_id FROM videos
                 WHERE recording_id IN (SELECT recording_id FROM videos WHERE video_id = ANY ($1))`,
                [videoIds]);
            return [...new Set([...videoIds, ...res.rows.map((row) => row.video_id)])];
        } catch (error) {
            console.warn("Could not widen covered videos to their recordings:", error.message);
            return videoIds;
        }
    },

    /**
     * One entry per recording: an MVK dive is one card, not one per clip. Every other video is its
     * own recording. `video_id` is the recording's first clip, which opens the whole recording.
     */
    getAllVideos: async (page = 1, perPage = 20, collection = null) => {
        const offset = (page - 1) * perPage;

        const countSql = `SELECT COUNT(DISTINCT recording_id)::int AS total FROM videos
                          WHERE ($1::text IS NULL OR collection = $1);`;
        // One representative keyframe per recording: the first keyframe of its first clip's first shot.
        const videosSql = `
            WITH recording AS (SELECT recording_id,
                                      min(video_id)      AS first_clip,
                                      count(*)::int      AS num_clips,
                                      sum(duration_ms)   AS duration_ms,
                                      bool_or(damaged)   AS damaged
                               FROM videos
                               WHERE ($3::text IS NULL OR collection = $3)
                               GROUP BY recording_id
                               ORDER BY recording_id
                               LIMIT $1 OFFSET $2)
            SELECT r.recording_id,
                   r.first_clip AS video_id,
                   r.num_clips,
                   r.duration_ms,
                   r.damaged,
                   v.fps,
                   v.width,
                   v.height,
                   (SELECT COUNT(*)::int
                    FROM scenes s
                             JOIN videos c ON c.video_id = s.video_id
                    WHERE c.recording_id = r.recording_id) AS num_scenes,
                   cover.shot_index,
                   cover.kf_index
            FROM recording r
                     JOIN videos v ON v.video_id = r.first_clip
                     LEFT JOIN LATERAL (
                SELECT s.shot_index, k.kf_index
                FROM scenes s
                         JOIN keyframes k ON k.scene_id = s.scene_id
                WHERE s.video_id = r.first_clip
                ORDER BY s.shot_index, abs(k.ts_ms - (s.start_ms + s.end_ms) / 2)
                LIMIT 1) cover ON TRUE
            ORDER BY r.recording_id;
        `;

        const [countRes, videosRes] = await Promise.all([
            pool.query(countSql, [collection]),
            pool.query(videosSql, [perPage, offset, collection])
        ]);

        const videos = videosRes.rows.map((row) => ({
            video_id: row.video_id,
            recording_id: row.recording_id,
            num_clips: row.num_clips,
            fps: row.fps,
            duration_sec: Math.round((row.duration_ms || 0) / 1000),
            width: row.width,
            height: row.height,
            damaged: row.damaged,
            num_scenes: row.num_scenes,
            thumbnail_url: row.shot_index === null
                ? null
                : media.thumbnailUrl(row.video_id, row.shot_index, row.kf_index),
            video_url: media.videoUrl(row.video_id)
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

    /**
     * Scenes of a video's whole recording, with their keyframes -- the filmstrip.
     *
     * For an MVK clip that is every clip of the dive, in order; every other video is its own
     * recording. Each scene names its clip, fps and video_url, because selecting it selects that
     * clip. fps, duration_sec and video_url at the top level are the requested clip's.
     */
    getVideoScenes: async (videoId, page = null, perPage = null) => {
        const usePagination = page !== null && perPage !== null;
        const offset = usePagination ? (page - 1) * perPage : null;

        const clipsSql = `
            SELECT v.video_id, v.recording_id, v.fps, v.duration_ms, t.offset_ms
            FROM videos v
                     JOIN video_timeline t USING (video_id)
            WHERE v.recording_id = (SELECT recording_id FROM videos WHERE video_id = $1)
            ORDER BY v.video_id;
        `;
        const scenesSql = `
            WITH page AS (SELECT s.scene_id,
                                 s.video_id,
                                 s.shot_index,
                                 s.start_frame,
                                 s.end_frame,
                                 s.start_ms,
                                 s.end_ms
                          FROM scenes s
                          WHERE s.video_id = ANY ($1)
                          ORDER BY s.video_id, s.shot_index
                              ${usePagination ? 'LIMIT $2 OFFSET $3' : ''})
            SELECT p.scene_id,
                   p.video_id,
                   p.shot_index,
                   p.start_frame,
                   p.end_frame,
                   p.start_ms,
                   p.end_ms,
                   k.keyframe_id,
                   k.kf_index,
                   k.frame_number,
                   k.ts_ms
            FROM page p
                     LEFT JOIN keyframes k ON k.scene_id = p.scene_id
            ORDER BY p.video_id, p.shot_index, k.ts_ms;
        `;

        const clipRows = (await pool.query(clipsSql, [videoId])).rows;
        const clipIds = clipRows.map((clip) => clip.video_id);
        const [scenesRes, countRes] = await Promise.all([
            pool.query(scenesSql, usePagination ? [clipIds, perPage, offset] : [clipIds]),
            pool.query(`SELECT COUNT(*)::int AS total FROM scenes WHERE video_id = ANY ($1);`, [clipIds])
        ]);

        const clips = clipRows.map((clip) => ({
            video_id: clip.video_id,
            fps: clip.fps ?? 25.0,
            duration_sec: Math.round((clip.duration_ms || 0) / 1000),
            recording_offset_ms: clip.offset_ms,
            video_url: media.videoUrl(clip.video_id)
        }));
        const clipById = new Map(clips.map((clip) => [clip.video_id, clip]));
        const requested = clipById.get(videoId) || {fps: 25.0, duration_sec: 0};

        // Collapse the scene/keyframe join into one entry per scene.
        const byScene = new Map();
        for (const row of scenesRes.rows) {
            if (!byScene.has(row.scene_id)) {
                const clip = clipById.get(row.video_id);
                byScene.set(row.scene_id, {
                    scene_id: row.scene_id,
                    video_id: row.video_id,
                    shot_index: row.shot_index,
                    start_frame: row.start_frame,
                    end_frame: row.end_frame,
                    start_time_ms: row.start_ms,
                    end_time_ms: row.end_ms,
                    fps: clip.fps,
                    recording_offset_ms: clip.recording_offset_ms,
                    video_url: clip.video_url,
                    keyframes: []
                });
            }
            if (row.keyframe_id !== null) {
                byScene.get(row.scene_id).keyframes.push(
                    toKeyframe(row.video_id, row.shot_index, row)
                );
            }
        }

        const scenes = [...byScene.values()].map((scene) => ({
            ...scene,
            thumbnail_url:
                scene.keyframes[Math.floor(scene.keyframes.length / 2)]?.thumbnail_url || null
        }));

        return {
            total: countRes.rows[0].total || 0,
            recording_id: clipRows[0]?.recording_id ?? videoId,
            fps: requested.fps,
            duration_sec: requested.duration_sec,
            video_url: media.videoUrl(videoId),
            clips,
            scenes
        };
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
