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
// Between reconnect attempts, growing to the cap. Without retrying, a backend started a moment
// before the Redis container ran uncached until it was restarted.
const RETRY_MS = 500;
const MAX_RETRY_MS = 10000;

const client = createClient({
    url: REDIS_URL,
    // Fail a command at once while disconnected instead of queueing it until Redis is back, which
    // would hold up the search waiting on it.
    disableOfflineQueue: true,
    socket: {
        connectTimeout: CONNECT_TIMEOUT_MS,
        reconnectStrategy: (retries) => Math.min((retries + 1) * RETRY_MS, MAX_RETRY_MS)
    }
});

// Without a handler an ECONNREFUSED here is an unhandled 'error' event, which takes the process
// down -- losing the backend because the optional cache is missing. While Redis is away this fires
// on every retry, so it only logs the first one.
let reported = false;
client.on('error', () => {
    if (reported) return;
    reported = true;
    console.log('Redis not available, continuing without caching (retrying in the background)');
});
client.on('ready', () => {
    reported = false;
    console.log('Connected to Redis');
});

client.connect().catch(() => {
});

const isAvailable = () => client.isReady;

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
