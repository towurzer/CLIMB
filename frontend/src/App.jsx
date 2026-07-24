// importing stuff - useState etc from react and components
import {useState, useCallback, useEffect} from "react";
import SearchBar from "./components/SearchBar";
import ResultsGrid from "./components/ResultsGrid";
import VideoPlayer from "./components/VideoPlayer";
import ShotBrowser from "./components/ShotBrowser";
import VideoBrowser from "./components/VideoBrowser";
import VqaAnswer from "./components/VqaAnswer";
import SubmissionLog from "./components/SubmissionLog";
import KeyframeTime from "./components/KeyframeTime";
import {describeDresResult, describeDresError} from "./dresSubmission";
import {formatTimecode, parseTimecode, snapMsToFrame, keyframeMs} from "./timecode";
import "./App.css";

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
    const [searchLoadingMore, setSearchLoadingMore] = useState(false);
    const [resultsPanel, setResultsPanel] = useState(null);
    const [searchSentinel, setSearchSentinel] = useState(null);
    const [dresUsername, setDresUsername] = useState("");
    const [dresPassword, setDresPassword] = useState("");
    const [dresConnected, setDresConnected] = useState(false);
    const [dresStatus, setDresStatus] = useState("Not connected");
    const [dresLoading, setDresLoading] = useState(false);
    const [snackbar, setSnackbar] = useState({visible: false, message: "", type: "info", raw: null});
    const [searchHistory, setSearchHistory] = useState([]);
    const [submissions, setSubmissions] = useState([]);
    const [excludedVideos, setExcludedVideos] = useState([]);
    const [similarSource, setSimilarSource] = useState(null);
    // null means "whatever the selected keyframe says", a string means the user typed over it
    const [timeOverride, setTimeOverride] = useState({from: null, to: null});

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
    }, [selectedResult?.video_id, selectedResult?.shot_id, selectedKeyframeMs]);

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
                s.shot_id === result.shot_id
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
                // where are we rn
                const currentIndex = selectedResult
                    ? results.findIndex(
                        (r) => r.video_id === selectedResult.video_id &&
                            r.shot_id === selectedResult.shot_id
                    )
                    : -1;
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
            const res = await fetch(
                `${API_URL}/climb/search?q=${encodeURIComponent(searchQuery)}&page=${page}&per_page=${searchPerPage}`
            );
            const data = await res.json();
            const pageResults = data.results || [];
            setResults((prev) => (append ? [...prev, ...pageResults] : pageResults));
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            return pageResults;
        } catch (err) {
            console.error("Search failed:", err);
            return [];
        }
    }, [searchPerPage]);

    const fetchSimilarPage = useCallback(async (videoId, shotId, excluded = [], page = 1, append = false) => {
        const params = new URLSearchParams({page, per_page: searchPerPage});
        if (excluded.length) params.set("exclude", excluded.join(","));
        try {
            const res = await fetch(
                `${API_URL}/climb/videos/${videoId}/${shotId}/similar?${params}`
            );
            const data = await res.json();
            const pageResults = data.results || [];
            setResults((prev) => (append ? [...prev, ...pageResults] : pageResults));
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            return pageResults;
        } catch (err) {
            console.error("Similar search failed:", err);
            return [];
        }
    }, [searchPerPage]);

    // "Similar to X / shot Y" plus any active exclusions - shown in the results header
    const similarLabel = (videoId, shotId, excluded = []) => {
        const base = `Similar to ${videoId} / shot ${shotId}`;
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
        setExcludedVideos([]);
        setSimilarSource(null);
        setSearchHistory((prev) => {
            const filtered = prev.filter((q) => q !== searchQuery);
            return [searchQuery, ...filtered].slice(0, 10);
        });

        await fetchSearchPage(searchQuery, 1, false);
        setLoading(false);
    }, [fetchSearchPage]);

    // Find similar refines what we are already looking at, so exclusions carry over
    const handleFindSimilar = useCallback(async (result) => {
        setLoading(true);
        setMode("search");
        setQuery(similarLabel(result.video_id, result.shot_id, excludedVideos));
        setSimilarSource({video_id: result.video_id, shot_id: result.shot_id});
        setSelectedResult(null);
        setConfirmSubmit(false);
        try {
            await fetchSimilarPage(result.video_id, result.shot_id, excludedVideos);
        } finally {
            setLoading(false);
        }
    }, [excludedVideos, fetchSimilarPage]);

    // Exclude video from search
    const handleExcludeVideo = useCallback(async (result) => {
        const baseQuery = query.split(" --exclude:")[0].trim();
        // nothing has been searched yet, so there is no result set to exclude from - ignore it
        if (!baseQuery) return;

        const updatedExcluded = [...new Set([...excludedVideos, result.video_id])];
        setExcludedVideos(updatedExcluded);

        // re-run whichever kind of search produced the current results (similarity search / text search)
        const isSimilar = Boolean(similarSource);
        const updatedQuery = isSimilar
            ? similarLabel(similarSource.video_id, similarSource.shot_id, updatedExcluded)
            : `${baseQuery} --exclude: ${updatedExcluded.join(", ")}`;

        // can be triggered from the browse tab, so jump back to the results we just refreshed
        setMode("search");
        setQuery(updatedQuery);
        setLoading(true);
        setSelectedResult(null);
        setConfirmSubmit(false);
        setSearchHasMore(false);
        setSearchPage(1);
        try {
            if (isSimilar) {
                await fetchSimilarPage(similarSource.video_id, similarSource.shot_id, updatedExcluded);
            } else {
                await fetchSearchPage(updatedQuery, 1, false);
            }
        } catch (err) {
            console.error("Search with exclude failed:", err);
        } finally {
            setLoading(false);
        }
    }, [query, excludedVideos, similarSource, fetchSearchPage, fetchSimilarPage]);

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
                setDresStatus(msg);
                setSnackbar({visible: true, message: msg, type: "error", raw: dresErr});
                setTimeout(() => setSnackbar((s) => ({...s, visible: false, raw: null})), 7000);
                return;
            }
            setDresConnected(true);
            setDresStatus(data.message || "Connected to DRES");
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
            const errMsg = err?.message || "DRES login failed";
            setDresStatus(errMsg);
            setSnackbar({visible: true, message: errMsg, type: "error", raw: null});
            setTimeout(() => setSnackbar((s) => ({...s, visible: false})), 7000);
        } finally {
            setDresLoading(false);
        }
    }, [dresUrl, dresUsername, dresPassword, dresName]);

    // Shot select from filmstrip - the one under video - that is why we can use the same video id
    const handleShotSelect = useCallback((shot) => {
        // of something was submitted - reseting it
        setConfirmSubmit(false);
        setSubmitStatus(null);
        setSelectedResult((prev) => {
            // 25 is just a backup becuase it is most common
            const fps = shot.fps || prev?.fps || 25;
            return {
                video_id: prev?.video_id || shot.video_id,
                shot_id: shot.shot_id,
                score: prev?.score || 0,
                middle_frame: shot.middle_frame,
                start_frame: shot.start_frame,
                end_frame: shot.end_frame,
                fps: fps,
                // we need to recalculate, because DRES is usinf ms not frames.
                // these are the bounds of the whole scene, what we submit comes from the keyframe time fields
                start_time_ms: Math.round((shot.start_frame / fps) * 1000),
                end_time_ms: Math.round((shot.end_frame / fps) * 1000),
                thumbnail_url: shot.thumbnail_url,
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
            video_id: result.video_id,
            shot_id: result.shot_id,
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
                }),
            });
            const data = await res.json();
            const {state, text} = res.ok
                ? describeDresResult(data)
                : describeDresError(data, "DRES Submission failed");
            entry.status = state;
            setSubmitStatus(state);
            setSubmitMessage(text);
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
    }, []);

    //  VQA submit callback, mode is "media" (text + shot) or "text" (text only)
    const handleVqaSubmitted = useCallback((answer, status, mode) => {
        setSubmissions((prev) => [
            {type: "vqa", text_answer: answer, time: timeNow(), status, mode},
            ...prev,
        ]);
    }, []);

    // Select result
    const handleSelect = useCallback((result) => {
        console.log("handleSelect middle_frame:", result.middle_frame);
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
                await fetchSimilarPage(similarSource.video_id, similarSource.shot_id, excludedVideos, nextPage, true);
            } else {
                await fetchSearchPage(query, nextPage, true);
            }
        } catch (err) {
            console.error("Load more search results failed:", err);
        } finally {
            setSearchLoadingMore(false);
        }
    }, [searchHasMore, loading, searchLoadingMore, searchPage, query, similarSource, excludedVideos, fetchSearchPage, fetchSimilarPage]);

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
    const handleBrowseAllShots = useCallback((videoId) => {
        setMode("browse");
        setBrowseOpenVideoId(videoId);
    }, []);
    const handleBrowseOpenVideoHandled = useCallback(() => {
        setBrowseOpenVideoId(null);
    }, []);

    const isDuplicate = selectedResult ? alreadySubmitted(selectedResult) : false;

    return (
        <div className="app">
            {/* for the top top bar */}
            <header className="app-header">
                <div className="app-header-left">
                    <h1>
                        CLIMB, a Content Localization system and Intelligent Multimedia Browser
                        <br/>
                        Made with love from the <a href="https://www.aau.at">AAU</a> students <a href="https://github.com/towurzer">Wurzer</a> and <a
                        href="https://github.com/sesnr">Eisner</a>.
                    </h1>
                </div>

                <div className="app-header-right">
                    <div className="dres-login-panel">
                        <input
                            type="text"
                            value={dresUrl}
                            onChange={(e) => setDresUrl(e.target.value)}
                            placeholder="DRES URL"
                            title="DRES server URL"
                        />
                        <input
                            type="text"
                            value={dresName}
                            onChange={(e) => setDresName(e.target.value)}
                            placeholder="DRES name (e.g. IVADL26)"
                            title="DRES evaluation name"
                        />
                        <input
                            type="text"
                            value={dresUsername}
                            onChange={(e) => setDresUsername(e.target.value)}
                            placeholder="Username"
                            title="DRES username"
                        />
                        <input
                            type="password"
                            value={dresPassword}
                            onChange={(e) => setDresPassword(e.target.value)}
                            placeholder="Password"
                            title="DRES password"
                        />
                        <button className="dres-login-btn" onClick={handleDresLogin} disabled={dresLoading}>
                            {dresLoading ? "Connecting..." : dresConnected ? "Reconnect DRES" : "DRES Login"}
                        </button>
                        <span className={`dres-status ${dresConnected ? "connected" : ""}`}>{dresStatus}</span>
                    </div>
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
                        <button className={`mode-btn ${mode === "search" ? "active" : ""}`}
                                onClick={() => setMode("search")}>Search
                        </button>
                        <button className={`mode-btn ${mode === "browse" ? "active" : ""}`}
                                onClick={() => setMode("browse")}>Browse
                        </button>
                    </div>
                </div>
            </header>
            {/* if statement, because for browsing we dont need it */}
            {mode === "search" && (
                <SearchBar onSearch={handleSearch} loading={loading} history={searchHistory}/>
            )}

            <div className="main-content">
                <div className="results-panel" ref={setResultsPanel}>
                    {/* difference between search and browse mode */}
                    {mode === "search" ? (
                            <>
                                {loading && <div className="loading">Searching...</div>}
                                {!loading && results.length > 0 && (
                                    <div className="results-info">
                                        {results.length}{searchHasMore ? "+" : ""} results for "{query}"
                                        <span className="shortcuts-hint">← → navigate · Esc deselect · Ctrl+K search</span>
                                    </div>
                                )}
                                <ResultsGrid
                                    results={results}
                                    selectedResult={selectedResult}
                                    onSelect={handleSelect}
                                    onFindSimilar={handleFindSimilar}
                                    onExcludeVideo={handleExcludeVideo}
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
                            />
                        )}
                </div>
                {/* right pannel , if nothing selected then dyplaying nothing */}
                <div className="player-panel">
                    {selectedResult ? (
                        <>
                            <div className="result-details">
                                <span>Video: {selectedResult.video_id}</span>
                                <span>Shot: {selectedResult.shot_id}</span>
                                <span>Score: {(selectedResult.score * 100).toFixed(1)}%</span>
                                <span>Scene: {formatTimecode(selectedResult.start_time_ms)} – {formatTimecode(selectedResult.end_time_ms)}</span>
                                <span>Keyframe: {autoTimecode || "unknown"}</span>
                            </div>
                            <VideoPlayer result={selectedResult} apiUrl={API_URL}/>
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
                                {!confirmSubmit ? (
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
                            {/*  film tape under the video */}
                            <ShotBrowser
                                videoId={selectedResult.video_id}
                                currentShotId={selectedResult.shot_id}
                                onSelectShot={handleShotSelect}
                                apiUrl={API_URL}
                                onBrowseAll={handleBrowseAllShots}
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