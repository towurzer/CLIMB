const express = require('express');
const cors = require('cors');

const searchRoutes = require('./routes/search.routes');
const videoRoutes = require('./routes/video.routes');
const dresRoutes = require('./routes/dres.routes');

const app = express();
const PORT = process.env.PORT || 8000;

app.use(cors({
    origin: ['http://localhost:3000', 'http://localhost:5173'],
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