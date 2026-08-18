import {useState, useCallback, useEffect, useRef} from "react";
import {ALL_SOURCE_KEYS} from "./sources";
import SearchBar from "./components/SearchBar";
import ResultsGrid from "./components/ResultsGrid";
import VideoPlayer from "./components/VideoPlayer";
import ShotBrowser from "./components/ShotBrowser";
import VideoBrowser from "./components/VideoBrowser";
import VqaAnswer from "./components/VqaAnswer";
import SubmissionLog from "./components/SubmissionLog";
import KeyframeTime from "./components/KeyframeTime";
import AvsSessionBar from "./components/AvsSessionBar";
import DresLoginModal from "./components/DresLoginModal";
import BackendStatusDot from "./components/BackendStatusDot";
import {describeDresResult, describeDresError} from "./dresSubmission";
import {sceneKey} from "./sceneKey";
import {formatTimecode, parseTimecode, snapMsToFrame, keyframeMs} from "./timecode";
import "./App.css";

const EMPTY_SET = new Set();

const avsHolds = (status) => status === "CORRECT" || status === "INDETERMINATE";

// Scene keys to hide, from a session snapshot's scene list.
const heldSceneKeys = (scenes) => new Set(
    (scenes || [])
        .filter((s) => avsHolds(s.status))
        .map((s) => sceneKey(s.scene_id))
);
const backendHost = import.meta.env.BACKEND_URL || "localhost";
const backendPort = import.meta.env.BACKEND_PORT || "8000";
const API_URL = backendHost.startsWith("http://") || backendHost.startsWith("https://")
    ? backendHost
    : `http://${backendHost}:${backendPort}`;

