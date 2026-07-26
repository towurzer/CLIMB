const express = require('express');
const router = express.Router();
const avsController = require('../controller/avs.controller');

router.post('/session', avsController.createAvsSession);
router.get('/sessions', avsController.listAvsSessions);
router.post('/session/:code/join', avsController.joinAvsSession);
router.get('/session/:code', avsController.getAvsSession);

module.exports = router;
