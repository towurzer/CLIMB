const { Pool } = require('pg');
const pgvector = require('pgvector/pg');
const axios = require('axios');


const pool = new Pool({
    user: 'postgres',
    password: 'superSecretPassword23', // config.db_password
    host: 'localhost',            // config.DB_HOST
    port: 5432,                   // config.DB_PORT
    database: 'CLIMB_DB'            // config.db_name
});
const BACKEND_URL = "http://localhost:8000";

pool.on('connect', async (client) => {
    await pgvector.registerType(client);
});

module.exports = {
    searchByText: async (queryText) => {
        try {
            const res = await axios.post('http://localhost:5000/api/search', {
                prompt: queryText
            });

            return res.data.results.map(shot => ({
                video_id: shot.video_id,
                shot_id: shot.shot_id,
                score: shot.similarity_score,
                start_frame: shot.start_frame,
                end_frame: shot.end_frame,
                fps: shot.fps,
                start_time_ms: shot.start_frame_time_ms,
                end_time_ms: shot.end_frame_time_ms,
                // Make sure this points to your Express static folder path!
                thumbnail_url: `${BACKEND_URL}/keyframes/${shot.image_path.split('/').pop()}`
            }));


        } catch (error) {
            console.error("Failed to connect to Python Worker");
            throw error;
        }
    },

    getAllVideos: async () => {
        const sql = `
            SELECT
                v.video_id,
                v.fps,
                COUNT(s.shot_id)::integer AS num_shots
            FROM videos v
                     LEFT JOIN shots s ON v.video_id = s.video_id
            GROUP BY v.video_id
            ORDER BY v.video_id;
        `;
        const { rows } = await pool.query(sql);

        return rows.map(row => ({
            video_id: row.video_id,
            fps: row.fps,
            duration_sec: 0,
            num_shots: row.num_shots,
            thumbnail_url: `${BACKEND_URL}/keyframes/${row.video_id}_shot_0000.jpg`
        }));
    },

    getVideoDetails: async (videoId) => {
        const sql = `SELECT video_id, fps FROM videos WHERE video_id = $1`;
        const { rows } = await pool.query(sql, [videoId]);

        if (rows.length === 0) return null;

        return {
            video_id: rows[0].video_id,
            fps: rows[0].fps,
            duration_sec: 0,
            width: 1280,
            height: 720,
            video_url: `${BACKEND_URL}/videos/${rows[0].video_id}.mp4`

        };
    },

    getVideoShots: async (videoId) => {
        const sql = `
            SELECT shot_id, start_frame, end_frame, image_path
            FROM shots
            WHERE video_id = $1
            ORDER BY start_frame;
        `;
        const fpsSql = `SELECT fps FROM videos WHERE video_id = $1`;

        const [shotsRes, fpsRes] = await Promise.all([
            pool.query(sql, [videoId]),
            pool.query(fpsSql, [videoId])
        ]);

        const fps = fpsRes.rows.length > 0 ? fpsRes.rows[0].fps : 25.0;

        return shotsRes.rows.map(row => ({
            shot_id: row.shot_id,
            start_frame: row.start_frame,
            end_frame: row.end_frame,
            fps: fps,
            thumbnail_url: `${BACKEND_URL}/keyframes/${row.image_path.split('/').pop()}`
        }));
    },

    getSimilarShots: async (videoId, shotId) => {
        const getEmbedSql = `SELECT embedding FROM shots WHERE video_id = $1 AND shot_id = $2`;
        const embedRes = await pool.query(getEmbedSql, [videoId, shotId]);

        if (embedRes.rows.length === 0) throw new Error("Shot not found");

        const targetVectorString = embedRes.rows[0].embedding;

        if (!targetVectorString) {
            return [];
        }
        const searchSql = `
            SELECT
                s.shot_id, s.video_id, s.start_frame, s.end_frame, s.image_path, v.fps,
                1 - (s.embedding <=> $1::vector) AS score
            FROM shots s
                     JOIN videos v ON s.video_id = v.video_id
            WHERE s.shot_id != $2
              AND s.embedding IS NOT NULL
            ORDER BY s.embedding <=> $1::vector
                LIMIT 50;
        `;

        const { rows } = await pool.query(searchSql, [targetVectorString, shotId]);

        return rows.map(row => ({
            video_id: row.video_id,
            shot_id: row.shot_id,
            score: row.score ? parseFloat(row.score.toFixed(4)) : 0,
            start_frame: row.start_frame,
            end_frame: row.end_frame,
            fps: row.fps,
            start_time_ms: Math.floor((row.start_frame / row.fps) * 1000),
            end_time_ms: Math.floor((row.end_frame / row.fps) * 1000),
            thumbnail_url: `${BACKEND_URL}/keyframes/${row.image_path.split('/').pop()}`
        }));
    },

    askVQA: async (videoId, shotId, question) => {
        // TODO: Add SQL query here to get the image_path for this videoId/shotId to send to Python
        const res = await axios.post('http://localhost:5000/api/vqa', {
            image_path: `/path/to/v3c_keyframes/${videoId}_shot_${shotId}.jpg`, // TODO: Update with real path
            question: question
        });

        return res.data.answer;
    }
};