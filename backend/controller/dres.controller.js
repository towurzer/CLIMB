const axios = require('axios')

let dresSession = {
    connected: false,
    evaluation_id: null,
    active_task: null
};

let dresState = {
    connected: false,
    dres_url: "https://vbs.videobrowsing.org",
    sessionId: null,
    evaluationId: null
};

exports.connectDres = async (req, res) => {
    const { username, password, dres_url } = req.body;

    if (dres_url) dresState.dres_url = dres_url;

    try {
        console.log(`Connecting to DRES at ${dresState.dres_url}...`);

        // Login
        const loginRes = await axios.post(`${dresState.dres_url}/api/v2/login`, {
            username,
            password
        });

        dresState.sessionId = loginRes.data.sessionId;

        // Active Sessions
        const evalRes = await axios.get(`${dresState.dres_url}/api/v2/client/evaluation/list`, {
            params: { session: dresState.sessionId }
        });

        const evaluations = evalRes.data;

        // get Session
        const targetEval = evaluations.find(e => e.name === "IVADL2026" || e.id === "IVADL2026");

        if (targetEval) {
            dresState.evaluationId = targetEval.id;
        } else if (evaluations.length > 0) {
            dresState.evaluationId = evaluations[0].id;
            console.log(`IVADL2026 not found. Defaulting to: ${dresState.evaluationId}`);
        } else {
            throw new Error("No active evaluations found on the DRES server.");
        }

        dresState.connected = true;

        res.status(200).json({
            status: "success",
            evaluation_id: dresState.evaluationId,
            message: `Connected to DRES successfully. Evaluation ID: ${dresState.evaluationId}`
        });

    } catch (error) {
        console.error("DRES Connection Error:", error.response?.data || error.message);
        dresState.connected = false;
        res.status(500).json({ error: "Failed to connect to DRES.", details: error.message });
    }
};

exports.dresStatus = async (req, res) => {
    res.status(200).json({
        connected: dresState.connected,
        evaluation_id: dresState.evaluationId,
        dres_url: dresState.dres_url
    });
};

exports.submitToDres = async (req, res) => {
    const { video_id, start_time_ms, end_time_ms } = req.body;

    if (!dresState.connected || !dresState.evaluationId) {
        return res.status(401).json({ error: "Not connected to DRES. Please login first." });
    }

    try {
        const payload = {
            answerSets: [{
                answers: [{
                    text: null,
                    mediaItemName: video_id,
                    //mediaItemCollectionName: "IVADL",
                    start: start_time_ms,
                    end: end_time_ms
                }]
            }]
        };

        const submitUrl = `${dresState.dres_url}/api/v2/submit/${dresState.evaluationId}`;

        const response = await axios.post(submitUrl, payload, {
            params: { session: dresState.sessionId }
        });

        res.status(200).json({
            status: "success",
            message: "Submitted successfully!",
            dres_response: response.data
        });

    } catch (error) {
        console.error("DRES KIS Submit Error:", error.response?.data || error.message);
        res.status(400).json({ error: "DRES Submission failed", details: error.response?.data?.description || error.message });
    }
};

exports.submitVqaToDres = async (req, res) => {
    const { text_answer, video_id, start_time_ms, end_time_ms } = req.body;

    if (!dresState.connected || !dresState.evaluationId) {
        return res.status(401).json({ error: "Not connected to DRES. Please login first." });
    }

    try {
        const payload = {
            answerSets: [{
                answers: [{
                    text: text_answer,
                    mediaItemName: video_id || null,
                    mediaItemCollectionName: video_id ? "IVADL" : null,
                    start: start_time_ms || null,
                    end: end_time_ms || null
                }]
            }]
        };

        const submitUrl = `${dresState.dres_url}/api/v2/submit/${dresState.evaluationId}`;

        const response = await axios.post(submitUrl, payload, {
            params: { session: dresState.sessionId }
        });

        res.status(200).json({
            status: "success",
            message: `VQA Answer '${text_answer}' submitted successfully!`,
            dres_response: response.data
        });

    } catch (error) {
        console.error("DRES VQA Submit Error:", error.response?.data || error.message);
        res.status(400).json({ error: "DRES VQA Submission failed", details: error.response?.data?.description || error.message });
    }
};