const path = require('path');
require('dotenv').config({path: path.resolve(__dirname, '../.env')});

const resolveUrl = (host, port) =>
    host.startsWith('http://') || host.startsWith('https://')
        ? host
        : `http://${host}:${port}`;

module.exports = {
    resolveUrl,
    BACKEND_URL: resolveUrl(
        process.env.BACKEND_URL || 'localhost',
        process.env.BACKEND_PORT || 8000
    ),
    SEARCH_ENGINE_URL: resolveUrl(
        process.env.SEARCH_ENGINE_URL || 'localhost',
        process.env.SEARCH_ENGINE_PORT || 5000
    )
};
