/**
 * URLs for the media the pipeline produces.
 *
 * These mirror video_processing/src/pipeline/paths.py. Paths are derived from
 * (video_id, shot_index, kf_index) rather than stored.
 *
 * Important: If the layout changes in paths.py it has to change here too.
 */

const SHARD_WIDTH = 2;
const {BACKEND_URL} = require('./serviceUrls');

/** Directory shard for a video id: V3C runs 00001..28450, so ~29 dirs of ~1000 videos. */
const shardOf = (videoId) => String(videoId).slice(0, SHARD_WIDTH).padStart(SHARD_WIDTH, '0');

const keyframeName = (shotIndex, kfIndex) =>
    `${String(shotIndex).padStart(5, '0')}_${kfIndex}.webp`;

/** 160px tile for the results grid. */
const thumbnailUrl = (videoId, shotIndex, kfIndex) =>
    `${BACKEND_URL}/thumbs/${shardOf(videoId)}/${videoId}/${keyframeName(shotIndex, kfIndex)}`;

/** 384px still for the detail panel. */
const keyframeUrl = (videoId, shotIndex, kfIndex) =>
    `${BACKEND_URL}/kf/${shardOf(videoId)}/${videoId}/${keyframeName(shotIndex, kfIndex)}`;

/** 360p web-playable transcode. */
const videoUrl = (videoId) => `${BACKEND_URL}/videos/${shardOf(videoId)}/${videoId}.mp4`;

module.exports = {shardOf, keyframeName, thumbnailUrl, keyframeUrl, videoUrl};
