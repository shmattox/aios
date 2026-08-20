// Tiny per-view state memory so navigating away (e.g. clicking a file → Files) and pressing Back
// restores the stage + selected item you were on, instead of resetting to the first item.
const store = {};
export function remember(key, val) { store[key] = val; }
export function recall(key) { return store[key]; }
