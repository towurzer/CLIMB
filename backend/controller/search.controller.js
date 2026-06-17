const queries = require('../models/queries');

exports.searchVideos = async (req, res) => {
    try {
        const { q } = req.query;
        if (!q) {
            return res.status(400).json({ error: "Query parameter 'q' is required" });
        }

        const results = await queries.searchByText(q);

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