// The things a search can look at, and what they are called on screen.
//
// `key` is what the backend expects -- see SOURCE_RETRIEVERS in video_processing/src/retrieval/
// engine.py, which has to agree with this list. `label` is shared with the signal badges on a
// result card, so the picker and the badges can never end up calling the same encoder two names.
export const SOURCES = [
    {key: "visual", label: "Visual", title: "SigLIP2 embeddings of the keyframes; for MVK also BioCLIP, for LHE also PeskaVLP"},
    {key: "caption", label: "Caption", title: "VLM shot descriptions, matched by meaning"},
    {key: "ocr", label: "OCR", title: "Text read off the keyframes -- signs, chyrons, subtitles"},
    {key: "asr", label: "ASR", title: "What people said, from the Whisper transcripts"},
    {key: "info", label: "Info", title: "The uploader's title, tags and description (V3C), and query words that are its tags. Re-orders what the others found, by how well the whole video matches"},
];

export const ALL_SOURCE_KEYS = SOURCES.map((source) => source.key);

// Sources that start unticked for some datasets. Choosing a dataset applies these; they can still be
// flipped by hand afterwards.
//   info: only V3C has info files.
//   asr:  MVK is filmed underwater; its "transcripts" are Whisper hallucinations ("Thank you.").
const OFF_FOR = {
    info: (collection) => collection !== "V3C",
    asr: (collection) => collection === "MVK",
};

const startsTicked = (key, collection) => !(key in OFF_FOR) || !OFF_FOR[key](collection);

export const sourcesForCollection = (collection, sources) => {
    const next = ALL_SOURCE_KEYS.filter((key) =>
        key in OFF_FOR ? startsTicked(key, collection) : sources.includes(key));
    // Never leave nothing ticked: fall back to everything this dataset starts with
    return next.length ? next : ALL_SOURCE_KEYS.filter((key) => startsTicked(key, collection));
};

// The collections a search can be limited to. `key` is what videos.collection holds (see
// /datasets.json); "" means all of them. At VBS the task says which one it is from.
export const COLLECTIONS = [
    {key: "", label: "All datasets"},
    {key: "V3C", label: "V3C"},
    {key: "MVK", label: "MVK (marine)"},
    {key: "LHE", label: "LHE (medical)"},
];

export const SIGNAL_LABELS = {
    visual: "Visual",
    bioclip: "BioCLIP",
    peskavlp: "PeskaVLP",
    ocr: "OCR",
    ocr_phrase: "OCR",
    transcript: "ASR",
    asr_phrase: "ASR",
    caption: "Caption",
    info: "Info",
    tags: "Tags",
    listing: "Listing",
    similar: "Similar",
};
