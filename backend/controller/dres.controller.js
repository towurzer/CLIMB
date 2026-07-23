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
    const { username, password, dres_url, dres_name } = req.body;

    if (dres_url) dresState.dres_url = dres_url;

    // requested evaluation name (frontend defaults to IVADL26)
    const requestedName = dres_name || "IVADL26";

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

        // try to find requested evaluation by name or id
        const targetEval = evaluations.find(e => e.name === requestedName || e.id === requestedName);

        if (targetEval) {
            dresState.evaluationId = targetEval.id;
            dresState.evaluationName = targetEval.name;
        } else if (evaluations.length > 0) {
            // default to first available evaluation and inform the client
            dresState.evaluationId = evaluations[0].id;
            dresState.evaluationName = evaluations[0].name;
            console.log(`${requestedName} not found. Defaulting to: ${dresState.evaluationName}`);
            // return with info that we defaulted
            dresState.connected = true;

            return res.status(200).json({
                status: "success",
                evaluation_id: dresState.evaluationId,
                selected_name: dresState.evaluationName,
                defaulted: true,
                message: `Requested evaluation '${requestedName}' not found. Defaulting to first: ${dresState.evaluationName}`
            });
        } else {
            dresState.connected = false;
            return res.status(404).json({ error: "No active evaluations found on the DRES server." });
        }

        dresState.connected = true;

        res.status(200).json({
            status: "success",
            evaluation_id: dresState.evaluationId,
            selected_name: dresState.evaluationName || requestedName,
            defaulted: false,
            message: `Connected to DRES successfully. Evaluation ID: ${dresState.evaluationId}`
        });

    } catch (error) {
        // prefer the DRES server response if available
        const dresErr = error.response?.data;
        console.error("DRES Connection Error:", dresErr || error.message);
        dresState.connected = false;
        if (dresErr) {
            // forward the original DRES response inside our error payload
            return res.status(500).json({ error: "Failed to connect to DRES.", details: error.message, dres_error: dresErr });
        }
        return res.status(500).json({ error: "Failed to connect to DRES.", details: error.message });
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
    const { text_answer, question, video_id, start_time_ms, end_time_ms } = req.body;
    const textToSubmit = (text_answer || question || "").trim();

    if (!dresState.connected || !dresState.evaluationId) {
        return res.status(401).json({ error: "Not connected to DRES. Please login first." });
    }

    if (!textToSubmit) {
        return res.status(400).json({ error: "VQA text is required for submission." });
    }

    try {
        const payload = {
            answerSets: [{
                answers: [{
                    text: textToSubmit,
                    mediaItemName: video_id || null,
                    // mediaItemCollectionName: video_id ? "IVADL" : null,
                    start: start_time_ms || null,
                    end: end_time_ms || null
                }]
            }]
        };

        const submitUrl = `${dresState.dres_url}/api/v2/submit/${dresState.evaluationId}`;

        console.log("DRES VQA submit payload:", JSON.stringify({ url: submitUrl, payload }, null, 2));

        const response = await axios.post(submitUrl, payload, {
            params: { session: dresState.sessionId }
        });

        res.status(200).json({
            status: "success",
            message: `VQA Answer '${textToSubmit}' submitted successfully!`,
            dres_response: response.data
        });

    } catch (error) {
        console.error("DRES VQA Submit Error:", error.response?.data || error.message);
        res.status(400).json({ error: "DRES VQA Submission failed", details: error.response?.data?.description || error.message });
    }
};