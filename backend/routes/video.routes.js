const express = require('express');
const router = express.Router();
const videosController = require('../controller/video.controller');

router.get('/', videosController.listVideos);
router.get('/:video_id', videosController.getVideoInfo);
router.get('/:video_id/shots', videosController.getVideoShots);
router.get('/:video_id/:shot_id/similar', videosController.findSimilar);
router.post('/:video_id/ask', videosController.askVideoVQA);
router.post('/:video_id/:shot_id/ask', videosController.askShotVQA);

module.exports = router;