function App() {
    // memory variables - calling set... and react changes everything based on the other variable
    const [mode, setMode] = useState("search");
    const [results, setResults] = useState([]);
    const [query, setQuery] = useState("");
    const [loading, setLoading] = useState(false);
    const [selectedResult, setSelectedResult] = useState(null);
    const [submitStatus, setSubmitStatus] = useState(null);
    const [submitMessage, setSubmitMessage] = useState("");
    const [confirmSubmit, setConfirmSubmit] = useState(false);
    const [dresUrl, setDresUrl] = useState("https://vbs.videobrowsing.org");
    const [dresName, setDresName] = useState("IVADL26");
    const [searchPage, setSearchPage] = useState(1);
    const [searchPerPage] = useState(24);
    const [searchHasMore, setSearchHasMore] = useState(false);
    const [searchTemporal, setSearchTemporal] = useState(null); // set only by an `A >> B` query
    const [sources, setSources] = useState(ALL_SOURCE_KEYS);
    const [searchLoadingMore, setSearchLoadingMore] = useState(false);
    const [resultsPanel, setResultsPanel] = useState(null);
    const [searchSentinel, setSearchSentinel] = useState(null);
    const [dresUsername, setDresUsername] = useState("");
    const [dresPassword, setDresPassword] = useState("");
    const [dresConnected, setDresConnected] = useState(false);
    const [dresStatus, setDresStatus] = useState("Not connected");
    const [dresLoading, setDresLoading] = useState(false);
    const [dresLoginOpen, setDresLoginOpen] = useState(false);
    // what DRES actually connected us to - the name we asked for is not always the one we got
    const [dresEvaluation, setDresEvaluation] = useState(null); // {id, name} | null
    const [snackbar, setSnackbar] = useState({visible: false, message: "", type: "info", raw: null});
    const [searchHistory, setSearchHistory] = useState([]);
    const [submissions, setSubmissions] = useState([]);
    const [excludedVideos, setExcludedVideos] = useState([]);
    const [similarSource, setSimilarSource] = useState(null);
    // null means "whatever the selected keyframe says", a string means the user typed over it
    const [timeOverride, setTimeOverride] = useState({from: null, to: null});

    // AVS: task-type toggle (independent of the search/browse `mode`) and the collaborative session state that hides/marks scenes submitted.
    const [taskMode, setTaskMode] = useState("kis"); // "kis" | "avs"
    const [avsSession, setAvsSession] = useState(null); // {code, name, expiresInMs, counts} | null
    const [avsSubmittedScenes, setAvsSubmittedScenes] = useState(EMPTY_SET); // Set<sceneKey(scene_id)>
    const [avsCoveredVideos, setAvsCoveredVideos] = useState(EMPTY_SET);      // Set<videoId>
    const [avsExpiredCode, setAvsExpiredCode] = useState(null); // last session that idled out
    const [avsCollabOffline, setAvsCollabOffline] = useState(false);

    const avsFilterRef = useRef({mode: "kis", code: null});
    useEffect(() => {
        avsFilterRef.current = {mode: taskMode, code: avsSession?.code || null};
    }, [taskMode, avsSession]);

    const sourcesRef = useRef(sources);
    useEffect(() => {
        sourcesRef.current = sources;
    }, [sources]);

    const excludedRef = useRef(excludedVideos);
    useEffect(() => {
        excludedRef.current = excludedVideos;
    }, [excludedVideos]);

    const isAvs = taskMode === "avs";

    // The submitted time is the keyframe, not the scene, so both fields start out on the keyframe
    const selectedKeyframeMs = keyframeMs(selectedResult);
    const autoTimecode = selectedKeyframeMs != null ? formatTimecode(selectedKeyframeMs) : "";
    const fromText = timeOverride.from ?? autoTimecode;
    const toText = timeOverride.to ?? autoTimecode;
    const fromMs = parseTimecode(fromText);
    const toMs = parseTimecode(toText);
    const timesValid = fromMs !== null && toMs !== null;
    // frame snapped, so DRES never gets a moment sitting between two frames
    const submitStartMs = timesValid ? snapMsToFrame(fromMs, selectedResult?.fps) : null;
    const submitEndMs = timesValid ? snapMsToFrame(toMs, selectedResult?.fps) : null;

    // Picking a new keyframe throws away whatever the user typed.
    // Keyed on the values instead of the object, because every selection path builds a fresh one.
    useEffect(() => {
        setTimeOverride({from: null, to: null});
    }, [selectedResult?.video_id, selectedResult?.keyframe_id, selectedKeyframeMs]);

    const handleTimeChange = useCallback((field, value) => {
        setTimeOverride((prev) => ({...prev, [field]: value}));
        // changing the time mid confirmation means the confirmation was for a different time
        setConfirmSubmit(false);
    }, []);

    // get current time string
    const timeNow = () => new Date().toLocaleTimeString("cs-CZ", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });

    // check if we already submitted this exact segment
    const alreadySubmitted = (result) => {
        return submissions.some(
            (s) => s.type === "segment" &&
                s.video_id === result.video_id &&
                s.keyframe_id === result.keyframe_id
        );
    };

    // Keyboard shortcuts - arrows and esc
    useEffect(() => {
        const handleKeyDown = (e) => {
            const tag = e.target.tagName;
            // cause we dont want it to work in text area - we want to be avle to normally write in there
            if (tag === "INPUT" || tag === "TEXTAREA") return;
            // if event is equal to this, we want to move to the next one or previous one
            if (e.key === "ArrowRight" || e.key === "ArrowLeft" ||
                e.key === "ArrowDown" || e.key === "ArrowUp") {
                // if no results - no sense in navigating
                if (results.length === 0) return;
                // blocking default behaviour of the server
                e.preventDefault();
                // where are we rn - a sequence hit can have a partner selected rather than its
                // anchor, and that still counts as being on that card
                const onCard = (r) => [r, ...(r.temporal_partners || [])].some(
                    (scene) => scene.video_id === selectedResult.video_id &&
                        scene.keyframe_id === selectedResult.keyframe_id
                );
                const currentIndex = selectedResult ? results.findIndex(onCard) : -1;
                let nextIndex;
                // moving to the next frame in both directions + cycling
                if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                    nextIndex = currentIndex < results.length - 1 ? currentIndex + 1 : 0;
                } else {
                    nextIndex = currentIndex > 0 ? currentIndex - 1 : results.length - 1;
                }
                // selecting the video onto my right side of the page
                setSelectedResult(results[nextIndex]);
                setConfirmSubmit(false);
                setSubmitStatus(null);
            }
            // escape works as disselecting stuff
            if (e.key === "Escape") {
                setSelectedResult(null);
                setConfirmSubmit(false);
                setSubmitStatus(null);
            }
        };
        // putting this on the whole window - changing when the results are changing
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [results, selectedResult]);

    // searchiing - asynchornous - waiting for the response from the server
    const fetchSearchPage = useCallback(async (searchQuery, page = 1, append = false) => {
        try {
            // In AVS mode the backend hides scenes already submitted in our session
            const {mode, code} = avsFilterRef.current;
            const avsParam = mode === "avs" && code ? `&avs_session=${code}` : "";
            // Omitted when everything is ticked, which the backend already reads as "all sources".
            const picked = sourcesRef.current;
            const sourcesParam = picked.length < ALL_SOURCE_KEYS.length
                ? `&sources=${picked.join(",")}`
                : "";

            const excluded = excludedRef.current;
            const excludeParam = excluded.length ? `&exclude=${excluded.join(",")}` : "";
            const res = await fetch(
                `${API_URL}/climb/search?q=${encodeURIComponent(searchQuery)}&page=${page}&per_page=${searchPerPage}${avsParam}${sourcesParam}${excludeParam}`
            );
            const data = await res.json();
            const pageResults = data.results || [];
            setResults((prev) => (append ? [...prev, ...pageResults] : pageResults));
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            // Null unless the query used `A >> B`; describes how it was understood.
            setSearchTemporal(data.temporal || null);
            return pageResults;
        } catch (err) {
            console.error("Search failed:", err);
            return [];
        }
    }, [searchPerPage]);

    const fetchSimilarPage = useCallback(async (keyframeId, excluded = [], page = 1, append = false) => {
        const params = new URLSearchParams({page, per_page: searchPerPage});
        if (excluded.length) params.set("exclude", excluded.join(","));
        try {
            // Find-similar lives under /search now: it is fused, cached and paged like a search.
            const res = await fetch(
                `${API_URL}/climb/search/similar/${keyframeId}?${params}`
            );
            const data = await res.json();
            const pageResults = data.results || [];
            setResults((prev) => (append ? [...prev, ...pageResults] : pageResults));
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            setSearchTemporal(null); // find-similar replaces the result set, sequence or not
            return pageResults;
        } catch (err) {
            console.error("Similar search failed:", err);
            return [];
        }
    }, [searchPerPage]);

    // "Similar to X / shot Y" plus any active exclusions - shown in the results header
    const similarLabel = (videoId, keyframeId, excluded = []) => {
        const base = `Similar to ${videoId} / keyframe ${keyframeId}`;
        return excluded.length ? `${base} --exclude: ${excluded.join(", ")}` : base;
    };

    const handleSearch = useCallback(async (searchQuery) => {
        if (!searchQuery.trim()) return;
        setMode("search");
        setQuery(searchQuery);
        setLoading(true);
        setSelectedResult(null);
        setSubmitStatus(null);
        setConfirmSubmit(false);
        setSearchHasMore(false);
        setSearchPage(1);
        excludedRef.current = [];
        setExcludedVideos([]);
        setSimilarSource(null);
        setSearchHistory((prev) => {
            const filtered = prev.filter((q) => q !== searchQuery);
            return [searchQuery, ...filtered].slice(0, 10);
        });

        await fetchSearchPage(searchQuery, 1, false);
        setLoading(false);
    }, [fetchSearchPage]);

    // Ticking a source changes the result set, so the query re-runs on the spot
    const handleToggleSource = useCallback((key) => {
        const next = sources.includes(key)
            ? sources.filter((source) => source !== key)
            : [...sources, key];

        if (!next.length) return;

        setSources(next);

        sourcesRef.current = next;

        if (mode !== "search" || similarSource || !query.trim()) return;

        (async () => {
            setLoading(true);
            setSelectedResult(null);
            setSearchPage(1);
            setSearchHasMore(false);
            await fetchSearchPage(query, 1, false);
            setLoading(false);
        })();
    }, [sources, mode, similarSource, query, fetchSearchPage]);

    // Find similar refines what we are already looking at, so exclusions carry over
    const handleFindSimilar = useCallback(async (result) => {
        const excluded = excludedRef.current;
        setLoading(true);
        setMode("search");
        setQuery(similarLabel(result.video_id, result.keyframe_id, excluded));
        setSimilarSource({video_id: result.video_id, keyframe_id: result.keyframe_id});
        setSelectedResult(null);
        setConfirmSubmit(false);
        try {
            await fetchSimilarPage(result.keyframe_id, excluded);
        } finally {
            setLoading(false);
        }
    }, [fetchSimilarPage]);

    // Exclude video from search
    const handleExcludeVideo = useCallback(async (result) => {
        const updatedExcluded = [...new Set([...excludedRef.current, result.video_id])];
        excludedRef.current = updatedExcluded;
        setExcludedVideos(updatedExcluded);

        const baseQuery = query.split(" --exclude:")[0].trim();
        if (!baseQuery) {
            setSnackbar({
                visible: true,
                message: `${result.video_id} excluded - it will be left out of your next search`,
                type: "info",
                raw: null,
            });
            setTimeout(() => setSnackbar((s) => ({...s, visible: false})), 4000);
            return;
        }

        // re-run whichever kind of search produced the current results (similarity search / text search)
        const isSimilar = Boolean(similarSource);
        const updatedQuery = isSimilar
            ? similarLabel(similarSource.video_id, similarSource.keyframe_id, updatedExcluded)
            : `${baseQuery} --exclude: ${updatedExcluded.join(", ")}`;

        // can be triggered from the browse tab, so jump back to the results we just refreshed
        setMode("search");
        setBrowseReturnMode(null);
        setQuery(updatedQuery);
        setLoading(true);
        setSelectedResult(null);
        setConfirmSubmit(false);
        setSearchHasMore(false);
        setSearchPage(1);
        try {
            if (isSimilar) {
                await fetchSimilarPage(similarSource.keyframe_id, updatedExcluded);
            } else {
                await fetchSearchPage(updatedQuery, 1, false);
            }
        } catch (err) {
            console.error("Search with exclude failed:", err);
        } finally {
            setLoading(false);
        }
    }, [query, similarSource, fetchSearchPage, fetchSimilarPage]);

    const handleDresLogin = useCallback(async () => {
        if (!dresUsername.trim() || !dresPassword.trim()) {
            setDresStatus("Enter username and password");
            return;
        }
        setDresLoading(true);
        setDresStatus("Connecting to DRES...");
        try {
            const res = await fetch(`${API_URL}/climb/dres/connect`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    username: dresUsername,
                    password: dresPassword,
                    dres_url: dresUrl,
                    dres_name: dresName,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                // prefer nested DRES server error if present
                const dresErr = data?.dres_error || data;
                const msg = (dresErr && typeof dresErr === "object")
                    ? (dresErr.description || dresErr.message || JSON.stringify(dresErr))
                    : (data?.description || data?.details || data?.error || JSON.stringify(data));
                setDresConnected(false);
                setDresEvaluation(null);
                setDresStatus(msg);
                setSnackbar({visible: true, message: msg, type: "error", raw: dresErr});
                setTimeout(() => setSnackbar((s) => ({...s, visible: false, raw: null})), 7000);
                return;
            }
            setDresConnected(true);
            setDresStatus(data.message || "Connected to DRES");
            setDresEvaluation({id: data.evaluation_id || null, name: data.selected_name || dresName});
            // nothing left to do in the popup once we are in
            setDresLoginOpen(false);
            // update dres name if server selected a different one
            if (data.selected_name) {
                setDresName(data.selected_name);
            }
            if (data.defaulted) {
                setSnackbar({
                    visible: true,
                    message: `Not found, defaulting to first: ${data.selected_name}`,
                    type: "info",
                    raw: null
                });
                setTimeout(() => setSnackbar((s) => ({...s, visible: false})), 5000);
            }
        } catch (err) {
            console.error("DRES login failed:", err);
            setDresConnected(false);
            setDresEvaluation(null);
            const errMsg = err?.message || "DRES login failed";
            setDresStatus(errMsg);
            setSnackbar({visible: true, message: errMsg, type: "error", raw: null});
            setTimeout(() => setSnackbar((s) => ({...s, visible: false})), 7000);
        } finally {
            setDresLoading(false);
        }
    }, [dresUrl, dresUsername, dresPassword, dresName]);

    // Fold a session snapshot from the backend into local hide/mark state
    const applyAvsSession = useCallback((data) => {
        setAvsExpiredCode(null);
        setAvsCollabOffline(data.collab === "offline");
        setAvsSession({code: data.code, name: data.name, expiresInMs: data.expiresInMs, counts: data.counts});
        setAvsSubmittedScenes(heldSceneKeys(data.scenes));
        setAvsCoveredVideos(new Set(data.coveredVideos || []));
    }, []);

    const createAvsSession = useCallback(async () => {
        try {
            const res = await fetch(`${API_URL}/climb/avs/session`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({name: dresName || null}),
            });
            // A new session cannot be created without the shared service, so this one really does fail
            if (res.status === 503) {
                setAvsCollabOffline(true);
                return;
            }
            applyAvsSession(await res.json());
        } catch (err) {
            console.error("Create AVS session failed:", err);
        }
    }, [dresName, applyAvsSession]);

    // Returns "ok" | "notfound" | "offline" so the join UI can tell the which it was
    const joinAvsSession = useCallback(async (code) => {
        const clean = (code || "").trim().toUpperCase();
        if (!clean) return "notfound";
        try {
            const res = await fetch(`${API_URL}/climb/avs/session/${clean}/join`, {method: "POST"});
            if (res.status === 503) {
                setAvsCollabOffline(true);
                return "offline";
            }
            if (!res.ok) return "notfound";
            applyAvsSession(await res.json());
            return "ok";
        } catch (err) {
            console.error("Join AVS session failed:", err);
            return "offline";
        }
    }, [applyAvsSession]);

    // Leaving is local only, the session lives on until it idles out
    const leaveAvsSession = useCallback(() => {
        setAvsSession(null);
        setAvsSubmittedScenes(EMPTY_SET);
        setAvsCoveredVideos(EMPTY_SET);
        setAvsCollabOffline(false);
    }, []);

    // Poll the session while in AVS mode: refreshes the shared hide/mark sets and doubles as the keep-alive heartbeat.
    //
    // 404 means the session is really gone, so the exclusion list is dropped.
    // 503 means the backend could not reach the shared session service and has nothing to say
    useEffect(() => {
        if (!isAvs || !avsSession?.code) return;
        const code = avsSession.code;
        let cancelled = false;

        const poll = async () => {
            try {
                const res = await fetch(`${API_URL}/climb/avs/session/${code}`);
                if (cancelled) return;
                if (res.status === 404) {
                    setAvsExpiredCode(code);
                    setAvsCollabOffline(false);
                    setAvsSession(null);
                    setAvsSubmittedScenes(EMPTY_SET);
                    setAvsCoveredVideos(EMPTY_SET);
                    return;
                }
                if (res.status === 503) {
                    setAvsCollabOffline(true);
                    return; // keep the session and the scenes we already have
                }
                if (!res.ok) return;
                const data = await res.json();
                if (cancelled) return;
                setAvsCollabOffline(data.collab === "offline");
                setAvsSubmittedScenes(heldSceneKeys(data.scenes));
                setAvsCoveredVideos(new Set(data.coveredVideos || []));
                setAvsSession((prev) => prev ? {...prev, name: data.name, expiresInMs: data.expiresInMs, counts: data.counts} : prev);
            } catch (err) {
                // Our own backend is unreachable, which the status dot already reports. Keep state
                // and let the next tick retry.
                if (!cancelled) setAvsCollabOffline(true);
            }
        };

        poll();
        const id = setInterval(poll, 4000);
        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, [isAvs, avsSession?.code]);

    // Shot select from filmstrip - the one under video - that is why we can use the same video id
    const handleShotSelect = useCallback((shot, clicked) => {
        // of something was submitted - reseting it
        setConfirmSubmit(false);
        setSubmitStatus(null);
        setSelectedResult((prev) => {
            // 25 is just a backup becuase it is most common
            const fps = shot.fps || prev?.fps || 25;
            // ShotBrowser hands over a raw scene from /videos/:id/scenes, where the keyframe
            // fields sit inside `keyframes`; VideoBrowser hands over an already-flattened one.
            // Accepting both keeps the two filmstrips from needing different callbacks.
            const keyframes = shot.keyframes || [];
            const fallback = keyframes[Math.floor(keyframes.length / 2)] || {};
            const keyframe = clicked || {
                keyframe_id: shot.keyframe_id ?? fallback.keyframe_id,
                kf_index: shot.kf_index ?? fallback.kf_index,
                // The keyframe instant has to survive the hop: it is what prefills the submission
                // time and where the player seeks to. Dropping it here left both empty.
                frame_number: shot.frame_number ?? fallback.frame_number,
                keyframe_time_ms: shot.keyframe_time_ms ?? fallback.keyframe_time_ms,
                thumbnail_url: shot.thumbnail_url ?? fallback.thumbnail_url,
                keyframe_url: shot.keyframe_url ?? fallback.keyframe_url,
            };
            return {
                video_id: prev?.video_id || shot.video_id,
                scene_id: shot.scene_id,
                keyframe_id: keyframe.keyframe_id,
                kf_index: keyframe.kf_index,
                keyframes,
                score: prev?.score || 0,
                start_frame: shot.start_frame,
                end_frame: shot.end_frame,
                fps: fps,
                frame_number: keyframe.frame_number,
                keyframe_time_ms: keyframe.keyframe_time_ms,
                // we need to recalculate, because DRES is usinf ms not frames.
                // these are the bounds of the whole scene, what we submit comes from the keyframe time fields
                start_time_ms: Math.round((shot.start_frame / fps) * 1000),
                end_time_ms: Math.round((shot.end_frame / fps) * 1000),
                thumbnail_url: keyframe.thumbnail_url,
                keyframe_url: keyframe.keyframe_url,
                video_url: shot.video_url || prev?.video_url,
            };
        });
    }, []);

    //Submit to DRES  - calles after clicking on submit button
    // the times come from the keyframe time fields, not from the result itself
    const handleSubmit = useCallback(async (result, startTimeMs, endTimeMs) => {
        setSubmitStatus("submitting");
        setSubmitMessage("Submitting to DRES...");
        setConfirmSubmit(false);
        // what is submitted
        const entry = {
            type: "segment",
            task: taskMode,
            video_id: result.video_id,
            scene_id: result.scene_id,
            keyframe_id: result.keyframe_id,
            start_time_ms: startTimeMs,
            end_time_ms: endTimeMs,
            time: timeNow(),
            status: "submitting",
        };

        // send data to backend
        try {
            const res = await fetch(`${API_URL}/climb/dres/submit/kis`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    video_id: result.video_id,
                    start_time_ms: startTimeMs,
                    end_time_ms: endTimeMs,
                    // scene frame range + session let the backend record the whole
                    // scene (all its keyframes) into our shared AVS session
                    scene_id: result.scene_id,
                    start_frame: result.start_frame,
                    end_frame: result.end_frame,
                    avs_session: isAvs && avsSession?.code ? avsSession.code : undefined,
                }),
            });
            const data = await res.json();
            const {state, text} = res.ok
                ? describeDresResult(data)
                : describeDresError(data, "DRES Submission failed");
            entry.status = state;
            setSubmitStatus(state);
            setSubmitMessage(text);
            // AVS: a correct / awaiting-verdict scene is hidden from search for
            // everyone (poll confirms). Mark it locally now, but KEEP it selected so
            // the preview and filmstrip stay put and show a persistent "submitted"
            // state. A WRONG scene is left visible so its keyframes can still be tried.
            if (isAvs && res.ok && avsHolds(data.verdict)) {
                const key = sceneKey(result.scene_id);
                setAvsSubmittedScenes((prev) => new Set(prev).add(key));
            }
        } catch (err) {
            // it did not work
            console.error("DRES submit failed:", err);
            const {state, text} = describeDresError(
                null,
                `DRES Submission failed: ${err.message || "Please check DRES connection."}`
            );
            entry.status = state;
            setSubmitStatus(state);
            setSubmitMessage(text);
        }
        // for our submitted array
        setSubmissions((prev) => [entry, ...prev]);
    }, [taskMode, isAvs, avsSession]);

    //  VQA submit callback, mode is "media" (text + shot) or "text" (text only)
    const handleVqaSubmitted = useCallback((answer, status, mode) => {
        setSubmissions((prev) => [
            {type: "vqa", text_answer: answer, time: timeNow(), status, mode},
            ...prev,
        ]);
    }, []);

    // Select result
    const handleSelect = useCallback((result) => {
        setSelectedResult(result);
        setConfirmSubmit(false);
        setSubmitStatus(null);
        setSubmitMessage("");
    }, []);

    const handleLoadMoreSearch = useCallback(async () => {
        if (!searchHasMore || loading || searchLoadingMore) return;
        setSearchLoadingMore(true);
        const nextPage = searchPage + 1;
        try {
            // ask for next page, endpoint depends on search type
            if (similarSource) {
                await fetchSimilarPage(similarSource.keyframe_id, excludedRef.current, nextPage, true);
            } else {
                await fetchSearchPage(query, nextPage, true);
            }
        } catch (err) {
            console.error("Load more search results failed:", err);
        } finally {
            setSearchLoadingMore(false);
        }
    }, [searchHasMore, loading, searchLoadingMore, searchPage, query, similarSource, fetchSearchPage, fetchSimilarPage]);

    useEffect(() => {
        if (!searchSentinel || !resultsPanel || !searchHasMore) return;
        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting && !loading && !searchLoadingMore) {
                        handleLoadMoreSearch();
                    }
                });
            },
            {root: resultsPanel, rootMargin: "300px", threshold: 0.1}
        );

        observer.observe(searchSentinel);
        return () => observer.disconnect();
    }, [searchSentinel, resultsPanel, handleLoadMoreSearch, loading, searchLoadingMore, searchHasMore]);

    const handleBrowseSelect = useCallback((result) => {
        setSelectedResult(result);
        setConfirmSubmit(false);
        setSubmitStatus(null);
        setSubmitMessage("");
    }, []);

    // Jump to browse mode with a specific video's shots open (triggered from the sidebar ShotBrowser)
    const [browseOpenVideoId, setBrowseOpenVideoId] = useState(null);
    // Where "Browse in Fullscreen" was fired from. Set only when the jump crossed modes, so the
    // shot view's back arrow returns to the results the user came from instead of the video grid.
    const [browseReturnMode, setBrowseReturnMode] = useState(null);
    const handleBrowseAllShots = useCallback((videoId) => {
        // Re-triggering from inside browse keeps the original origin: the user never left it.
        if (mode !== "browse") setBrowseReturnMode(mode);
        setMode("browse");
        setBrowseOpenVideoId(videoId);
    }, [mode]);
    const handleBrowseOpenVideoHandled = useCallback(() => {
        setBrowseOpenVideoId(null);
    }, []);
    const handleBrowseExit = useCallback(() => {
        setMode(browseReturnMode || "search");
        setBrowseReturnMode(null);
    }, [browseReturnMode]);
    const switchMode = useCallback((next) => {
        setBrowseReturnMode(null);
        setMode(next);
    }, []);

    const isDuplicate = selectedResult ? alreadySubmitted(selectedResult) : false;

    // AVS: is the currently previewed scene already submitted in this session?
    // Drives the "submitted" preview overlay and disables re-submitting it.
    const selectedSubmitted = Boolean(
        isAvs && selectedResult &&
        avsSubmittedScenes.has(sceneKey(selectedResult.scene_id))
    );

    // Results actually shown (submitted scenes are hidden in AVS), so the counter
    // matches the grid instead of counting scenes that have been filtered out.
    const visibleResultCount = (isAvs && avsSubmittedScenes.size)
        ? results.filter((r) => !avsSubmittedScenes.has(sceneKey(r.scene_id))).length
        : results.length;
    const hiddenResultCount = results.length - visibleResultCount;

    // How an `A >> B` query was understood, plus how much each stage found on its own. When a
    // sequence comes back empty this is the only thing that distinguishes "a stage matched
    // nothing" from "no single video contained all of them".
    const temporalNote = searchTemporal && [
        `${(searchTemporal.stages || []).length} stages`,
        `Δ ${(searchTemporal.deltas_ms || []).map((ms) => `${Math.round(ms / 1000)}s`).join(" / ")}`,
        `stage hits ${(searchTemporal.stage_counts || []).join(" / ")}`
    ].join(" · ");

    return (
        <div className="app">
            {/* for the top top bar */}
            <header className="app-header">
                <div className="app-header-left">
                    <h1>
                        CLIMB, Keyframe-Level Multi-Signal Retrieval with Collaborative Ad-Hoc Search
                        <br/>
                        Made with love from the <a href="https://www.aau.at">AAU</a> students <a href="https://github.com/towurzer">Wurzer</a> and <a
                        href="https://github.com/sesnr">Eisner</a>.
                    </h1>
                </div>

                <div className="app-header-right">
                    {snackbar.visible && (
                        <div className={`snackbar ${snackbar.type || "info"}`}>
                            {snackbar.raw ? (
                                <pre>{JSON.stringify(snackbar.raw, null, 2)}</pre>
                            ) : (
                                <div>{snackbar.message}</div>
                            )}
                        </div>
                    )}
                    <div className="mode-toggle">
                        {/* Switching by hand drops the "return here" marker: the user picked a mode. */}
                        <button className={`mode-btn ${mode === "search" ? "active" : ""}`}
                                onClick={() => switchMode("search")}>Search
                        </button>
                        <button className={`mode-btn ${mode === "browse" ? "active" : ""}`}
                                onClick={() => switchMode("browse")}>Browse
                        </button>
                    </div>
                    {/* task type: KIS (single shot) vs AVS (many collaborative instances) */}
                    <div className="mode-toggle task-toggle">
                        <button className={`mode-btn ${taskMode === "kis" ? "active" : ""}`}
                                onClick={() => setTaskMode("kis")}
                                title="Known-Item Search: submit one shot">KIS
                        </button>
                        <button className={`mode-btn ${taskMode === "avs" ? "active" : ""}`}
                                onClick={() => setTaskMode("avs")}
                                title="Ad-hoc Video Search: submit many instances, collaboratively">AVS
                        </button>
                    </div>
                    {/* the form itself lives in a popup, the header only shows where we stand */}
                    <button
                        className={`dres-login-btn ${dresConnected ? "connected" : ""}`}
                        onClick={() => setDresLoginOpen(true)}
                        title={dresStatus}
                    >
                        <span className="dres-login-dot"/>
                        {dresLoading ? "Connecting..."
                            : dresConnected ? `DRES · ${dresName}`
                                : "Login to DRES"}
                    </button>
                    <BackendStatusDot apiUrl={API_URL}/>
                </div>
            </header>
            <DresLoginModal
                open={dresLoginOpen}
                onClose={() => setDresLoginOpen(false)}
                url={dresUrl}
                name={dresName}
                username={dresUsername}
                password={dresPassword}
                onUrlChange={setDresUrl}
                onNameChange={setDresName}
                onUsernameChange={setDresUsername}
                onPasswordChange={setDresPassword}
                onSubmit={handleDresLogin}
                loading={dresLoading}
                connected={dresConnected}
                status={dresStatus}
                evaluation={dresEvaluation}
            />
            {/* AVS collaborative session controls, only in AVS mode */}
            {isAvs && (
                <AvsSessionBar
                    apiUrl={API_URL}
                    session={avsSession}
                    expiredCode={avsExpiredCode}
                    collabOffline={avsCollabOffline}
                    onCreate={createAvsSession}
                    onJoin={joinAvsSession}
                    onLeave={leaveAvsSession}
                />
            )}
            {/* if statement, because for browsing we dont need it */}
            {mode === "search" && (
                <SearchBar
                    onSearch={handleSearch}
                    loading={loading}
                    history={searchHistory}
                    sources={sources}
                    onToggleSource={handleToggleSource}
                />

            )}

            <div className="main-content">
                <div className="results-panel" ref={setResultsPanel}>
                    {/* difference between search and browse mode */}
                    {mode === "search" ? (
                            <>
                                {loading && <div className="loading">Searching...</div>}
                                {/* A sequence that found nothing still shows its header - that is
                                    exactly when the per-stage counts are worth reading */}
                                {!loading && (results.length > 0 || temporalNote) && (
                                    <div className="results-info">
                                        {visibleResultCount}{searchHasMore ? "+" : ""} results for "{query}"
                                        {temporalNote && (
                                            <span className="results-temporal-note"> · {temporalNote}</span>
                                        )}
                                        {hiddenResultCount > 0 && (
                                            <span className="results-hidden-note"> · {hiddenResultCount} submitted hidden</span>
                                        )}
                                        <span className="shortcuts-hint">← → navigate · Esc deselect · Ctrl+K search</span>
                                    </div>
                                )}
                                <ResultsGrid
                                    results={results}
                                    selectedResult={selectedResult}
                                    onSelect={handleSelect}
                                    onFindSimilar={handleFindSimilar}
                                    onExcludeVideo={handleExcludeVideo}
                                    hiddenScenes={isAvs ? avsSubmittedScenes : EMPTY_SET}
                                    coveredVideos={isAvs ? avsCoveredVideos : EMPTY_SET}
                                />
                                <div ref={setSearchSentinel} className="search-sentinel"/>
                                {searchLoadingMore && <div className="loading">Loading more results...</div>}
                            </>
                        ) :
                        (
                            /* for browsing - showing everything */
                            <VideoBrowser
                                apiUrl={API_URL}
                                onSelectShot={handleBrowseSelect}
                                openVideoId={browseOpenVideoId}
                                onOpenVideoHandled={handleBrowseOpenVideoHandled}
                                onExitBrowse={browseReturnMode ? handleBrowseExit : null}
                                submittedScenes={isAvs ? avsSubmittedScenes : EMPTY_SET}
                                coveredVideos={isAvs ? avsCoveredVideos : EMPTY_SET}
                            />
                        )}
                </div>
                {/* right pannel , if nothing selected then dyplaying nothing */}
                <div className="player-panel">
                    {selectedResult ? (
                        <>
                            <div className="result-details">
                                <span>Video: {selectedResult.video_id}</span>
                                <span>Scene: {selectedResult.scene_id}</span>
                                <span>Score: {(selectedResult.score * 100).toFixed(1)}%</span>
                                <span>Scene: {formatTimecode(selectedResult.start_time_ms)} – {formatTimecode(selectedResult.end_time_ms)}</span>
                                <span>Keyframe: {autoTimecode || "unknown"}</span>
                            </div>
                            {/* Once submitted in an AVS session, replace the player with a
                                blurred still + green check */}
                            {selectedSubmitted ? (
                                <div className="submitted-preview">
                                    <img className="submitted-preview-bg" src={selectedResult.thumbnail_url} alt=""/>
                                    <div className="submitted-preview-overlay">
                                        <div className="submitted-preview-check">✓</div>
                                        <div className="submitted-preview-label">Already submitted</div>
                                    </div>
                                </div>
                            ) : (
                                <VideoPlayer result={selectedResult}/>
                            )}
                            <div className="actions">
                                {/* what actually gets submitted - auto filled from the keyframe, editable */}
                                <KeyframeTime
                                    from={fromText}
                                    to={toText}
                                    fromValid={fromMs !== null}
                                    toValid={toMs !== null}
                                    onChange={handleTimeChange}
                                    disabled={submitStatus === "submitting"}
                                />
                                {/* if already submitted, we can see it */}
                                {isDuplicate && (
                                    <div className="duplicate-warning">Already submitted this shot!</div>
                                )}
                                {/* double verification, that we actually want to submit that */}
                                {selectedSubmitted ? (
                                    <button className="submit-btn success" disabled>
                                        Submitted ✓
                                    </button>
                                ) : !confirmSubmit ? (
                                    <button
                                        className={`submit-btn ${submitStatus || ""} ${isDuplicate ? "duplicate" : ""}`}
                                        onClick={() => {
                                            // a time we cannot parse must never reach DRES
                                            if (!timesValid) return;
                                            if (!submitStatus) setConfirmSubmit(true);
                                            if (submitStatus === "error") setConfirmSubmit(true);
                                        }}
                                        disabled={submitStatus === "submitting" || submitStatus === "success" || !timesValid}
                                    >
                                        {submitStatus === "submitting" ? "Submitting..."
                                            : submitStatus === "success" ? "Submitted!"
                                                : submitStatus === "error" ? "Error - try again?"
                                                    : !timesValid ? "Fix the time to submit"
                                                        : "Submit to DRES"}
                                    </button>
                                ) : (
                                    <div className="confirm-row">
                                        <button className="submit-btn confirm"
                                                onClick={() => handleSubmit(selectedResult, submitStartMs, submitEndMs)}>
                                            Yes, submit!
                                        </button>
                                        <button className="submit-btn cancel" onClick={() => setConfirmSubmit(false)}>
                                            Cancel
                                        </button>
                                    </div>
                                )}
                                <div className="action-button-row">
                                    <button className="exclude-btn" onClick={() => handleExcludeVideo(selectedResult)}>
                                        Exclude video from search
                                    </button>
                                    <button className="similar-btn" onClick={() => handleFindSimilar(selectedResult)}>
                                        Find similar
                                    </button>
                                </div>

                                {submitMessage && (
                                    <div className={`submit-result-box ${submitStatus || ""}`}>
                                        {submitMessage}
                                    </div>
                                )}
                            </div>
                            {/*  film tape under the video, every keyframe of the video */}
                            <ShotBrowser
                                videoId={selectedResult.video_id}
                                currentKeyframeId={selectedResult.keyframe_id}
                                onSelectShot={handleShotSelect}
                                apiUrl={API_URL}
                                onBrowseAll={handleBrowseAllShots}
                                submittedScenes={isAvs ? avsSubmittedScenes : EMPTY_SET}
                                coveredVideos={isAvs ? avsCoveredVideos : EMPTY_SET}
                            />
                        </>
                    ) : (
                        <div className="no-selection">Select a result to preview the video segment</div>
                    )}
                    {/* always visible - vqa and submissions*/}
                    <VqaAnswer
                        apiUrl={API_URL}
                        selectedResult={selectedResult}
                        startTimeMs={submitStartMs}
                        endTimeMs={submitEndMs}
                        onSubmitted={handleVqaSubmitted}
                    />
                    <SubmissionLog submissions={submissions}/>
                </div>
            </div>
        </div>
    );
}

export default App;