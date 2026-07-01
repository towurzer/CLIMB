const express = require('express');
const cors = require('cors');
require('dotenv').config();

const searchRoutes = require('./routes/search.routes');
const videoRoutes = require('./routes/video.routes');
const dresRoutes = require('./routes/dres.routes');

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

app.use('/climb/search', searchRoutes);
app.use('/climb/videos', videoRoutes);
app.use('/climb/dres', dresRoutes);

app.use('/keyframes', express.static('../dataset/keyframes'));
app.use('/videos', express.static('../dataset/web_ready'));

app.listen(PORT, () => {
    console.log(`Video Retrieval API running on http://localhost:${PORT}`);
});