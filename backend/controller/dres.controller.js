const axios = require('axios')
const avsStore = require('../avsSessionStore');

let dresState = {
    connected: false,
    dres_url: "https://vbs.videobrowsing.org",
    sessionId: null,
    evaluationId: null
};

// DRES answers every submission with a meaningful HTTP status, see https://raw.githubusercontent.com/dres-dev/DRES/master/doc/oas-client.json; under "/api/v2/submit/{evaluationId}"
const DRES_STATUS_SUMMARY = {
    200: "Accepted & graded",
    202: "Accepted - awaiting verdict",
    400: "Bad request - malformed submission",
    401: "Unauthorized - DRES session expired",
    404: "Not found - no active task or evaluation",
    412: "Rejected by the server"
};

const summarizeDresStatus = (code) => {
    if (!code) return "No response from DRES";
    return DRES_STATUS_SUMMARY[code] || `DRES responded with HTTP ${code}`;
};

const submitAnswerSet = async (res, answer, {successMessage, errorLabel, record = null}) => {
    if (!dresState.connected || !dresState.evaluationId) {
        return res.status(401).json({error: "Not connected to DRES. Please login first."});
    }

    const payload = {answerSets: [{answers: [answer]}]};
    const submitUrl = `${dresState.dres_url}/api/v2/submit/${dresState.evaluationId}`;

    console.log("DRES submit payload:", JSON.stringify({url: submitUrl, payload}, null, 2));

    try {
        // axios resolves 200 and 202 alike, so the status has to be read off the response
        const response = await axios.post(submitUrl, payload, {
            params: {session: dresState.sessionId}
        });

        // The DRES answer is what scores; recording the delivered scene into the AVS session  is a best-effort overlay that must never block or alter this response.
        if (record) {
            avsStore.recordSceneSafe(record.avs_session, {
                video_id: record.video_id,
                start_frame: record.start_frame,
                end_frame: record.end_frame,
                // null falls back to INDETERMINATE inside the store
                status: response.data?.submission || (response.status === 202 ? "INDETERMINATE" : null)
            });
        }

        return res.status(200).json({
            status: response.status === 202 ? "pending" : "success",
            dres_status: response.status,
            status_summary: summarizeDresStatus(response.status),
            verdict: response.data?.submission || null,
            message: successMessage,
            dres_response: response.data
        });

    } catch (error) {
        const dresStatus = error.response?.status || null;
        console.error(`${errorLabel}:`, error.response?.data || error.message);

        return res.status(400).json({
            error: errorLabel,
            dres_status: dresStatus,
            status_summary: summarizeDresStatus(dresStatus),
            details: error.response?.data?.description || error.message
        });
    }
};

exports.connectDres = async (req, res) => {
    const {username, password, dres_url, dres_name} = req.body;

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
            params: {session: dresState.sessionId}
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
            return res.status(404).json({error: "No active evaluations found on the DRES server."});
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
            return res.status(500).json({
                error: "Failed to connect to DRES.",
                details: error.message,
                dres_error: dresErr
            });
        }
        return res.status(500).json({error: "Failed to connect to DRES.", details: error.message});
    }
};

exports.dresStatus = async (req, res) => {
    res.status(200).json({
        connected: dresState.connected,
        evaluation_id: dresState.evaluationId,
        dres_url: dresState.dres_url
    });
};

// Kis answer submitted (shot), also records it for avs sessions.
exports.submitToDres = async (req, res) => {
    const {video_id, start_frame, end_frame, start_time_ms, end_time_ms, avs_session} = req.body;

    return submitAnswerSet(res, {
        text: null,
        mediaItemName: video_id,
        //mediaItemCollectionName: "IVADL",
        start: start_time_ms,
        end: end_time_ms
    }, {
        successMessage: "Submitted successfully!",
        errorLabel: "DRES Submission failed",
        record: avs_session ? {avs_session, video_id, start_frame, end_frame} : null
    });
};

// VQA answer submitted together with the selected shot
exports.submitVqaToDres = async (req, res) => {
    const {text_answer, video_id, start_time_ms, end_time_ms} = req.body;
    const textToSubmit = (text_answer || "").trim();

    if (!textToSubmit) {
        return res.status(400).json({error: "VQA text is required for submission."});
    }

    return submitAnswerSet(res, {
        text: textToSubmit,
        mediaItemName: video_id || null,
        // mediaItemCollectionName: video_id ? "IVADL" : null,
        start: start_time_ms || null,
        end: end_time_ms || null
    }, {
        successMessage: `VQA Answer '${textToSubmit}' submitted successfully!`,
        errorLabel: "DRES VQA Submission failed"
    });
};

// VQA answer submitted on its own, DRES only needs the text property for these tasks
exports.submitVqaTextOnlyToDres = async (req, res) => {
    const {text_answer} = req.body;
    const textToSubmit = (text_answer || "").trim();

    if (!textToSubmit) {
        return res.status(400).json({error: "VQA text is required for submission."});
    }

    return submitAnswerSet(res, {text: textToSubmit}, {
        successMessage: `VQA Answer '${textToSubmit}' submitted successfully (text only)!`,
        errorLabel: "DRES VQA Submission failed"
    });
};
