const express = require('express');

const store = require('./sessionStore');
const sessions = require('./sessions.controller');
const {requireToken} = require('./auth');

const PORT = parseInt(process.env.AVS_SESSION_PORT || '20359', 10);

const app = express();

app.use(express.json({limit: '64kb'}));

app.get('/health', (req, res) => res.status(200).json({status: "ok"}));

app.use(requireToken);

app.post('/avs/session', sessions.create);
app.get('/avs/sessions', sessions.list);
app.post('/avs/session/:code/join', sessions.join);
app.get('/avs/session/:code', sessions.get);
app.post('/avs/session/:code/scene', sessions.recordScene);

app.listen(PORT, () => {
    console.log(`AVS session service listening on ${PORT} (idle timeout ${store.IDLE_TIMEOUT_MS} ms)`);
});

for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => process.exit(0));
