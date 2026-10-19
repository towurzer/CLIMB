/**
 * Finds source videos on disk, so they can be played as they are.
 *
 * The layout is /datasets.json, shared with video_processing/src/pipeline/datasets.py and
 * resolved the same way: CLIMB_<NAME>_VIDEOS overrides an entry, and relative paths sit under
 * CLIMB_SOURCE_ROOT.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const REGISTRY = require('../datasets.json');
const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mkv', '.avi', '.mov'];
// V3C numbers, MVK clip names, LHE names. Anything else is not a video id.
const VIDEO_ID = /^[A-Za-z0-9_-]+$/;

const expandHome = (value) => value.startsWith('~') ? path.join(os.homedir(), value.slice(1)) : value;

const resolveDir = (value) => {
    const dir = expandHome(String(value));
    if (path.isAbsolute(dir)) return dir;
    const root = process.env.CLIMB_SOURCE_ROOT;
    return root ? path.join(expandHome(root), dir) : null;
};

const configuredVideoDirs = () => Object.entries(REGISTRY)
    .map(([name, entry]) => [name, resolveDir(process.env[`CLIMB_${name}_VIDEOS`] || entry.videos)]);

const videoDirs = () => configuredVideoDirs().map(([, dir]) => dir).filter(Boolean);

/** One line per collection whose video folder cannot be read; empty when all are there. */
const unreachableVideoDirs = () => configuredVideoDirs()
    .filter(([, dir]) => !dir || !fs.existsSync(dir))
    .map(([name, dir]) => dir
        ? `${name}: ${dir} does not exist (is the drive mounted?)`
        : `${name}: CLIMB_SOURCE_ROOT is not set`);

// Hits only. A miss is not cached, so a drive mounted after the backend started is still found.
const found = new Map();

/** Absolute path of a video's source file, or null. */
const sourceVideoPath = (videoId) => {
    if (!VIDEO_ID.test(videoId)) return null;
    if (found.has(videoId)) return found.get(videoId);
    for (const dir of videoDirs()) {
        for (const ext of VIDEO_EXTENSIONS) {
            const candidate = path.join(dir, videoId + ext);
            if (fs.existsSync(candidate)) {
                found.set(videoId, candidate);
                return candidate;
            }
        }
    }
    return null;
};

module.exports = {sourceVideoPath, videoDirs, unreachableVideoDirs};
