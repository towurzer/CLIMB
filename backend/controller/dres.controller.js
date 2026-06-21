let dresSession = {
    connected: false,
    evaluation_id: null,
    active_task: null
};

exports.connectDres = async (req, res) => {
    const { username, password, dres_url } = req.body;

    // TODO: Make actual API call to DRES OpenAPI /login here
    console.log(`Mock connecting to DRES at ${dres_url} with user ${username}...`);

    dresSession.connected = true;
    dresSession.evaluation_id = "IVADL2026_MOCK_ID";

    res.status(200).json({
        status: "success",
        evaluation_id: dresSession.evaluation_id
    });
};

exports.dresStatus = async (req, res) => {
    res.status(200).json(dresSession);
};

exports.submitToDres = async (req, res) => {
    const { video_id, start_time_ms, end_time_ms } = req.body;

    // TODO: Make actual API call to DRES /submit here
    console.log(`[DRES KIS SUBMIT] Video: ${video_id}, Time: ${start_time_ms}-${end_time_ms}ms`);

    res.status(200).json({
        status: "mock_success",
        message: `Successfully submitted segment for video ${video_id} to DRES.`
    });
};

exports.submitVqaToDres = async (req, res) => {
    const { text_answer, video_id, start_time_ms, end_time_ms } = req.body;

    // TODO: Make actual API call to DRES /submit here
    console.log(`[DRES VQA SUBMIT] Answer: "${text_answer}"`);

    res.status(200).json({
        status: "mock_success",
        message: `Successfully submitted VQA answer to DRES.`
    });
};