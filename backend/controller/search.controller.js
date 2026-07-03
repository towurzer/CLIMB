const queries = require('../models/queries');

exports.searchVideos = async (req, res) => {
    try {
        const { q } = req.query;
        if (!q) {
            return res.status(400).json({ error: "Query parameter 'q' is required" });
        }

        console.log(`Searching for ${q}`)
        
        // Parse exclude parameter from query
        const excludeMatch = q.match(/--exclude:\s*([^]*?)(?:\s*$)/);
        let exclude = [];
        if (excludeMatch) {
            // Extract video IDs from exclude parameter
            exclude = excludeMatch[1].split(',').map(id => id.trim()).filter(id => id);
        }
        
        const results = await queries.searchByText(q, exclude);

        res.status(200).json({
            query: q,
            count: results.length,
            results: results
        });
    } catch (error) {
        console.error(error);
        res.status(500).json({ error: "Internal Server Error" });
    }
};