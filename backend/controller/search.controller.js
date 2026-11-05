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
// v5 adds filters (each text:/said:/tag:/video: requirement and how many videos met it)
// v6 adds widen (the next depth of a sequence's background search)
const CACHE_VERSION = 'v6';

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

function paginate(results, page, perPage, offset = null) {
    const start = offset ?? (page - 1) * perPage;
    return {
        page: results.slice(start, start + perPage),
        hasMore: results.length > start + perPage
    };
}

// Which collection to search: V3C, MVK or LHE. Absent or empty means all of them.
const parseCollection = (req) => (req.query.collection || '').trim().toUpperCase() || null;

// `offset` wins over `page`: a sequence's results can grow after a short last page, and the next
// page number would then skip what landed right behind it.
const parsePaging = (req) => {
    const offset = parseInt(req.query.offset, 10);
    return {
        page: Math.max(parseInt(req.query.page || '1', 10), 1),
        perPage: Math.min(Math.max(parseInt(req.query.per_page || '24', 10), 1), 100),
        offset: Number.isInteger(offset) && offset >= 0 ? offset : null
    };
};

// What a text search is cached under, and what its background search has to repeat. Sources are
// sorted because the same query against a different set of sources is a different result set, not
// a cache hit.
const parseTextSearch = (req) => ({
    exclude: (req.query.exclude || '').split(',').map((v) => v.trim()).filter(Boolean),
    sources: (req.query.sources || '').split(',').map((v) => v.trim().toLowerCase())
        .filter(Boolean).sort(),
    collection: parseCollection(req)
});

exports.searchVideos = async (req, res) => {
    try {
        const {q, avs_session} = req.query;
        if (!q) return res.status(400).json({error: "Query parameter 'q' is required"});

        const {page, perPage, offset} = parsePaging(req);
        // Which sources to search. Absent means all of them, which is also what the UI sends when
        // every checkbox is ticked.
        const {exclude, sources, collection} = parseTextSearch(req);

        const {payload, cached} = await deepResults('text', [q, exclude, sources, collection], () =>
            queries.searchByText(q, exclude, RESULT_DEPTH, sources, collection));

        // Anchors, for a sequence query -
        const filtered = applyAvsFilter(payload.results, avs_session);
        const {page: pageResults, hasMore} = paginate(filtered, page, perPage, offset);

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
            filters: payload.filters,
            // What a background search adds is paged from the cache, so without Redis there is
            // nowhere to keep it and none runs.
            widen: cache.isAvailable() ? payload.widen ?? null : null,
            collection,
            results: pageResults
        });
    } catch (error) {
        console.error(error);
        res.status(500).json({error: 'Internal Server Error'});
    }
};

/**
 * One rung of a sequence's background search, appended to the cached result set. Everything already
 * in it keeps its position, so no card moves under the operator. The frontend asks again until
 * next_depth is null, and stops asking when a new query starts.
 */
exports.widenSearch = async (req, res) => {
    try {
        const {q, avs_session, client} = req.query;
        if (!q) return res.status(400).json({error: "Query parameter 'q' is required"});

        const {exclude, sources, collection} = parseTextSearch(req);
        const key = cacheKey('text', [q, exclude, sources, collection]);
        const before = await cache.get(key);
        const depth = before?.widen?.next_depth ?? null;
        if (depth === null) {
            const total = before ? applyAvsFilter(before.results, avs_session).length : 0;
            return res.status(200).json({added: 0, total, depth: null, next_depth: null});
        }

        const rung = await queries.widenByText(q, depth, exclude, sources, collection,
            before.results.map((r) => [r.recording_id, r.keyframe_id]), client || '');
        if (rung.cancelled) {
            return res.status(200).json({added: 0, cancelled: true, depth, next_depth: null});
        }

        // Read again: a second tab on the same query may have appended while the worker was busy.
        const payload = (await cache.get(key)) || before;
        const shown = new Set(payload.results.map((r) => r.keyframe_id));
        const fresh = rung.results.filter((r) => !shown.has(r.keyframe_id));
        payload.results.push(...fresh);
        payload.widen = {depth, next_depth: rung.next_depth};
        await cache.set(key, payload, CACHE_TTL_SECONDS);

        console.log(`widen q=${JSON.stringify(q)} depth=${depth} -> +${fresh.length}/${payload.results.length}`);
        res.status(200).json({
            added: fresh.length,
            total: applyAvsFilter(payload.results, avs_session).length,
            depth,
            next_depth: rung.next_depth
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

        const collection = parseCollection(req);
        const {payload, cached} = await deepResults('similar', [keyframeId, exclude, collection], () =>
            queries.findSimilarByKeyframe(keyframeId, exclude, RESULT_DEPTH, collection));

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
