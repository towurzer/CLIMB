const express = require('express');
const router = express.Router();
const videosController = require('../controller/video.controller');

router.get('/', videosController.listVideos);
router.get('/:video_id', videosController.getVideoInfo);
router.get('/:video_id/scenes', videosController.getVideoScenes);

module.exports = router;