const queries = require('../models/queries');

exports.searchVideos = async (req, res) => {
    try {
        const { q } = req.query;
        const page = Math.max(parseInt(req.query.page || '1', 10), 1);
        const perPage = Math.min(Math.max(parseInt(req.query.per_page || '24', 10), 1), 100);

        if (!q) {
            return res.status(400).json({ error: "Query parameter 'q' is required" });
        }

        console.log(`Searching for ${q} page=${page} per_page=${perPage}`)
        
        // Parse exclude parameter from query
        const excludeMatch = q.match(/--exclude:\s*([^]*?)(?:\s*$)/);
        let exclude = [];
        if (excludeMatch) {
            exclude = excludeMatch[1].split(',').map(id => id.trim()).filter(id => id);
        }

        const topK = page * perPage;
        const allResults = await queries.searchByText(q, exclude, topK);
        const startIndex = (page - 1) * perPage;
        const pageResults = allResults.slice(startIndex, startIndex + perPage);
        const hasMore = allResults.length === topK;

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
        res.status(500).json({ error: "Internal Server Error" });
    }
};