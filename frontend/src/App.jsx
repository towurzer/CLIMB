import {useState, useCallback, useEffect, useLayoutEffect, useRef} from "react";
import {ALL_SOURCE_KEYS, COLLECTIONS, sourceKeysFor, sourcesFor, startingSources} from "./sources";
import {digitOf, DOUBLE_PRESS_MS, keepsTheKeys, letterOf, MODIFIER_KEYS, neighbourCard, neighbourStage} from "./shortcuts";
import SearchBar from "./components/SearchBar";
import ResultsGrid from "./components/ResultsGrid";
import VideoPlayer from "./components/VideoPlayer";
import ShotBrowser from "./components/ShotBrowser";
import {clipLabel, landingInClip, neighbourClips, neighbourKeyframe, useRecording} from "./useRecording";
import VideoBrowser from "./components/VideoBrowser";
import VqaAnswer from "./components/VqaAnswer";
import SubmissionLog from "./components/SubmissionLog";
import KeyframeTime from "./components/KeyframeTime";
import AvsSessionBar from "./components/AvsSessionBar";
import DresLoginModal from "./components/DresLoginModal";
import TeamLoginModal from "./components/TeamLoginModal";
import TeamInbox from "./components/TeamInbox";
import HintToast from "./components/HintToast";
import SendHint from "./components/SendHint";
import {useTeam} from "./useTeam";
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
// Tells the search worker whose background search a new one replaces, so two tabs do not stop each other.
const WIDEN_CLIENT = Math.random().toString(36).slice(2);

