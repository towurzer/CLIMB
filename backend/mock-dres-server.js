/**
 * A mock DRES v2 server, for developing and testing submission without a live competition.
 *
 * It implements only what `controller/dres.controller.js` actually calls -- login, the client
 * evaluation list, currentTask, and submit -- but it implements them strictly: a wrong session is
 * a 401, an unknown evaluation is a 404, and a submission that omits `mediaItemCollectionName`
 * while naming a media item is a 400.
 *
 *   node backend/mock-dres-server.js            # port 8080
 *   MOCK_DRES_PORT=9090 node ...                # somewhere else
 *   MOCK_REQUIRE_COLLECTION=0 node ...          # accept payloads without a collection name
 *
 * Test hooks, all outside /api so they can never collide with the real surface:
 *   POST /mock/verdict     {verdict, httpStatus}  what the next submissions answer
 *   POST /mock/task        {name, collectionName, ...} swap the current task
 *   GET  /mock/submissions                       everything received, newest last
 *   POST /mock/reset                             back to defaults, submissions cleared
 */

const express = require('express');

const app = express();
app.use(express.json());

const PORT = process.env.MOCK_DRES_PORT || 8080;
const REQUIRE_COLLECTION = process.env.MOCK_REQUIRE_COLLECTION !== '0';

const USERS = {admin: 'password'};
const DEFAULTS = {
    verdict: 'CORRECT',
    httpStatus: 200,
    task: {
        id: 'task-001',
        name: 'KIS Task 1',
        taskGroup: 'KIS-Visual',
        taskType: 'KIS',
        duration: 300,
        status: 'RUNNING',
        collectionId: 'c-ivadl-01',
        collectionName: 'IVADL',
        mediaCollectionName: 'IVADL'
    }
};

let sessions = new Map();
let state = structuredClone(DEFAULTS);
let submissions = [];

const EVALUATIONS = [
    {id: 'eval-ivadl26', name: 'IVADL26', status: 'ACTIVE', templateId: 'tpl-1'},
    {id: 'eval-other', name: 'SomeOtherEvaluation', status: 'ACTIVE', templateId: 'tpl-2'}
];

const log = (...args) => console.log(`[mock-dres ${new Date().toISOString().slice(11, 19)}]`, ...args);

/** DRES carries the session as a query parameter, not a header. */
const requireSession = (req, res) => {
    const session = req.query.session;
    if (!session || !sessions.has(session)) {
        res.status(401).json({status: false, description: 'Unauthorized: invalid session'});
        return null;
    }
    return session;
};

// login

app.post('/api/v2/login', (req, res) => {
    const {username, password} = req.body || {};
    if (!username || USERS[username] !== password) {
        log(`login REJECTED for ${username}`);
        return res.status(401).json({status: false, description: 'Invalid credentials'});
    }
    const sessionId = `sess-${Math.random().toString(36).slice(2, 12)}`;
    sessions.set(sessionId, {username, since: Date.now()});
    log(`login ok  ${username} -> ${sessionId}`);
    res.status(200).json({id: `user-${username}`, username, role: 'PARTICIPANT', sessionId});
});

// evaluations

app.get('/api/v2/client/evaluation/list', (req, res) => {
    if (!requireSession(req, res)) return;
    res.status(200).json(EVALUATIONS);
});

/**
 * Both shapes are served: DRES puts the evaluation in the path, but answering the bare form too
 * costs nothing and means a client that has not been updated still gets a task rather than a 404
 * it would silently swallow.
 */
const currentTask = (req, res) => {
    if (!requireSession(req, res)) return;
    const evaluationId = req.params.evaluationId || req.query.evaluationId;
    if (evaluationId && !EVALUATIONS.some(e => e.id === evaluationId)) {
        return res.status(404).json({status: false, description: `No evaluation ${evaluationId}`});
    }
    res.status(200).json(state.task);
};

