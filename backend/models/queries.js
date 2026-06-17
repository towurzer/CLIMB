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

pool.on('connect', async (client) => {
    await pgvector.registerType(client);
});

async function getTextEmbedding(text) {
    try {
        // TODO change random vector to real vector
        return Array.from({ length: 1024 }, () => Math.random() - 0.5);
    } catch (error) {
        console.error("Failed to fetch embedding from AI worker:", error.message);
        throw error;
    }
}

module.exports = {
    searchByText: async (queryText) => {
        const queryVector = await getTextEmbedding(queryText);
        const vectorString = pgvector.toSql(queryVector);

        // Perform Cosine Similarity Search
        const sql = `
            SELECT 
                s.shot_id, 
                s.video_id, 
                s.start_frame, 
                s.end_frame, 
                s.image_path,
                v.fps,
                1 - (s.embedding <=> $1::vector) AS score
            FROM shots s
            JOIN videos v ON s.video_id = v.video_id
            ORDER BY s.embedding <=> $1::vector
            LIMIT 50;
        `;

        const { rows } = await pool.query(sql, [vectorString]);

        return rows.map(row => ({
            video_id: row.video_id,
            shot_id: row.shot_id,
            score: parseFloat(row.score.toFixed(4)),
            start_frame: row.start_frame,
            end_frame: row.end_frame,
            fps: row.fps,
            start_time_ms: Math.floor((row.start_frame / row.fps) * 1000),
            end_time_ms: Math.floor((row.end_frame / row.fps) * 1000),
            // Assuming image_path is saved like '/data/keyframes/xyz.jpg'
            thumbnail_url: `/keyframes/${row.image_path.split('/').pop()}`
        }));
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
            thumbnail_url: `/keyframes/${row.video_id}_shot_0000.jpg`
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
            video_url: `/videos/${rows[0].video_id}.mp4`
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
            thumbnail_url: `/keyframes/${row.image_path.split('/').pop()}`
        }));
    },

    getSimilarShots: async (videoId, shotId) => {
        const getEmbedSql = `SELECT embedding FROM shots WHERE video_id = $1 AND shot_id = $2`;
        const embedRes = await pool.query(getEmbedSql, [videoId, shotId]);

        if (embedRes.rows.length === 0) throw new Error("Shot not found");

        const targetVectorString = embedRes.rows[0].embedding; // Already a pgvector format

        const searchSql = `
            SELECT
                s.shot_id, s.video_id, s.start_frame, s.end_frame, s.image_path, v.fps,
                1 - (s.embedding <=> $1::vector) AS score
            FROM shots s
                     JOIN videos v ON s.video_id = v.video_id
            WHERE s.shot_id != $2 -- Don't return the exact image they clicked on
            ORDER BY s.embedding <=> $1::vector
                LIMIT 50;
        `;

        const { rows } = await pool.query(searchSql, [targetVectorString, shotId]);

        return rows.map(row => ({
            video_id: row.video_id,
            shot_id: row.shot_id,
            score: parseFloat(row.score.toFixed(4)),
            start_frame: row.start_frame,
            end_frame: row.end_frame,
            fps: row.fps,
            start_time_ms: Math.floor((row.start_frame / row.fps) * 1000),
            end_time_ms: Math.floor((row.end_frame / row.fps) * 1000),
            thumbnail_url: `/keyframes/${row.image_path.split('/').pop()}`
        }));
    },

    askVQA: async (videoId, shotId, question) => {
        const sql = `SELECT image_path FROM shots WHERE video_id = $1 AND shot_id = $2`;
        const { rows } = await pool.query(sql, [videoId, shotId]);

        if (rows.length === 0) return "Error: Shot not found.";
        const imagePath = rows[0].image_path;

        // TODO: add ai model to ask

        return `You Idiot didnt add an AI model to answer that question for you?`;
    }
};