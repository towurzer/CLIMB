const express = require('express');
const router = express.Router();
const searchController = require('../controller/search.controller');

router.get('/', searchController.searchVideos);

module.exports = router;