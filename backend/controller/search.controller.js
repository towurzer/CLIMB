const crypto = require('crypto');

const queries = require('../models/queries');
const avsStore = require('../avsSessionStore');
const cache = require('../cache');

// How deep a result set is fetched and cached. Paging then costs a Redis read instead of another search.
// 500 scenes is ~10 pages of 48
const RESULT_DEPTH = parseInt(process.env.SEARCH_RESULT_DEPTH || '500', 10);
const CACHE_TTL_SECONDS = parseInt(process.env.SEARCH_CACHE_TTL_SECONDS || '300', 10);

const cacheKey = (kind, parts) =>
    `search:${kind}:${crypto.createHash('sha1').update(JSON.stringify(parts)).digest('hex')}`;

/**
 * Fetch a deep result set once and reuse it.
 *
 * The AVS session is *not* part of the key. Session membership changes as teammates submit, and
 * baking it into the key would throw the whole cached search away every time somebody else
 * submitted something. Filtering happens after the cache read instead, which is cheap.
 */
async function deepResults(kind, keyParts, fetcher) {
    const key = cacheKey(kind, keyParts);
    const cached = await cache.get(key);
    if (cached) return {results: cached, cached: true};

    const results = await fetcher();
    await cache.set(key, results, CACHE_TTL_SECONDS);
    return {results, cached: false};
}

/** Drop scenes already submitted in this AVS session, so nobody submits the same shot twice. */
function applyAvsFilter(results, avsSession) {
    if (!avsSession) return results;
    const session = avsStore.getSession(String(avsSession).toUpperCase());
    if (!session) return results;
    return results.filter((r) => {
        const scene = session.scenes.get(avsStore.sceneKey(r.scene_id));
        return !(scene && avsStore.holdsVideo(scene.status));
    });
}

function paginate(results, page, perPage) {
    const start = (page - 1) * perPage;
    return {
        page: results.slice(start, start + perPage),
        hasMore: results.length > start + perPage
    };
}

const parsePaging = (req) => ({
    page: Math.max(parseInt(req.query.page || '1', 10), 1),
    perPage: Math.min(Math.max(parseInt(req.query.per_page || '24', 10), 1), 100)
});

exports.searchVideos = async (req, res) => {
    try {
        const {q, avs_session} = req.query;
        if (!q) return res.status(400).json({error: "Query parameter 'q' is required"});

        const {page, perPage} = parsePaging(req);
        const exclude = (req.query.exclude || '').split(',').map((v) => v.trim()).filter(Boolean);

        const {results, cached} = await deepResults('text', [q, exclude], () =>
            queries.searchByText(q, exclude, RESULT_DEPTH));

        const filtered = applyAvsFilter(results, avs_session);
        const {page: pageResults, hasMore} = paginate(filtered, page, perPage);

        console.log(`search q=${JSON.stringify(q)} page=${page} ${cached ? '(cached)' : '(fresh)'} ` +
            `-> ${pageResults.length}/${filtered.length}`);

        res.status(200).json({
            query: q,
            page,
            per_page: perPage,
            count: pageResults.length,
            total: filtered.length,
            has_more: hasMore,
            cached,
            results: pageResults
        });
    } catch (error) {
        console.error(error);
        res.status(500).json({error: 'Internal Server Error'});
    }
};

exports.findSimilar = async (req, res) => {
    try {
        const keyframeId = parseInt(req.params.keyframe_id, 10);
        if (!Number.isInteger(keyframeId)) {
            return res.status(400).json({error: 'keyframe_id must be an integer'});
        }

        const {page, perPage} = parsePaging(req);
        const exclude = (req.query.exclude || '').split(',').map((v) => v.trim()).filter(Boolean);

        const {results, cached} = await deepResults('similar', [keyframeId, exclude], () =>
            queries.findSimilarByKeyframe(keyframeId, exclude, RESULT_DEPTH));

        const filtered = applyAvsFilter(results, req.query.avs_session);
        const {page: pageResults, hasMore} = paginate(filtered, page, perPage);

        res.status(200).json({
            source_keyframe: keyframeId,
            excluded: exclude,
            page,
            per_page: perPage,
            count: pageResults.length,
            total: filtered.length,
            has_more: hasMore,
            cached,
            results: pageResults
        });
    } catch (error) {
        console.error(error);
        res.status(500).json({error: 'Internal Server Error'});
    }
};
