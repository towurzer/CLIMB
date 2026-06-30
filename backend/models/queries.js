const { Pool } = require('pg');
const pgvector = require('pgvector/pg');
const axios = require('axios');
const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '../../.env') });


const pool = new Pool({
    user: process.env.POSTGRES_USER || 'postgres',
    password: process.env.POSTGRES_PASSWORD,
    host: process.env.DB_HOST || 'localhost',
    port: process.env.DB_PORT || 5432,
    database: process.env.POSTGRES_DB_NAME || 'climb_db'
});
const BACKEND_URL = "http://localhost:8000";

async function initDatabase() {
    try {
        const client = await pool.connect();
        await pgvector.registerType(client);
        console.log("Successfully registered pgvector type.");
        client.release();
    }
    catch (err) {
        console.error("Failed to connect to PostgreSQL:", err);
    }
}
initDatabase();

pool.on('connect', async (client) => {
    try {
        await pgvector.registerType(client);
    } catch (err) {
        console.error("Failed to register pgvector type:", err);
    }
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
                middle_frame: shot.middle_frame,
                fps: shot.fps,
                start_time_ms: shot.start_frame_time_ms,
                end_time_ms: shot.end_frame_time_ms,
                thumbnail_url: `${BACKEND_URL}/keyframes/${shot.image_path.split('/').pop()}`
            }));
    
        } catch (error) {
            console.error("Failed to connect to Python Worker");
            throw error;
        }
    },


    getAllVideos: async (page = 1, perPage = 20) => {
        const offset = (page - 1) * perPage;

        const countSql = `SELECT COUNT(*)::int AS total FROM videos;`;
        const videosSql = `
            SELECT
                v.video_id,
                v.fps,
                COUNT(s.shot_id)::integer AS num_shots
            FROM videos v
                     LEFT JOIN shots s ON v.video_id = s.video_id
            GROUP BY v.video_id, v.fps
            ORDER BY v.video_id
            LIMIT $1 OFFSET $2;
        `;

        const [countRes, videosRes] = await Promise.all([
            pool.query(countSql),
            pool.query(videosSql, [perPage, offset])
        ]);

        const total = countRes.rows[0].total || 0;

        const videoIds = videosRes.rows.map(r => r.video_id);

        // Fetch preferred keyframes only for returned video ids
        let prefMap = new Map();
        if (videoIds.length > 0) {
            const preferredSql = `
                SELECT DISTINCT ON (video_id) video_id, image_path
                FROM shots
                WHERE image_path LIKE '%_kf_00010.jpg' AND video_id = ANY($1)
                ORDER BY video_id, start_frame ASC;
            `;
            const prefRes = await pool.query(preferredSql, [videoIds]);
            prefMap = new Map(prefRes.rows.map(r => [r.video_id, r.image_path]));
        }

        const videos = videosRes.rows.map(row => ({
            video_id: row.video_id,
            fps: row.fps,
            duration_sec: 0,
            num_shots: row.num_shots,
            thumbnail_url: prefMap.has(row.video_id)
                ? `${BACKEND_URL}/keyframes/${path.basename(prefMap.get(row.video_id))}`
                : `${BACKEND_URL}/keyframes/${row.video_id}_shot_00000_kf_00000.jpg`
        }));

        return { total, videos };
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
        SELECT shot_id, start_frame, end_frame, middle_frame, image_path
        FROM shots
            WHERE video_id = $1
            ORDER BY start_frame ASC, middle_frame ASC;
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
            middle_frame: row.middle_frame,
            fps: fps,
            thumbnail_url: `${BACKEND_URL}/keyframes/${row.image_path.split('/').pop()}`
        }));
    },

    getSimilarShots: async (videoId, shotId) => {
        const getEmbedSql = `SELECT embedding FROM shots WHERE video_id = $1 AND shot_id = $2`;
        const embedRes = await pool.query(getEmbedSql, [videoId, shotId]);

        if (embedRes.rows.length === 0) throw new Error("Shot not found");

        const targetVectorArray = embedRes.rows[0].embedding;

        if (!targetVectorArray) {
            return [];
        }
        const formattedVectorString = pgvector.toSql(targetVectorArray);

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

        const { rows } = await pool.query(searchSql, [formattedVectorString, shotId]);

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
        // Get actual image path from database
        const pathSql = `SELECT image_path FROM shots WHERE shot_id = $1`;
        const pathRes = await pool.query(pathSql, [shotId]);
        
        if (pathRes.rows.length === 0) throw new Error("Shot not found");
        
        const imagePath = pathRes.rows[0].image_path;

        console.log(`Asking python ${imagePath}, ${question}`);
        const res = await axios.post('http://localhost:5000/api/vqa', {
            image_path: imagePath,
            question: question
        });
    
        return res.data.answer;
    }
};