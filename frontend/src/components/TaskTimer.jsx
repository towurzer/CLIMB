import { useState, useEffect, useRef } from "react";

function TaskTimer() {
    const [seconds, setSeconds] = useState(0);
    const [running, setRunning] = useState(false);
    const intervalRef = useRef(null);
    const TASK_DURATION = 300; // 5 minutes

    useEffect(() => {
        if (running && seconds < TASK_DURATION) {
            intervalRef.current = setInterval(() => {
                setSeconds((s) => s + 1);
            }, 1000);
        }
        return () => clearInterval(intervalRef.current);
    }, [running, seconds]);

    // Auto-stop at 5 minutes
    useEffect(() => {
        if (seconds >= TASK_DURATION) {
            setRunning(false);
            clearInterval(intervalRef.current);
        }
    }, [seconds]);

    const remaining = TASK_DURATION - seconds;
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    const progress = seconds / TASK_DURATION;
    const isUrgent = remaining <= 60;
    const isExpired = remaining <= 0;

    const handleStartReset = () => {
        if (running) {
            // Reset
            setRunning(false);
            setSeconds(0);
            clearInterval(intervalRef.current);
        } else {
            // Start
            setSeconds(0);
            setRunning(true);
        }
    };

    return (
        <div className={`task-timer ${isUrgent ? "urgent" : ""} ${isExpired ? "expired" : ""}`}>
            <div className="timer-display">
                {isExpired ? "TIME'S UP" : `${mins}:${secs.toString().padStart(2, "0")}`}
            </div>
            <div className="timer-bar">
                <div className="timer-fill" style={{ width: `${(1 - progress) * 100}%` }} />
            </div>
            <button className="timer-btn" onClick={handleStartReset}>
                {running ? "Reset" : seconds > 0 ? "Reset" : "Start"}
            </button>
        </div>
    );
}

export default TaskTimer;