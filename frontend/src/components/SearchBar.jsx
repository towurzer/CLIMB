import {useState, useRef, useEffect, useLayoutEffect} from "react";
import {COLLECTIONS, sourcesFor} from "../sources";
import {cycleFilter} from "../shortcuts";

// The query syntax has nowhere else to be discovered, so it hangs off the input as a tooltip.
const SYNTAX_HELP = [
    "plain words          search everything",
    "text:Boulangerie     on-screen text, as a phrase",
    "said:good evening    speech, as a phrase (or spoken:)",
    "tag:bmx              uploader tags (V3C)",
    'text:"Dupont" a man  quote it to add plain words after',
    "video:00191          only this video / MVK recording",
    "-video:00191         exclude a video / MVK recording",
    "A >> B               A, then B within 1min, same video",
    "A >>(d120) B         ...within 120s instead",
    "",
    "text:, said: and tag: combine: every one must hold",
    "somewhere in the same video.",
    "",
    "Tab adds a filter, Tab again swaps it for the next one.",
].join("\n");

const FILTERS = ["text:", "said:", "tag:", "video:", "-video:"];
// Only V3C has uploader tags.
const filtersFor = (collection) =>
    (collection && collection !== "V3C" ? FILTERS.filter((filter) => filter !== "tag:") : FILTERS);

function SearchBar({input, onInputChange, focusRequested, onFocused, onSearch, loading, history,
                       sources, onToggleSource, collection, onChangeCollection}) {
    const [showHistory, setShowHistory] = useState(false);
    const inputRef = useRef(null);
    const wrapperRef = useRef(null);
    // Setting a controlled input's value puts the caret at the end, Tab can happen mid-text.
    const caretAfterRender = useRef(null);

    useLayoutEffect(() => {
        if (caretAfterRender.current === null) return;
        inputRef.current?.setSelectionRange(caretAfterRender.current, caretAfterRender.current);
        caretAfterRender.current = null;
    }, [input]);

    // Focus search bar with Ctrl+K shortcut
    useEffect(() => {
        const handleKeyDown = (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "k") {
                e.preventDefault();
                inputRef.current?.focus();
                inputRef.current?.select();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, []);

    // Space Space, which App listens for
    useEffect(() => {
        if (!focusRequested) return;
        inputRef.current?.focus();
        inputRef.current?.select();
        onFocused();
    }, [focusRequested, onFocused]);

    // Close dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
                setShowHistory(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    const handleSubmit = (e) => {
        e.preventDefault();
        if (input.trim()) {
            onSearch(input.trim());
            setShowHistory(false);
            // the keys go back to the results
            inputRef.current?.blur();
        }
    };

    const handleHistoryClick = (entry) => {
        onInputChange(entry);
        onSearch(entry);
        setShowHistory(false);
    };

    return (
        <div className="search-bar-wrapper" ref={wrapperRef}>
            <form className="search-bar" onSubmit={handleSubmit}>
                <input
                    ref={inputRef}
                    type="text"
                    value={input}
                    onChange={(e) => onInputChange(e.target.value)}
                    onFocus={() => {
                        if (history.length > 0) setShowHistory(true);
                    }}
                    onKeyDown={(e) => {
                        if (e.key === "Escape") setShowHistory(false);
                        if (e.key === "Tab" && !e.nativeEvent.isComposing && !e.ctrlKey && !e.altKey && !e.metaKey) {
                            e.preventDefault();
                            const next = cycleFilter(input, e.currentTarget.selectionEnd,
                                filtersFor(collection), e.shiftKey ? -1 : 1);
                            caretAfterRender.current = next.caret;
                            onInputChange(next.value);
                        }
                    }}
                    placeholder="Describe what you're looking for, or A >> B for a sequence... (Tab, Space Space or Ctrl+K, then Tab for filters)"
                    title={SYNTAX_HELP}
                    disabled={loading}
                />
                <button type="submit" disabled={loading || !input.trim()}>
                    {loading ? "..." : "Search"}
                </button>
            </form>

            {/* All on by default. Untick one and it is not searched at all -- OCR is pure
                noise on a query like "a snowboarder doing a backflip", and this is how you say so
                for one query without retuning the weights for every query. */}
            <div className="source-picker">
                {/* At VBS the task names its collection; searching only that one keeps the other
                    collections' look-alikes off the page. */}
                <select
                    className={`collection-picker${collection ? " active" : ""}`}
                    value={collection}
                    disabled={loading}
                    onChange={(e) => onChangeCollection(e.target.value)}
                    title="Search only this dataset (Ctrl+0 goes to the next one)"
                >
                    {COLLECTIONS.map(({key, label}) => (
                        <option key={key || "all"} value={key}>{label}</option>
                    ))}
                </select>
                <span className="source-key">0</span>
                <span className="source-picker-label" title="Ctrl+number flips a source">Sources</span>
                {sourcesFor(collection).map(({key, label, title}, index) => {
                    const active = sources.includes(key);
                    // Searching nothing is not a search. The last one standing cannot be unticked.
                    const isLast = active && sources.length === 1;
                    return (
                        <label
                            key={key}
                            className={`source-toggle${active ? " active" : ""}`}
                            title={isLast ? "At least one source has to stay on" : title}
                        >
                            <input
                                type="checkbox"
                                checked={active}
                                disabled={loading || isLast}
                                onChange={() => onToggleSource(key)}
                            />
                            {label}
                            <span className="source-key">{index + 1}</span>
                        </label>
                    );
                })}
            </div>

            {showHistory && history.length > 0 && (
                <div className="search-history">
                    <div className="history-label">Recent searches</div>
                    {history.map((entry, i) => (
                        <div
                            key={i}
                            className="history-item"
                            onClick={() => handleHistoryClick(entry)}
                        >
                            <span className="history-icon">↩</span>
                            {entry}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export default SearchBar;