const express = require('express');
const router = express.Router();
const teamController = require('../controller/team.controller');

router.get('/', teamController.getTeam);
router.get('/list', teamController.listTeams);
router.post('/connect', teamController.connect);
router.post('/disconnect', teamController.disconnect);
router.post('/session', teamController.newSession);
router.post('/hint', teamController.sendHint);

module.exports = router;
