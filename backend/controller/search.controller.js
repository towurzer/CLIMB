const crypto = require('crypto');

const queries = require('../models/queries');
const avsClient = require('../avsSessionClient');
const cache = require('../cache');

// How deep a result set is fetched and cached. Paging then costs a Redis read instead of another search.
// 500 scenes is ~10 pages of 48
const RESULT_DEPTH = parseInt(process.env.SEARCH_RESULT_DEPTH || '500', 10);
const CACHE_TTL_SECONDS = parseInt(process.env.SEARCH_CACHE_TTL_SECONDS || '300', 10);

// Bump when the cached payload's shape changes. v2 caches {results, temporal}
// v3 adds video_url to every result;
// v4 adds contributions (per-signal share of the fused score) 
const CACHE_VERSION = 'v4';

const cacheKey = (kind, parts) =>
    `search:${CACHE_VERSION}:${kind}:${crypto.createHash('sha1').update(JSON.stringify(parts)).digest('hex')}`;

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
    if (cached) return {payload: cached, cached: true};

    const payload = await fetcher();
    await cache.set(key, payload, CACHE_TTL_SECONDS);
    return {payload, cached: false};
}

/**
 * Drop scenes already submitted in this AVS session, so nobody submits the same shot twice.
 *
 * Reads the local mirror and nothing else, meaning this is synchronous,
 * no network, no awaiting, the mirror gets pulled not asked every query.
 */
function applyAvsFilter(results, avsSession) {
    if (!avsSession) return results;
    const session = avsClient.getMirror(avsSession);
    if (!session) return results;
    return results.filter((r) => {
        const scene = session.scenes.get(avsClient.sceneKey(r.scene_id));
        return !(scene && avsClient.holdsVideo(scene.status));
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
        // Which sources to search. Absent means all of them, which is also what the UI sends when
        // every checkbox is ticked. Sorted into the cache key, because the same query against a
        // different set of sources is a different result set, not a cache hit.
        const sources = (req.query.sources || '').split(',').map((v) => v.trim().toLowerCase())
            .filter(Boolean).sort();

        const {payload, cached} = await deepResults('text', [q, exclude, sources], () =>
            queries.searchByText(q, exclude, RESULT_DEPTH, sources));

        // Anchors, for a sequence query -
        const filtered = applyAvsFilter(payload.results, avs_session);
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
            temporal: payload.temporal,
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

        const {payload, cached} = await deepResults('similar', [keyframeId, exclude], () =>
            queries.findSimilarByKeyframe(keyframeId, exclude, RESULT_DEPTH));

        const filtered = applyAvsFilter(payload.results, req.query.avs_session);
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
