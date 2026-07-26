const queries = require('../models/queries');
const avsStore = require('../avsSessionStore');

exports.searchVideos = async (req, res) => {
    try {
        const {q, avs_session} = req.query;
        const page = Math.max(parseInt(req.query.page || '1', 10), 1);
        const perPage = Math.min(Math.max(parseInt(req.query.per_page || '24', 10), 1), 100);

        if (!q) {
            return res.status(400).json({error: "Query parameter 'q' is required"});
        }

        console.log(`Searching for ${q} page=${page} per_page=${perPage}`)

        // Parse exclude parameter from query
        const excludeMatch = q.match(/--exclude:\s*([^]*?)(?:\s*$)/);
        let exclude = [];
        if (excludeMatch) {
            exclude = excludeMatch[1].split(',').map(id => id.trim()).filter(id => id);
        }

        const topK = page * perPage;
        const rawResults = await queries.searchByText(q, exclude, topK);
        const hasMore = rawResults.length === topK;

        // Hide scenes already submitted in the caller's AVS session. getSession also refreshes the session's idle timer.
        const session = avs_session ? avsStore.getSession(String(avs_session).toUpperCase()) : null;
        const allResults = session
            ? rawResults.filter(r => {
                const scene = session.scenes.get(avsStore.sceneKey(r.video_id, r.start_frame, r.end_frame));
                return !(scene && avsStore.holdsVideo(scene.status));
            })
            : rawResults;

        const startIndex = (page - 1) * perPage;
        const pageResults = allResults.slice(startIndex, startIndex + perPage);

        res.status(200).json({
            query: q,
            page,
            per_page: perPage,
            count: pageResults.length,
            has_more: hasMore,
            results: pageResults
        });
    } catch (error) {
        console.error(error);
        res.status(500).json({error: "Internal Server Error"});
    }
};