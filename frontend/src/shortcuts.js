// Helpers for the keyboard shortcuts in App.jsx.

// How long after one press a second one still counts as pressing the key twice.
export const DOUBLE_PRESS_MS = 500;

// Keys that are only on their way to a combination, lowercase like the handler's `key`.
export const MODIFIER_KEYS = new Set(["control", "shift", "alt", "altgraph", "meta", "os", "capslock", "numlock"]);

// Focus on one of these keeps the keys there: text is being typed, or the hint popup or the VQA
// block is running its own keyboard flow.
export const keepsTheKeys = (element) =>
    Boolean(element.closest?.("textarea, select, input:not([type=checkbox]), .send-hint, .vqa-section"));

// Which letter a key is: what it says on it, and where a layout has no Latin letters (Cyrillic,
// Greek, ...) the letter at that position on a QWERTY keyboard.
export const letterOf = (e) =>
    /^[a-z]$/i.test(e.key || "") ? e.key.toLowerCase() : e.code?.match(/^Key([A-Z])$/)?.[1].toLowerCase() ?? null;

// Which digit a key is. By position, because AZERTY needs Shift to type a digit from the top row.
export const digitOf = (e) =>
    e.code?.match(/^(?:Digit|Numpad)(\d)$/)?.[1] ?? (/^\d$/.test(e.key || "") ? e.key : null);

/**
 * The card an arrow key moves to. Left and right go card by card and wrap around, up and down go
 * to the closest card of the next row and stop at the edge.
 *
 * Rows come from the layout: the number of columns follows the window, and clip dividers break up
 * the keyframe grid. offsetTop and offsetLeft because the selected card is scaled, which
 * getBoundingClientRect would report as being in a row of its own.
 */
export function neighbourCard(cards, current, key) {
    const forward = key === "ArrowRight" || key === "ArrowDown";
    const at = cards.indexOf(current);
    if (at < 0) return forward ? cards[0] : cards[cards.length - 1];
    if (key === "ArrowRight") return cards[(at + 1) % cards.length];
    if (key === "ArrowLeft") return cards[(at - 1 + cards.length) % cards.length];

    const distance = (card) => Math.abs(card.offsetLeft - current.offsetLeft);
    let rowTop = null;
    let closest = null;
    for (let i = at + (forward ? 1 : -1); cards[i]; i += forward ? 1 : -1) {
        const card = cards[i];
        if (card.offsetTop === current.offsetTop) continue;
        if (rowTop === null) rowTop = card.offsetTop;
        if (card.offsetTop !== rowTop) break;
        if (!closest || distance(card) < distance(closest)) closest = card;
    }
    return closest;
}

/**
 * The stage of a chain an arrow key moves to: up to the first, left to the previous, right and down
 * to the next, round and round. The card itself stands for the first stage, its strip for the
 * others. Null on a card that is no chain. A card the selection has walked off (A and D) starts
 * over from either end.
 */
export function neighbourStage(card, key) {
    const partners = [...card.querySelectorAll(".temporal-partner")];
    if (partners.length === 0) return null;
    if (key === "ArrowUp") return card;
    const forward = key === "ArrowRight" || key === "ArrowDown";
    const stages = [card, ...partners];
    const active = partners.findIndex((partner) => partner.classList.contains("temporal-active"));
    const at = active >= 0 ? active + 1 : card.classList.contains("selected") ? 0 : -1;
    if (forward) return stages[(at + 1) % stages.length];
    return stages[at <= 0 ? stages.length - 1 : at - 1];
}

const FILTER_PREFIX = /(^|\s)(-?video:|text:|said:|spoken:|tag:)/gi;

/**
 * Tab in the search bar. Inside a filter (`said:good evening|`) it swaps that filter for the next
 * one and keeps what was typed after it. After a space, or with no filter yet, it starts a new one.
 * `step` is 1, or -1 to go back through the list.
 */
export function cycleFilter(value, caret, filters, step = 1) {
    const before = value.slice(0, caret);
    const after = value.slice(caret);
    const current = /\S$/.test(before) ? [...before.matchAll(FILTER_PREFIX)].pop() : undefined;
    if (!current) {
        const gap = before && !/\s$/.test(before) ? " " : "";
        const started = before + gap + filters[step > 0 ? 0 : filters.length - 1];
        return {value: started + after, caret: started.length};
    }
    const name = current[2].toLowerCase() === "spoken:" ? "said:" : current[2].toLowerCase();
    const at = filters.indexOf(name);
    const next = filters[((at < 0 ? 0 : at + step) + filters.length) % filters.length];
    const start = current.index + current[1].length;
    const swapped = before.slice(0, start) + next + before.slice(start + current[2].length);
    return {value: swapped + after, caret: swapped.length};
}
