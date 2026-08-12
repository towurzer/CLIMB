const express = require('express');
const cors = require('cors');
const axios = require('axios');
const path = require('path');
require('dotenv').config();

const {SEARCH_ENGINE_URL} = require('./serviceUrls');
const avsClient = require('./avsSessionClient');

const searchRoutes = require('./routes/search.routes');
const videoRoutes = require('./routes/video.routes');
const dresRoutes = require('./routes/dres.routes');
const avsRoutes = require('./routes/avs.routes');

const app = express();
const PORT = process.env.BACKEND_PORT || 8000;
const FRONTEND_PORT = process.env.FRONTEND_PORT || 3000;
const SEARCH_ENGINE_PORT = process.env.SEARCH_ENGINE_PORT || 5000;
const DB_PORT = process.env.DB_PORT || 5432;

const allowedOriginRegex = process.env.ALLOWED_ORIGIN_REGEX
    ? new RegExp(process.env.ALLOWED_ORIGIN_REGEX)
    : /^http:\/\/localhost:8000$/;


const allowedOrigins = [
    `http://localhost:${FRONTEND_PORT}`,
    `http://localhost:${SEARCH_ENGINE_PORT}`,
    `http://localhost:${DB_PORT}`,
    allowedOriginRegex
];

app.use(cors({
    origin: allowedOrigins,
    credentials: true
}));
app.use(express.json());

const EMBEDDING_PROBE_TIMEOUT_MS = 2000;

const probeEmbeddingService = async () => {
    try {
        const res = await axios.get(`${SEARCH_ENGINE_URL}/api/health`, {timeout: EMBEDDING_PROBE_TIMEOUT_MS});
        return {
            reachable: true,
            ready: Boolean(res.data?.search_engine_ready),
            detail: res.data?.search_engine_ready
                ? "Embedding service ready"
                : "Embedding service running but its search engine failed to initialize"
        };
    } catch (err) {
        const reason = err.code || err.message || (err.response && `HTTP ${err.response.status}`) || "no response";
        return {
            reachable: false,
            ready: false,
            detail: `Embedding service unreachable: ${reason}`
        };
    }
};

app.get('/climb/health', async (req, res) => {
    const [embedding, collab] = await Promise.all([
        probeEmbeddingService(),
        avsClient.health()
    ]);
    const status = !embedding.ready
        ? "degraded"
        : (collab.configured && !collab.reachable ? "collab_offline" : "ok");

    res.status(200).json({
        status,
        backend: "ok",
        embedding_service: embedding,
        avs_collab: collab,
        uptime_seconds: Math.floor(process.uptime()),
        time: new Date().toISOString()
    });
});

app.use('/climb/search', searchRoutes);
app.use('/climb/videos', videoRoutes);
app.use('/climb/dres', dresRoutes);
app.use('/climb/avs', avsRoutes);

// Media roots. CLIMB_MEDIA_DIR is where the pipeline writes; at full V3C scale that is the
// external SSD rather than anywhere inside the repo.
const MEDIA_DIR = process.env.CLIMB_MEDIA_DIR || path.resolve(__dirname, '../dataset/media');

// Keyframes and thumbnails never change once written
const immutable = {maxAge: '365d', immutable: true};

app.use('/thumbs', express.static(path.join(MEDIA_DIR, 'thumbs'), immutable));
app.use('/kf', express.static(path.join(MEDIA_DIR, 'kf'), immutable));
app.use('/videos', express.static(path.join(MEDIA_DIR, 'video'), {maxAge: '365d'}));

app.listen(PORT, () => {
    console.log(`Video Retrieval API running on http://localhost:${PORT}`);
});