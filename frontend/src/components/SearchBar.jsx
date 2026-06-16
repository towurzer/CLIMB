import { useState, useRef, useEffect } from "react";

function SearchBar({ onSearch, loading, history }) {
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
          placeholder="Describe what you're looking for... (Ctrl+K)"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? "..." : "Search"}
        </button>
      </form>

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