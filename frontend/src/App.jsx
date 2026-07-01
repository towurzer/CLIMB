// importing stuff - useState etc from react and components
import { useState, useCallback, useEffect } from "react";
import SearchBar from "./components/SearchBar";
import ResultsGrid from "./components/ResultsGrid";
import VideoPlayer from "./components/VideoPlayer";
import ShotBrowser from "./components/ShotBrowser";
import VideoBrowser from "./components/VideoBrowser";
import VqaAnswer from "./components/VqaAnswer";
import TaskTimer from "./components/TaskTimer";
import SubmissionLog from "./components/SubmissionLog";
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
  const [dresUsername, setDresUsername] = useState("");
  const [dresPassword, setDresPassword] = useState("");
  const [dresConnected, setDresConnected] = useState(false);
  const [dresStatus, setDresStatus] = useState("Not connected");
  const [dresLoading, setDresLoading] = useState(false);
  const [searchHistory, setSearchHistory] = useState([]);
  const [submissions, setSubmissions] = useState([]);

  // get current time string
  const timeNow = () => new Date().toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

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
  const handleSearch = useCallback(async (searchQuery) => {
    // trimming empty spaces - it just that, returning nothing
    if (!searchQuery.trim()) return;
    // reset of everything before calling
    setMode("search");
    setQuery(searchQuery);
    setLoading(true);
    setSelectedResult(null);
    setSubmitStatus(null);
    setConfirmSubmit(false);
    // adding this into our history - prev is our array of previous, removing duplicit stuff, max 10 
    setSearchHistory((prev) => {
      const filtered = prev.filter((q) => q !== searchQuery);
      return [searchQuery, ...filtered].slice(0, 10);
    });
    // try for preventing errors - sending request to the backend and waiting for a response
    try {
      const res = await fetch(`${API_URL}/climb/search?q=${encodeURIComponent(searchQuery)}`);
      // answer again in javascript object
      const data = await res.json();
      console.log("first result:", data.results[0]);
      // displaying results
      setResults(data.results || []);
    } catch (err) {
      console.error("Search failed:", err);
    } finally {
      // always end searching
      setLoading(false);
    }
  }, []);

  // Find similar
  const handleFindSimilar = useCallback(async (result) => {
    setLoading(true);
    setMode("search");
    setQuery(`Similar to ${result.video_id} / shot ${result.shot_id}`);
    setSelectedResult(null);
    setConfirmSubmit(false);
    try {
      // getting it from the backend 
      const res = await fetch(`${API_URL}/climb/videos/${result.video_id}/${result.shot_id}/similar`);
      const data = await res.json();
      setResults(data.results || []);
    } catch (err) {
      console.error("Similar search failed:", err);
    } finally {
      setLoading(false);
    }
  }, []);

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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: dresUsername,
          password: dresPassword,
          dres_url: dresUrl,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || data.details || "Failed to connect to DRES");
      }
      setDresConnected(true);
      setDresStatus(data.message || "Connected to DRES");
    } catch (err) {
      console.error("DRES login failed:", err);
      setDresConnected(false);
      setDresStatus(err.message || "DRES login failed");
    } finally {
      setDresLoading(false);
    }
  }, [dresUrl, dresUsername, dresPassword]);

  // Shot select from filmstrip - the one under video - that is why we can use the same video id 
  const handleShotSelect = useCallback((shot) => {
    // of something was submitted - reseting it
    setConfirmSubmit(false);
    setSubmitStatus(null);
    setSelectedResult((prev) => ({
      video_id: prev?.video_id || shot.video_id,
      shot_id: shot.shot_id,
      score: prev?.score || 0,
      middle_frame: shot.middle_frame,
      start_frame: shot.start_frame,
      end_frame: shot.end_frame,

      // 25 is just a backup becuase it is most common
      fps: shot.fps || prev?.fps || 25,
      // we need to recalculate, because DRES is usinf ms not frames
      // use middle frame to set start and end time since start frame and end frame are the starting and end points of the whole scen
      // we only want the starting frame of the shot currently selected by the user (called middle frame due to legacy code)
      start_time_ms: shot.middle_frame ? Math.round((shot.middle_frame / (shot.fps || prev?.fps || 25)) * 1000) : 0,
      end_time_ms: shot.middle_frame ? Math.round((shot.middle_frame / (shot.fps || prev?.fps || 25)) * 1000) : 0,
      thumbnail_url: shot.thumbnail_url,
    }));
  }, []);

  //Submit to DRES  - calles after clicking on submit button
  const handleSubmit = useCallback(async (result) => {
    setSubmitStatus("submitting");
    setSubmitMessage("Submitting to DRES...");
    setConfirmSubmit(false);
    // what is submitted
    const entry = {
      type: "segment",
      video_id: result.video_id,
      shot_id: result.shot_id,
      start_time_ms: result.start_time_ms,
      end_time_ms: result.end_time_ms,
      time: timeNow(),
      status: "submitting",
    };

    // send data to backend
    try {
      const res = await fetch(`${API_URL}/climb/dres/submit/kis`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: result.video_id,
          start_time_ms: result.start_time_ms,
          end_time_ms: result.end_time_ms,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const errorText = data.error || data.message || "DRES Submission failed";
        const detailsText = data.details ? ` ${data.details}` : "";
        throw new Error(`${errorText}${detailsText}`);
      }
      const submissionState = data.dres_response?.submission;
      const submissionDetails = data.dres_response?.description || data.message || "Submitted successfully.";
      if (submissionState === "CORRECT") {
        entry.status = "success";
        setSubmitStatus("success");
        setSubmitMessage(submissionDetails);
      } else {
        const errorText = submissionState ? `DRES Submission failed: ${submissionDetails}` : (data.message || "DRES Submission failed.");
        entry.status = "error";
        setSubmitStatus("error");
        setSubmitMessage(errorText);
      }
    } catch (err) {
      // it did not work
      console.error("DRES submit failed:", err);
      entry.status = "error";
      setSubmitStatus("error");
      setSubmitMessage(`DRES Submission failed: ${err.message || "Please check DRES connection."}`);
    }
    // for our submitted array
    setSubmissions((prev) => [entry, ...prev]);
  }, []);

  //  VQA submit callback 
  const handleVqaSubmitted = useCallback((answer, status) => {
    setSubmissions((prev) => [
      { type: "vqa", text_answer: answer, time: timeNow(), status },
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

  const handleBrowseSelect = useCallback((result) => {
    setSelectedResult(result);
    setConfirmSubmit(false);
    setSubmitStatus(null);
    setSubmitMessage("");
  }, []);

  const isDuplicate = selectedResult ? alreadySubmitted(selectedResult) : false;

  return (
    <div className="app">
      {/* for the top top bar */}
      <header className="app-header">
        <div className="app-header-left">
          <h1>CLIMB - Wurzer, Eisner, Hraničková </h1>
          <TaskTimer />
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
          <div className="mode-toggle">
            <button className={`mode-btn ${mode === "search" ? "active" : ""}`} onClick={() => setMode("search")}>Search</button>
            <button className={`mode-btn ${mode === "browse" ? "active" : ""}`} onClick={() => setMode("browse")}>Browse</button>
          </div>
        </div>
      </header>
      {/* if statement, because for browsing we dont need it */}
      {mode === "search" && (
        <SearchBar onSearch={handleSearch} loading={loading} history={searchHistory} />
      )}

      <div className="main-content">
        <div className="results-panel">
          {/* difference between search and browse mode */}
          {mode === "search" ? (
            <>
              {/* if it is loading, we display searching, if not and we hade results, we display results, if nothinf, display nothing */}
              {loading && <div className="loading">Searching...</div>}
              {!loading && results.length > 0 && (
                <div className="results-info">
                  {results.length} results for "{query}"
                  <span className="shortcuts-hint">← → navigate · Esc deselect · Ctrl+K search</span>
                </div>
              )}
              {/* selecting results, if we clicked on smth, we handle it, if we click on the arrow we find similar */}
              <ResultsGrid
                results={results}
                selectedResult={selectedResult}
                onSelect={handleSelect}
                onFindSimilar={handleFindSimilar}
              />
            </>
          ) :
            (
              /* for browsing - showing everything */
              <VideoBrowser apiUrl={API_URL} onSelectShot={handleBrowseSelect} />
            )}
        </div>
        {/* right pannel , if nothing selected then dyplaying nothing */}
        <div className="player-panel">
          {selectedResult ? (
            <>
              <VideoPlayer result={selectedResult} apiUrl={API_URL} />
              <div className="actions">
                {/* if already submitted, we can see it */}
                {isDuplicate && (
                  <div className="duplicate-warning">Already submitted this shot!</div>
                )}
                {/* double verification, that we actually want to submit that */}
                {!confirmSubmit ? (
                  <button
                    className={`submit-btn ${submitStatus || ""} ${isDuplicate ? "duplicate" : ""}`}
                    onClick={() => {
                      if (!submitStatus) setConfirmSubmit(true);
                      if (submitStatus === "error") setConfirmSubmit(true);
                    }}
                    disabled={submitStatus === "submitting" || submitStatus === "success"}
                  >
                    {submitStatus === "submitting" ? "Submitting..."
                      : submitStatus === "success" ? "Submitted!"
                        : submitStatus === "error" ? "Error - try again?"
                          : "Submit to DRES"}
                  </button>
                ) : (
                  <div className="confirm-row">
                    <button className="submit-btn confirm" onClick={() => handleSubmit(selectedResult)}>
                      Yes, submit!
                    </button>
                    <button className="submit-btn cancel" onClick={() => setConfirmSubmit(false)}>
                      Cancel
                    </button>
                  </div>
                )}
                {/* same as the arriw before, just finging similar */}
                <button className="similar-btn" onClick={() => handleFindSimilar(selectedResult)}>
                  Find similar
                </button>

                {submitMessage && (
                  <div className={`submit-result-box ${submitStatus || ""}`}>
                    {submitMessage}
                  </div>
                )}

                <div className="result-details">
                  <span>Video: {selectedResult.video_id}</span>
                  <span>Shot: {selectedResult.shot_id}</span>
                  <span>Score: {(selectedResult.score * 100).toFixed(1)}%</span>
                  <span>Time: {(selectedResult.start_time_ms / 1000).toFixed(1)}s – {(selectedResult.end_time_ms / 1000).toFixed(1)}s</span>
                </div>
              </div>
              {/*  film tape under the video */}
              <ShotBrowser
                videoId={selectedResult.video_id}
                currentShotId={selectedResult.shot_id}
                onSelectShot={handleShotSelect}
                apiUrl={API_URL}
              />
            </>
          ) : (
            <div className="no-selection">Select a result to preview the video segment</div>
          )}
          {/* always visible - vqa and submissions*/}
          <VqaAnswer apiUrl={API_URL} selectedResult={selectedResult} onSubmitted={handleVqaSubmitted} />
          <SubmissionLog submissions={submissions} />
        </div>
      </div>
    </div>
  );
}

export default App;