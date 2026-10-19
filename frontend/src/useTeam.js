import {useCallback, useEffect, useRef, useState} from "react";

const POLL_MS = 4000;

/**
 * This stack's team membership. The backend holds the connection (like the DRES login), so a page
 * reload finds it again; this polls it while connected, in KIS and AVS alike, because hints are
 * not an AVS thing.
 *
 * `incoming` is the hints from teammates that arrived since connecting and have not been shown yet,
 * oldest first. Hints that already existed when this browser connected are history (the inbox), not
 * news.
 */
export function useTeam(apiUrl) {
    const [team, setTeam] = useState(null); // backend view while connected, null otherwise
    const [lostTeam, setLostTeam] = useState(null); // name of a team that expired under us
    const [incoming, setIncoming] = useState([]);
    const seen = useRef(null); // Set of hint ids already known; null until the first snapshot
    const lastName = useRef(null); // so a team that expires under us can be named

    const take = useCallback((view) => {
        if (view?.lost) setLostTeam(lastName.current || "The team");
        if (!view?.connected) {
            setTeam(null);
            seen.current = null;
            setIncoming([]);
            return;
        }
        const hints = view.hints || [];
        if (seen.current === null) {
            seen.current = new Set(hints.map((h) => h.id));
        } else {
            const fresh = hints.filter((h) => !seen.current.has(h.id));
            fresh.forEach((h) => seen.current.add(h.id));
            const fromOthers = fresh.filter((h) => h.user !== view.user).reverse();
            if (fromOthers.length) setIncoming((prev) => [...prev, ...fromOthers]);
        }
        lastName.current = view.name;
        setLostTeam(null);
        setTeam(view);
    }, []);

    const fetchTeam = useCallback(async () => {
        const res = await fetch(`${apiUrl}/climb/team`);
        if (!res.ok) return; // 503 keeps what we have
        take(await res.json());
    }, [apiUrl, take]);

    // On load: a connection made before a page reload is still there
    useEffect(() => {
        fetchTeam().catch(() => {});
    }, [fetchTeam]);

    useEffect(() => {
        if (!team) return;
        const id = setInterval(() => fetchTeam().catch(() => {}), POLL_MS);
        return () => clearInterval(id);
    }, [Boolean(team), fetchTeam]);

    const listTeams = useCallback(async () => {
        const res = await fetch(`${apiUrl}/climb/team/list`);
        const data = await res.json().catch(() => ({}));
        return {ok: res.ok, teams: data.teams || [], error: data.error};
    }, [apiUrl]);

    /** {ok: true} or {ok: false, error} */
    const connect = useCallback(async (name, password, user) => {
        try {
            const res = await fetch(`${apiUrl}/climb/team/connect`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({team: name, password, user}),
            });
            const data = await res.json();
            if (!res.ok) return {ok: false, error: data.error || "Could not connect."};
            seen.current = null;
            take(data);
            return {ok: true, created: res.status === 201};
        } catch (err) {
            return {ok: false, error: "The backend is not reachable."};
        }
    }, [apiUrl, take]);

    const disconnect = useCallback(async () => {
        await fetch(`${apiUrl}/climb/team/disconnect`, {method: "POST"}).catch(() => {});
        lastName.current = null;
        take({connected: false});
    }, [apiUrl, take]);

    const post = useCallback(async (path, body) => {
        const res = await fetch(`${apiUrl}/climb/team/${path}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body || {}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) return {ok: false, error: data.error || "The team service did not answer."};
        take(data);
        return {ok: Boolean(data.connected)};
    }, [apiUrl, take]);

    const newSession = useCallback(() => post("session"), [post]);
    const sendHint = useCallback((hint) => post("hint", hint), [post]);

    const dismissIncoming = useCallback((id) => {
        setIncoming((prev) => prev.filter((h) => h.id !== id));
    }, []);

    return {team, lostTeam, incoming, listTeams, connect, disconnect, newSession, sendHint, dismissIncoming};
}

/** ">24h" while a team has more than a day left, then whole hours. */
export const teamExpiryLabel = (ms) => (ms > 24 * 3600 * 1000 ? ">24h" : `${Math.max(1, Math.ceil(ms / 3600000))}h`);
