/**
 * One Redis connection for the whole backend.
 *
 * Previously video.controller.js opened its own and search had none at all, which is why paging a
 * search re-ran the entire search on every "load more".
 *
 * Redis is optional throughout. If it is not running, get() misses and set() is a no-op, and
 * everything still works, just slower.
 */

const {createClient} = require('redis');

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const CONNECT_TIMEOUT_MS = 3000;

const client = createClient({
    url: REDIS_URL,
    socket: {connectTimeout: CONNECT_TIMEOUT_MS, reconnectStrategy: false}
});

// Without a handler an ECONNREFUSED here is an unhandled 'error' event, which takes the process
// down -- losing the backend because the optional cache is missing.
client.on('error', () => {
});

let available = false;

(async () => {
    try {
        await Promise.race([
            client.connect(),
            new Promise((_, reject) =>
                setTimeout(() => reject(new Error('Redis connect timeout')), CONNECT_TIMEOUT_MS))
        ]);
        available = true;
        console.log('Connected to Redis');
    } catch (err) {
        console.log('Redis not available, continuing without caching');
    }
})();

const isAvailable = () => available && client.isOpen;

async function get(key) {
    if (!isAvailable()) return null;
    try {
        const raw = await client.get(key);
        return raw ? JSON.parse(raw) : null;
    } catch (err) {
        console.warn('Redis get failed:', err.message || err);
        return null;
    }
}

async function set(key, value, ttlSeconds) {
    if (!isAvailable()) return false;
    try {
        await client.setEx(key, ttlSeconds, JSON.stringify(value));
        return true;
    } catch (err) {
        console.warn('Redis set failed:', err.message || err);
        return false;
    }
}

module.exports = {get, set, isAvailable};
