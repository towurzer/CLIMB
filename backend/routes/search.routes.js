const express = require('express');
const router = express.Router();
const searchController = require('../controller/search.controller');

router.get('/', searchController.searchVideos);

router.get('/widen', searchController.widenSearch);

router.get('/similar/:keyframe_id', searchController.findSimilar);

module.exports = router;