// A query with `-video:ID` added, unless it already has it.
const withExclusion = (text, token) =>
    (text.split(/\s+/).includes(token) ? text.trim() : `${text.trim()} ${token}`.trim());

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
    // What is typed in the search bar. Lives here because the bar is gone while browsing, and
    // `query` is no use for it: find similar and exclude write their own labels into that.
    const [searchInput, setSearchInput] = useState("");
    const [searchFocusRequested, setSearchFocusRequested] = useState(false);
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
    const [collection, setCollection] = useState(""); // "" = every dataset
    const [sources, setSources] = useState(() => startingSources("", "kis", ALL_SOURCE_KEYS));
    // Each text:/said:/tag:/video: requirement and how many videos met it; null without any.
    const [searchFilters, setSearchFilters] = useState(null);
    // The depth a sequence's background search is working on; null when none is running.
    const [widening, setWidening] = useState(null);
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

    // The team this stack is connected to: its AVS session and the hints teammates send
    const teamApi = useTeam(API_URL);
    const {team} = teamApi;
    const [teamLoginOpen, setTeamLoginOpen] = useState(false);

    const avsFilterRef = useRef({mode: "kis", code: null});
    useEffect(() => {
        avsFilterRef.current = {mode: taskMode, code: avsSession?.code || null};
    }, [taskMode, avsSession]);

    const sourcesRef = useRef(sources);
    useEffect(() => {
        sourcesRef.current = sources;
    }, [sources]);

    const collectionRef = useRef(collection);
    useEffect(() => {
        collectionRef.current = collection;
    }, [collection]);

    const excludedRef = useRef(excludedVideos);
    useEffect(() => {
        excludedRef.current = excludedVideos;
    }, [excludedVideos]);

    const resultsRef = useRef(results);
    useEffect(() => {
        resultsRef.current = results;
    }, [results]);

    // Set by VideoBrowser while the fullscreen keyframes are open: {back, videoId}.
    const shotView = useRef(null);
    const pendingEsc = useRef(null);
    const lastSpace = useRef(0);
    // Where the results were scrolled to. Their panel also holds Browse, whose content resets it.
    const resultsScroll = useRef(0);
    // The card the selection last sat on, per grid. A and D can walk the selection off the grid,
    // the arrow keys then carry on from here.
    const lastCard = useRef({});
    // What was selected when the fullscreen keyframes were opened from the results.
    const beforeBrowse = useRef(null);
    // The current run of A and D: the selection it started from, and the keyframe it is on now.
    const keyframeWalk = useRef(null);

    // Which result set is on screen. A response for an older one is dropped, however late it lands.
    const searchGeneration = useRef(0);
    const widenAbort = useRef(null);

    const isAvs = taskMode === "avs";

    // The selected video's recording: every clip of an MVK dive, for the filmstrip and clip buttons
    const recording = useRecording(selectedResult?.video_id, API_URL);
    const {previous: previousClip, next: nextClip} = neighbourClips(recording.clips, selectedResult?.video_id);

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

    // A new result set: whatever is still running for the old one stops counting.
    const startResultSet = useCallback(() => {
        widenAbort.current?.abort();
        widenAbort.current = null;
        setWidening(null);
        // What A and D started from and what was selected before Browse may be gone now, excluded
        // for one.
        keyframeWalk.current = null;
        beforeBrowse.current = null;
        searchGeneration.current += 1;
        return searchGeneration.current;
    }, []);

    // A sequence's background search, one depth after the other until there is none left or a new
    // result set starts. What it finds is appended behind the cached results and only reaches the
    // grid through the usual "load more", so nothing on screen moves.
    const widenSearch = useCallback(async (params, firstDepth, generation) => {
        const controller = new AbortController();
        widenAbort.current = controller;
        let depth = firstDepth;
        try {
            while (depth != null) {
                setWidening(depth);
                const res = await fetch(`${API_URL}/climb/search/widen?${params}&client=${WIDEN_CLIENT}`,
                    {signal: controller.signal});
                if (!res.ok) throw new Error(`widen answered ${res.status}`);
                const data = await res.json();
                if (generation !== searchGeneration.current || data.cancelled) return;
                setSearchHasMore(data.total > resultsRef.current.length);
                depth = data.next_depth;
            }
        } catch (err) {
            if (err.name !== "AbortError") console.error("Background search failed:", err);
        }
        if (generation === searchGeneration.current) setWidening(null);
    }, []);

    // searchiing - asynchornous - waiting for the response from the server
    const fetchSearchPage = useCallback(async (searchQuery, page = 1, append = false) => {
        const generation = append ? searchGeneration.current : startResultSet();
        try {
            // In AVS mode the backend hides scenes already submitted in our session
            const {mode, code} = avsFilterRef.current;
            const avsParam = mode === "avs" && code ? `&avs_session=${code}` : "";
            // Omitted when everything this dataset shows is ticked, which the backend already reads
            // as "all sources".
            const picked = sourcesRef.current;
            const sourcesParam = picked.length < sourceKeysFor(collectionRef.current).length
                ? `&sources=${picked.join(",")}`
                : "";

            const excluded = excludedRef.current;
            const excludeParam = excluded.length ? `&exclude=${excluded.join(",")}` : "";
            const collectionParam = collectionRef.current ? `&collection=${collectionRef.current}` : "";
            const params = `q=${encodeURIComponent(searchQuery)}${avsParam}${sourcesParam}${excludeParam}${collectionParam}`;
            // By offset, not page: a short last page can grow, and the next page would skip that.
            const position = append ? `&offset=${resultsRef.current.length}` : "";
            const res = await fetch(
                `${API_URL}/climb/search?${params}&page=${page}&per_page=${searchPerPage}${position}`
            );
            const data = await res.json();
            if (generation !== searchGeneration.current) return [];
            const pageResults = data.results || [];
            setResults((prev) => {
                if (!append) return pageResults;
                const shown = new Set(prev.map((r) => r.keyframe_id));
                return [...prev, ...pageResults.filter((r) => !shown.has(r.keyframe_id))];
            });
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            // Null unless the query used `A >> B`; describes how it was understood.
            setSearchTemporal(data.temporal || null);
            setSearchFilters(data.filters || null);
            if (!append && data.widen?.next_depth != null) {
                widenSearch(params, data.widen.next_depth, generation);
            }
            return pageResults;
        } catch (err) {
            console.error("Search failed:", err);
            return [];
        }
    }, [searchPerPage, startResultSet, widenSearch]);

    const fetchSimilarPage = useCallback(async (keyframeId, excluded = [], page = 1, append = false) => {
        const generation = append ? searchGeneration.current : startResultSet();
        const params = new URLSearchParams({page, per_page: searchPerPage});
        if (excluded.length) params.set("exclude", excluded.join(","));
        if (collectionRef.current) params.set("collection", collectionRef.current);
        try {
            // Find-similar lives under /search now: it is fused, cached and paged like a search.
            const res = await fetch(
                `${API_URL}/climb/search/similar/${keyframeId}?${params}`
            );
            const data = await res.json();
            if (generation !== searchGeneration.current) return [];
            const pageResults = data.results || [];
            setResults((prev) => (append ? [...prev, ...pageResults] : pageResults));
            setSearchPage(page);
            setSearchHasMore(Boolean(data.has_more));
            setSearchTemporal(null); // find-similar replaces the result set, sequence or not
            setSearchFilters(null);
            return pageResults;
        } catch (err) {
            console.error("Similar search failed:", err);
            return [];
        }
    }, [searchPerPage, startResultSet]);

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
        // The query says what is excluded, and a find similar from these results carries that over.
        const excluded = [...searchQuery.matchAll(/(?:^|\s)-video:([A-Za-z0-9_-]+)/g)].map((match) => match[1]);
        excludedRef.current = excluded;
        setExcludedVideos(excluded);
        setSimilarSource(null);
        setSearchHistory((prev) => {
            const filtered = prev.filter((q) => q !== searchQuery);
            return [searchQuery, ...filtered].slice(0, 10);
        });

        await fetchSearchPage(searchQuery, 1, false);
        setLoading(false);
    }, [fetchSearchPage]);

    // Ticking a source changes the result set, so the query re-runs on the spot
    const handleToggleSource = useCallback((key, rerun = true) => {
        const next = sources.includes(key)
            ? sources.filter((source) => source !== key)
            : [...sources, key];

        if (!next.length) return;

        setSources(next);

        sourcesRef.current = next;

        if (!rerun || mode !== "search" || similarSource || !query.trim()) return;

        (async () => {
            setLoading(true);
            setSelectedResult(null);
            setSearchPage(1);
            setSearchHasMore(false);
            await fetchSearchPage(query, 1, false);
            setLoading(false);
        })();
    }, [sources, mode, similarSource, query, fetchSearchPage]);

    // Switching dataset changes the result set, so whatever is on screen re-runs against it
    const handleChangeCollection = useCallback((next, rerun = true) => {
        setCollection(next);
        collectionRef.current = next;
        const nextSources = startingSources(next, taskMode, sourcesRef.current);
        setSources(nextSources);
        sourcesRef.current = nextSources;

        if (!rerun || mode !== "search") return;
        (async () => {
            setLoading(true);
            setSelectedResult(null);
            setSearchPage(1);
            setSearchHasMore(false);
            if (similarSource) {
                await fetchSimilarPage(similarSource.keyframe_id, excludedRef.current, 1, false);
            } else if (query.trim()) {
                await fetchSearchPage(query, 1, false);
            }
            setLoading(false);
        })();
    }, [mode, taskMode, similarSource, query, fetchSearchPage, fetchSimilarPage]);

    // KIS and AVS start with different sources ticked (OFF_FOR in sources.js), so switching task type
    // changes the result set and whatever is on screen re-runs, as for a dataset switch
    const handleChangeTaskMode = useCallback((next) => {
        if (next === taskMode) return;
        setTaskMode(next);
        avsFilterRef.current = {...avsFilterRef.current, mode: next};
        const nextSources = startingSources(collectionRef.current, next, sourcesRef.current);
        setSources(nextSources);
        sourcesRef.current = nextSources;

        if (mode !== "search") return;
        (async () => {
            setLoading(true);
            setSelectedResult(null);
            setSearchPage(1);
            setSearchHasMore(false);
            if (similarSource) {
                await fetchSimilarPage(similarSource.keyframe_id, excludedRef.current, 1, false);
            } else if (query.trim()) {
                await fetchSearchPage(query, 1, false);
            }
            setLoading(false);
        })();
    }, [taskMode, mode, similarSource, query, fetchSearchPage, fetchSimilarPage]);

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

    // Exclude video from search. A text search gets it written into the query and the bar: it shows
    // there, it survives amending the query, and deleting it there brings the video back.
    const handleExcludeVideo = useCallback(async (result) => {
        beforeBrowse.current = null;
        const updatedExcluded = [...new Set([...excludedRef.current, result.video_id])];
        excludedRef.current = updatedExcluded;
        setExcludedVideos(updatedExcluded);

        const isSimilar = Boolean(similarSource);
        const token = `-video:${result.video_id}`;
        // The space: Tab then starts a new filter instead of turning this one into the next.
        if (!isSimilar) setSearchInput((text) => `${withExclusion(text, token)} `);

        if (!query.trim()) {
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
        const updatedQuery = isSimilar
            ? similarLabel(similarSource.video_id, similarSource.keyframe_id, updatedExcluded)
            : withExclusion(query, token);

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
        if (team) {
            await teamApi.newSession();
            return;
        }
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
    }, [dresName, applyAvsSession, team, teamApi.newSession]);

    // In a team, the AVS session is the team's: every team poll brings it along. Leaving the team
    // (or the team expiring) leaves its session too.
    const wasInTeam = useRef(false);
    useEffect(() => {
        if (team?.session) {
            wasInTeam.current = true;
            applyAvsSession({...team.session, collab: team.collab});
        } else if (wasInTeam.current) {
            wasInTeam.current = false;
            setAvsSession(null);
            setAvsSubmittedScenes(EMPTY_SET);
            setAvsCoveredVideos(EMPTY_SET);
        }
    }, [team, applyAvsSession]);

    useEffect(() => {
        if (!teamApi.lostTeam) return;
        setSnackbar({visible: true, message: `${teamApi.lostTeam} expired or was reset. Join it again.`, type: "error", raw: null});
        const id = setTimeout(() => setSnackbar((s) => ({...s, visible: false})), 7000);
        return () => clearTimeout(id);
    }, [teamApi.lostTeam]);

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
        if (!isAvs || !avsSession?.code || team) return;
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
    }, [isAvs, avsSession?.code, Boolean(team)]);

    // Shot select from filmstrip - the one under video. It spans the whole recording, so the
    // scene says which clip it is in
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
                video_id: shot.video_id || prev?.video_id,
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

    // Prev / next clip of an MVK dive: selects that clip, which is then what gets submitted
    const handleStepClip = useCallback((clip, forward) => {
        const landing = landingInClip(recording.scenes, clip.video_id, forward);
        if (landing) handleShotSelect(landing.scene, landing.keyframe);
    }, [recording.scenes, handleShotSelect]);

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

    // First step of a submission, from the button or Ctrl+Enter: asks "Yes, submit!".
    const requestSubmit = () => {
        // a time we cannot parse must never reach DRES
        if (!timesValid) return;
        if (!submitStatus || submitStatus === "error") setConfirmSubmit(true);
    };

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
    // The keyframe the shot view opens on (scrolled to the middle); null opens at the top
    const [browseFocusKeyframeId, setBrowseFocusKeyframeId] = useState(null);
    // Where "Browse in Fullscreen" was fired from. Set only when the jump crossed modes, so the
    // shot view's back arrow returns to the results the user came from instead of the video grid.
    const [browseReturnMode, setBrowseReturnMode] = useState(null);
    const handleBrowseAllShots = useCallback((videoId, keyframeId = null, selection = null) => {
        // Re-triggering from inside browse keeps the original origin: the user never left it.
        if (mode !== "browse") {
            setBrowseReturnMode(mode);
            beforeBrowse.current = selection;
        }
        setMode("browse");
        setBrowseFocusKeyframeId(keyframeId);
        setBrowseOpenVideoId(videoId);
    }, [mode]);
    const handleBrowseOpenVideoHandled = useCallback(() => {
        setBrowseOpenVideoId(null);
    }, []);
    // Leaving before the shots have loaded cancels that load, so it never reports back as handled.
    // Without this the next visit to Browse would open that video again.
    useEffect(() => {
        if (mode !== "browse") setBrowseOpenVideoId(null);
    }, [mode]);
    // A card's button: what clicking the card and then "Browse in Fullscreen" does.
    const handleOpenKeyframes = useCallback((result) => {
        handleSelect(result);
        handleBrowseAllShots(result.video_id, result.keyframe_id, result);
    }, [handleSelect, handleBrowseAllShots]);
    const handleBrowseExit = useCallback(() => {
        setMode(browseReturnMode || "search");
        setBrowseReturnMode(null);
        // Whatever was clicked in there, the results come back as they were left.
        if (beforeBrowse.current) handleSelect(beforeBrowse.current);
        beforeBrowse.current = null;
    }, [browseReturnMode, handleSelect]);
    const switchMode = useCallback((next) => {
        setBrowseReturnMode(null);
        beforeBrowse.current = null;
        setMode(next);
    }, []);

    // A hint opens the fullscreen keyframe browser of its video (for MVK: the dive) with the sent
    // keyframe selected, or nothing selected when only the video was sent.
    const openHint = useCallback(async (hint) => {
        const selectedBefore = selectedResult;
        let selected = false;
        if (hint.keyframe_id != null) {
            try {
                const res = await fetch(`${API_URL}/climb/videos/${encodeURIComponent(hint.video_id)}/scenes`);
                const data = await res.json();
                for (const scene of data.scenes || []) {
                    const keyframe = (scene.keyframes || []).find((k) => k.keyframe_id === hint.keyframe_id);
                    if (keyframe) {
                        setSelectedResult(null);
                        handleShotSelect(scene, keyframe);
                        selected = true;
                        break;
                    }
                }
            } catch (err) {
                console.error("Opening the hint's keyframe failed:", err);
            }
        }
        if (!selected) setSelectedResult(null);
        handleBrowseAllShots(hint.video_id, selected ? hint.keyframe_id : null, selectedBefore);
    }, [selectedResult, handleShotSelect, handleBrowseAllShots]);

    const handleSearchFocused = useCallback(() => setSearchFocusRequested(false), []);

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

    // What each requirement left over, in order. An empty page reads as "this is the one that
    // matched nothing" instead of "the search is broken".
    const filtersNote = searchFilters && searchFilters
        .map((f) => `${f.stage != null ? `s${f.stage}: ` : ""}${f.filter} → ${f.videos} video${f.videos === 1 ? "" : "s"}`)
        .join(" · ");

    // Back on the results: where they were left. onScroll only records while they are on screen,
    // the scroll event of the panel being reset arrives after the switch to Browse.
    useLayoutEffect(() => {
        if (mode === "search" && resultsPanel) resultsPanel.scrollTop = resultsScroll.current;
    }, [mode, resultsPanel]);

    const gridCards = mode === "search" ? ".result-card" : ".browse-card[data-keyframe-id]";
    useEffect(() => {
        if (!selectedResult) {
            lastCard.current = {};
            return;
        }
        const card = document.querySelector(`${gridCards}.selected`);
        if (card) lastCard.current[mode] = card.dataset.keyframeId;
    }, [selectedResult, mode, gridCards]);

    // A click leaves the focus on what was clicked, where Enter and Space would click it again
    // instead of reaching the shortcuts, and a picked dropdown would keep every key.
    useEffect(() => {
        const release = (e) => {
            if (e.target.closest?.(".send-hint, .vqa-section")) return;
            e.target.closest?.("button, select, input[type=checkbox]")?.blur();
        };
        const releaseDropdown = (e) => {
            if (e.target.matches?.("select")) e.target.blur();
        };
        window.addEventListener("click", release);
        window.addEventListener("change", releaseDropdown);
        return () => {
            window.removeEventListener("click", release);
            window.removeEventListener("change", releaseDropdown);
        };
    }, []);

    // The keyboard. Registered anew on every render, so it always reads the current state.
    useEffect(() => {
        const handleKeyDown = (e) => {
            const digit = digitOf(e);
            const key = (e.key || "").toLowerCase();

            // "Yes, submit!" only waits for Enter. Any other key calls it off and then does what it
            // always does, so an arrow also moves.
            if (confirmSubmit && key !== "enter" && !MODIFIER_KEYS.has(key)) setConfirmSubmit(false);

            // Ctrl+number works while typing too. It is the browser's tab switching and zoom reset
            // otherwise, so it is taken in every mode, not only where it does something.
            if ((e.ctrlKey || e.metaKey) && !e.altKey && digit) {
                e.preventDefault();
                if (e.repeat || dresLoginOpen || teamLoginOpen) return;
                // In the search bar it only changes the setting, Enter then runs what is typed.
                const rerun = !e.target.closest?.(".search-bar");
                if (digit === "0") {
                    // Only where the dropdown is on screen and enabled
                    if (loading || shotView.current) return;
                    const at = COLLECTIONS.findIndex(({key: dataset}) => dataset === collection);
                    handleChangeCollection(COLLECTIONS[(at + 1) % COLLECTIONS.length].key, rerun);
                } else if (mode === "search" && !loading) {
                    // Same conditions as the tickboxes: on screen, and not while a search runs
                    const source = sourcesFor(collection)[Number(digit) - 1];
                    if (source) handleToggleSource(source.key, rerun);
                }
                return;
            }

            // a popup keeps the keys to itself
            if (dresLoginOpen || teamLoginOpen) return;

            // Tab goes up to the search bar. In the bar it is the filters, and the time fields, the
            // hint popup and the VQA block keep their own. In the fullscreen keyframes it does nothing.
            if (key === "tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !keepsTheKeys(e.target)) {
                e.preventDefault();
                if (shotView.current) return;
                switchMode("search");
                setSearchFocusRequested(true);
                return;
            }

            // cause we dont want it to work in text area - we want to be avle to normally write in there
            if (keepsTheKeys(e.target)) {
                // Esc is the way back out to the shortcuts
                if (e.key === "Escape") e.target.blur();
                return;
            }
            // After the text fields, where it moves the caret.
            if ((e.ctrlKey || e.metaKey) && !e.altKey && key.startsWith("arrow")) {
                e.preventDefault();
                e.target.closest("button")?.blur();
                // Back to before A and D, as long as the selection is still on that run and that
                // keyframe has not been hidden as submitted in the meantime.
                const walk = keyframeWalk.current;
                const hidden = walk && isAvs && avsSubmittedScenes.has(sceneKey(walk.start.scene_id));
                if (walk && !hidden && walk.at === selectedResult?.keyframe_id) {
                    keyframeWalk.current = null;
                    handleSelect(walk.start);
                }
                return;
            }

            // The submission time from where the preview is right now.
            if ((e.ctrlKey || e.metaKey) && !e.altKey && (key === "home" || key === "end")) {
                e.preventDefault();
                const video = document.querySelector(".video-player video");
                if (video && selectedResult) handleTimeChange(key === "home" ? "from" : "to", formatTimecode(video.currentTime * 1000));
                return;
            }

            // Asking takes Ctrl, so it cannot happen by accident. Confirming takes Enter with or without.
            const ctrlEnter = (e.ctrlKey || e.metaKey) && !e.altKey && key === "enter";
            if (ctrlEnter || (confirmSubmit && key === "enter" && !e.altKey && !e.target.closest("button, a"))) {
                e.preventDefault();
                if (e.repeat || !selectedResult || selectedSubmitted) return;
                if (confirmSubmit) handleSubmit(selectedResult, submitStartMs, submitEndMs);
                else requestSubmit();
                return;
            }

            // Ctrl+A, Alt+Left and the like stay the browser's
            if (e.ctrlKey || e.metaKey || e.altKey) return;

            const letter = letterOf(e);

            // Moving on: a button focused earlier must not catch the next Enter.
            if (key.startsWith("arrow") || key === "home" || key === "end" || letter === "a" || letter === "d") {
                e.target.closest("button")?.blur();
            }

            if (key === "home" || key === "end") {
                e.preventDefault();
                // All the way up, the line above the first card is worth seeing.
                if (key === "home" && resultsPanel) resultsPanel.scrollTop = 0;
                const cards = document.querySelectorAll(gridCards);
                const card = cards[key === "home" ? 0 : cards.length - 1];
                if (card) {
                    card.click();
                } else if (key === "end") {
                    // The video grid selects nothing, it can only scroll there.
                    const videos = document.querySelectorAll(".browse-card");
                    videos[videos.length - 1]?.scrollIntoView({block: "nearest"});
                }
                return;
            }

            if (key.startsWith("arrow")) {
                const cards = [...document.querySelectorAll(gridCards)];
                if (cards.length === 0) return;
                // the panel would scroll as well
                e.preventDefault();
                // A click, because each grid knows what selecting one of its cards means
                const current = cards.find((card) => card.classList.contains("selected"))
                    ?? cards.find((card) => card.dataset.keyframeId === lastCard.current[mode]);
                if (e.shiftKey) {
                    if (current) neighbourStage(current, e.key)?.click();
                } else {
                    neighbourCard(cards, current, e.key)?.click();
                }
                return;
            }

            if (letter === "a" || letter === "d") {
                const landing = selectedResult
                    && neighbourKeyframe(recording.scenes, selectedResult.keyframe_id, letter === "d");
                if (landing) {
                    const walk = keyframeWalk.current;
                    keyframeWalk.current = {
                        start: walk?.at === selectedResult.keyframe_id ? walk.start : selectedResult,
                        at: landing.keyframe.keyframe_id,
                    };
                    handleShotSelect(landing.scene, landing.keyframe);
                }
                return;
            }

            // One action per press from here on: holding a key down is not pressing it again.
            if (e.repeat) return;

            if (letter === "f") {
                // What "Browse in Fullscreen" does. Already there inside the keyframes.
                if (selectedResult && !shotView.current) {
                    handleBrowseAllShots(selectedResult.video_id, selectedResult.keyframe_id, selectedResult);
                }
                return;
            }

            if (letter === "l") {
                document.querySelector(".loop-toggle input")?.click();
                return;
            }

            if (letter === "c") {
                if (selectedResult) handleFindSimilar(selectedResult);
                return;
            }

            if (letter === "v") {
                // or the v lands in the field it just went to
                e.preventDefault();
                document.querySelector(".vqa-input")?.focus();
                return;
            }

            if (letter === "t") {
                // Only there in a team and with something selected
                document.querySelector(".send-hint-btn")?.focus();
                return;
            }

            if (key === "backspace" || key === "delete") {
                // some browsers can be set to go back a page on Backspace
                e.preventDefault();
                const keyframes = shotView.current;
                // In the keyframes it is the video on screen, the selection can be from before.
                const video = keyframes ? {video_id: keyframes.videoId} : selectedResult;
                if (video) handleExcludeVideo(video);
                // Without a query to re-run nothing else leaves the view.
                keyframes?.back();
                return;
            }

            if (key === " ") {
                // a focused button or tickbox keeps its own Space
                if (e.target.closest("button, a, input")) return;
                e.preventDefault();
                // A focused player toggles on key-up as well, which would undo this.
                e.target.closest("video")?.blur();
                if (!shotView.current && Date.now() - lastSpace.current < DOUBLE_PRESS_MS) {
                    lastSpace.current = 0;
                    switchMode("search");
                    setSearchFocusRequested(true);
                    return;
                }
                lastSpace.current = Date.now();
                // Holds the preview on the keyframe for a closer look, the next one plays on.
                const video = document.querySelector(".video-player video");
                if (video && selectedResult) {
                    if (video.paused) {
                        video.play().catch(() => {
                        });
                    } else {
                        video.pause();
                        video.currentTime = (selectedKeyframeMs ?? selectedResult.start_time_ms) / 1000;
                    }
                }
                return;
            }

            // escape works as disselecting stuff
            if (key === "escape") {
                // an open "Yes, submit!" is what it cancels first
                if (confirmSubmit) {
                    setConfirmSubmit(false);
                    return;
                }
                const pressedOn = selectedResult;
                const deselect = () => {
                    // A keyframe clicked while the second press could still come stays selected.
                    setSelectedResult((current) => (current === pressedOn ? null : current));
                    setConfirmSubmit(false);
                    setSubmitStatus(null);
                };
                if (!shotView.current) {
                    deselect();
                } else if (pendingEsc.current) {
                    // Twice in the fullscreen keyframes: the back button, selection kept.
                    clearTimeout(pendingEsc.current);
                    pendingEsc.current = null;
                    shotView.current.back();
                } else {
                    // Once: deselects as usual, when the second press can no longer come.
                    pendingEsc.current = setTimeout(() => {
                        pendingEsc.current = null;
                        deselect();
                    }, DOUBLE_PRESS_MS);
                }
            }
        };
        // putting this on the whole window
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    });

    return (
        <div className="app">
            {/* for the top top bar */}
            <header className="app-header">
                <div className="app-header-left">
                    <h1>
                        CLIMB, Multi-Signal Keyframe Retrieval with Collaborative Ad-Hoc Search
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
                                onClick={() => handleChangeTaskMode("kis")}
                                title="Known-Item Search: submit one shot">KIS
                        </button>
                        <button className={`mode-btn ${taskMode === "avs" ? "active" : ""}`}
                                onClick={() => handleChangeTaskMode("avs")}
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
                    <button
                        className={`dres-login-btn ${team ? "connected" : ""}`}
                        onClick={() => setTeamLoginOpen(true)}
                        title={team ? `Team ${team.name} as ${team.user}` : "Join or create a team"}
                    >
                        <span className="dres-login-dot"/>
                        {team ? `Team · ${team.name}` : "Join Team"}
                    </button>
                    {team && <TeamInbox hints={team.hints || []} onOpen={openHint}/>}
                    <BackendStatusDot apiUrl={API_URL}/>
                </div>
            </header>
            <TeamLoginModal
                open={teamLoginOpen}
                onClose={() => setTeamLoginOpen(false)}
                team={team}
                listTeams={teamApi.listTeams}
                onConnect={teamApi.connect}
                onDisconnect={teamApi.disconnect}
            />
            <HintToast
                hint={teamApi.incoming[0] || null}
                pending={teamApi.incoming.length}
                onOpen={(hint) => {
                    teamApi.dismissIncoming(hint.id);
                    openHint(hint);
                }}
                onDismiss={teamApi.dismissIncoming}
            />
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
                    session={avsSession}
                    teamName={team?.name || null}
                    expiredCode={avsExpiredCode}
                    collabOffline={avsCollabOffline}
                    onNewSession={createAvsSession}
                    onLeave={leaveAvsSession}
                />
            )}
            {/* if statement, because for browsing we dont need it */}
            {mode === "search" && (
                <SearchBar
                    input={searchInput}
                    onInputChange={setSearchInput}
                    focusRequested={searchFocusRequested}
                    onFocused={handleSearchFocused}
                    onSearch={handleSearch}
                    loading={loading}
                    history={searchHistory}
                    sources={sources}
                    onToggleSource={handleToggleSource}
                    collection={collection}
                    onChangeCollection={handleChangeCollection}
                />

            )}

            <div className="main-content">
                <div
                    className="results-panel"
                    ref={setResultsPanel}
                    onScroll={(e) => {
                        if (mode === "search") resultsScroll.current = e.currentTarget.scrollTop;
                    }}
                >
                    {/* difference between search and browse mode */}
                    {mode === "search" ? (
                            <>
                                {loading && <div className="loading">Searching...</div>}
                                {/* Takes no room and no clicks, so it can come and go without moving a card */}
                                {widening != null && (
                                    <div className="widen-status-anchor">
                                        <span className="widen-status">Searching deeper · {widening}</span>
                                    </div>
                                )}
                                {/* A sequence that found nothing still shows its header - that is
                                    exactly when the per-stage counts are worth reading */}
                                {!loading && (results.length > 0 || temporalNote || filtersNote) && (
                                    <div className="results-info">
                                        {visibleResultCount}{searchHasMore || widening != null ? "+" : ""} results for "{query}"
                                        {collection && (
                                            <span className="results-temporal-note"> · {collection} only</span>
                                        )}
                                        {filtersNote && (
                                            <span className="results-temporal-note"> · {filtersNote}</span>
                                        )}
                                        {temporalNote && (
                                            <span className="results-temporal-note"> · {temporalNote}</span>
                                        )}
                                        {hiddenResultCount > 0 && (
                                            <span className="results-hidden-note"> · {hiddenResultCount} submitted hidden</span>
                                        )}
                                        <span className="shortcuts-hint">Arrows move{searchTemporal ? " · Shift+arrows stage" : ""} · A D keyframe · F all keyframes · C similar · Ctrl+0 dataset · Ctrl+1-{sourcesFor(collection).length} signals · Ctrl+Enter submit · Backspace exclude · T team · V answer · Space hold or play · L loop · Ctrl+Home End set time · Esc deselect · Tab search</span>
                                    </div>
                                )}
                                <ResultsGrid
                                    results={results}
                                    selectedResult={selectedResult}
                                    onSelect={handleSelect}
                                    onOpenKeyframes={handleOpenKeyframes}
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
                                shotView={shotView}
                                submittedScenes={isAvs ? avsSubmittedScenes : EMPTY_SET}
                                coveredVideos={isAvs ? avsCoveredVideos : EMPTY_SET}
                                collection={collection}
                                onChangeCollection={handleChangeCollection}
                                selectedKeyframeId={selectedResult?.keyframe_id ?? null}
                                focusKeyframeId={browseFocusKeyframeId}
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
                                <VideoPlayer
                                    result={selectedResult}
                                    previousClip={previousClip}
                                    nextClip={nextClip}
                                    clipName={(clip) => clipLabel(recording.recordingId, clip.video_id)}
                                    onStepClip={handleStepClip}
                                />
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
                                        onClick={requestSubmit}
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
                                            Yes, submit! (Enter)
                                        </button>
                                        <button className="submit-btn cancel" onClick={() => setConfirmSubmit(false)}>
                                            Cancel (any other key)
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
                                {team && (
                                    <SendHint
                                        result={selectedResult}
                                        recordingId={recording.recordingId}
                                        onSend={teamApi.sendHint}
                                    />
                                )}

                                {submitMessage && (
                                    <div className={`submit-result-box ${submitStatus || ""}`}>
                                        {submitMessage}
                                    </div>
                                )}
                            </div>
                            {/*  film tape under the video, every keyframe of the video */}
                            <ShotBrowser
                                videoId={selectedResult.video_id}
                                recording={recording}
                                currentKeyframeId={selectedResult.keyframe_id}
                                onSelectShot={handleShotSelect}
                                onBrowseAll={(videoId) => handleBrowseAllShots(videoId, selectedResult.keyframe_id, selectedResult)}
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