// The four things a search can look at, and what they are called on screen.
//
// `key` is what the backend expects -- see SOURCE_RETRIEVERS in video_processing/src/retrieval/
// engine.py, which has to agree with this list. `label` is shared with the signal badges on a
// result card, so the picker and the badges can never end up calling the same encoder two names.
export const SOURCES = [
    {key: "visual", label: "Visual", title: "SigLIP2 embeddings of the keyframes"},
    {key: "caption", label: "Caption", title: "VLM shot descriptions, matched by meaning"},
    {key: "ocr", label: "OCR", title: "Text read off the keyframes -- signs, chyrons, subtitles"},
    {key: "asr", label: "ASR", title: "What people said, from the Whisper transcripts"},
];

export const ALL_SOURCE_KEYS = SOURCES.map((source) => source.key);

export const SIGNAL_LABELS = {
    visual: "Visual",
    ocr: "OCR",
    ocr_phrase: "OCR",
    transcript: "ASR",
    asr_phrase: "ASR",
    caption: "Caption",
    similar: "Similar",
};
