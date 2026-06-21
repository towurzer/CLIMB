const express = require('express');
const router = express.Router();
const dresController = require('../controller/dres.controller');

router.post('/connect', dresController.connectDres);
router.get('/status', dresController.dresStatus);
router.post('/submit/kis', dresController.submitToDres);
router.post('/submit/vqa', dresController.submitVqaToDres);

module.exports = router;