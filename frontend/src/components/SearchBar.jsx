import {useState, useRef, useEffect} from "react";
import {SOURCES} from "../sources";

// The query syntax has nowhere else to be discovered, so it hangs off the input as a tooltip.
const SYNTAX_HELP = [
    "plain words          search everything",
    "text:Boulangerie     on-screen text only, as a phrase",
    "said:good evening    transcripts only, as a phrase",
    'text:"Dupont" a man  quote it to add plain words after',
    "-video:00191         exclude a video",
    "A >> B               A, then B within 1min, same video",
    "A >>(d120) B         ...within 120s instead",
].join("\n");

function SearchBar({onSearch, loading, history, sources, onToggleSource}) {
    const [input, setInput] = useState("");
    const [showHistory, setShowHistory] = useState(false);
    const inputRef = useRef(null);
    const wrapperRef = useRef(null);

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
        }
    };

    const handleHistoryClick = (entry) => {
        setInput(entry);
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
                    onChange={(e) => setInput(e.target.value)}
                    onFocus={() => {
                        if (history.length > 0) setShowHistory(true);
                    }}
                    placeholder="Describe what you're looking for, or A >> B for a sequence... (Ctrl+K)"
                    title={SYNTAX_HELP}
                    disabled={loading}
                />
                <button type="submit" disabled={loading || !input.trim()}>
                    {loading ? "..." : "Search"}
                </button>
            </form>

            {/* All four on by default. Untick one and it is not searched at all -- OCR is pure
                noise on a query like "a snowboarder doing a backflip", and this is how you say so
                for one query without retuning the weights for every query. */}
            <div className="source-picker">
                <span className="source-picker-label">Sources</span>
                {SOURCES.map(({key, label, title}) => {
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