app.get('/api/v2/client/evaluation/currentTask/:evaluationId', currentTask);
app.get('/api/v2/client/evaluation/currentTask', currentTask);

// submission

app.post('/api/v2/submit/:evaluationId', (req, res) => {
    if (!requireSession(req, res)) return;

    const {evaluationId} = req.params;
    if (!EVALUATIONS.some(e => e.id === evaluationId)) {
        return res.status(404).json({status: false, description: `No evaluation ${evaluationId}`});
    }

    const answerSets = req.body?.answerSets;
    if (!Array.isArray(answerSets) || answerSets.length === 0) {
        return res.status(400).json({status: false, description: 'answerSets must be a non-empty array'});
    }

    // DRES reads answers.first() and ignores the rest, which is why CLIMB sends one POST per
    // answer for AVS rather than one POST with many answers. Warn loudly if that ever regresses.
    for (const set of answerSets) {
        if (!Array.isArray(set.answers) || set.answers.length === 0) {
            return res.status(400).json({status: false, description: 'answers must be a non-empty array'});
        }
        if (set.answers.length > 1) {
            log(`WARNING: answer set carried ${set.answers.length} answers; DRES grades only the first`);
        }
    }

    const answer = answerSets[0].answers[0];
    const hasMediaItem = answer.mediaItemName !== undefined && answer.mediaItemName !== null;

    // The rule this mock exists for.
    // Recorded so a test can assert the one-answer-per-POST shape AVS depends on, rather than
    // having to read the server log for the warning above.
    const shape = {
        answerSets: answerSets.length,
        answersInFirstSet: answerSets[0].answers.length,
        maxAnswersInAnySet: Math.max(...answerSets.map((set) => set.answers.length))
    };

    if (REQUIRE_COLLECTION && hasMediaItem && !answer.mediaItemCollectionName) {
        log(`submit REJECTED: mediaItemName=${answer.mediaItemName} without mediaItemCollectionName`);
        submissions.push({at: Date.now(), evaluationId, answer, shape, accepted: false, verdict: null});
        return res.status(400).json({
            status: false,
            description: 'Field mediaItemCollectionName is required when mediaItemName is set'
        });
    }
    if (!hasMediaItem && !answer.text) {
        return res.status(400).json({
            status: false, description: 'An answer needs either a media item or a text answer'
        });
    }

    const verdict = state.verdict;
    const httpStatus = state.httpStatus;
    submissions.push({at: Date.now(), evaluationId, answer, shape, accepted: true, verdict});
    log(`submit ok  ${JSON.stringify(answer)} -> ${verdict} (${httpStatus})`);

    res.status(httpStatus).json({
        status: true,
        submission: verdict,
        description: `Submission received: ${verdict}`
    });
});

// test hooks

app.post('/mock/verdict', (req, res) => {
    const {verdict, httpStatus} = req.body || {};
    if (verdict) state.verdict = verdict;
    if (httpStatus) state.httpStatus = httpStatus;
    log(`verdict set to ${state.verdict} (${state.httpStatus})`);
    res.status(200).json({verdict: state.verdict, httpStatus: state.httpStatus});
});

app.post('/mock/task', (req, res) => {
    state.task = {...state.task, ...(req.body || {})};
    log(`task set to ${JSON.stringify(state.task)}`);
    res.status(200).json(state.task);
});

app.get('/mock/submissions', (req, res) => res.status(200).json(submissions));

app.post('/mock/reset', (req, res) => {
    state = structuredClone(DEFAULTS);
    submissions = [];
    sessions = new Map();
    log('reset');
    res.status(200).json({ok: true});
});

app.get('/mock/health', (req, res) => res.status(200).json({
    ok: true, requireCollection: REQUIRE_COLLECTION, submissions: submissions.length
}));

app.listen(PORT, () => {
    log(`listening on http://localhost:${PORT}`);
    log(`login with admin/password; mediaItemCollectionName ${REQUIRE_COLLECTION ? 'REQUIRED' : 'optional'}`);
});
