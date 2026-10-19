import {useEffect, useState} from "react";

const EMPTY = {recordingId: null, clips: [], scenes: [], loading: false};

/**
 * Every scene of the recording a video belongs to, for the filmstrip and the clip buttons.
 *
 * An MVK dive is several clips; selecting a keyframe in another clip of the same dive changes
 * video_id but not the recording, so that does not fetch again.
 */
export function useRecording(videoId, apiUrl) {
    const [recording, setRecording] = useState(EMPTY);

    const loaded = recording.clips.some((clip) => clip.video_id === videoId);

    useEffect(() => {
        if (!videoId) {
            setRecording(EMPTY);
            return;
        }
        if (loaded) return;

        let cancelled = false;
        setRecording({...EMPTY, loading: true});
        fetch(`${apiUrl}/climb/videos/${videoId}/scenes`)
            .then((res) => res.json())
            .then((data) => {
                if (cancelled) return;
                setRecording({
                    recordingId: data.recording_id ?? videoId,
                    clips: data.clips || [],
                    scenes: data.scenes || [],
                    loading: false,
                });
            })
            .catch((err) => {
                console.error("Failed to load shots:", err);
                if (!cancelled) setRecording(EMPTY);
            });
        return () => {
            cancelled = true;
        };
    }, [videoId, apiUrl, loaded]);

    return recording;
}

/** "Ambon_Apr2012_0002" -> "0002" inside recording "Ambon_Apr2012". */
export const clipLabel = (recordingId, videoId) =>
    recordingId && videoId.startsWith(`${recordingId}_`) ? videoId.slice(recordingId.length + 1) : videoId;

/** The clips before and after `videoId` in its recording, or null at either end. */
export function neighbourClips(clips, videoId) {
    const at = clips.findIndex((clip) => clip.video_id === videoId);
    if (at < 0) return {previous: null, next: null};
    return {previous: clips[at - 1] || null, next: clips[at + 1] || null};
}

/**
 * Where to land when stepping to a neighbouring clip: forward onto its first keyframe, back onto
 * its last, so stepping reads as continuing the dive.
 */
export function landingInClip(scenes, clipId, forward) {
    const inClip = scenes.filter((scene) => scene.video_id === clipId && scene.keyframes?.length);
    const scene = forward ? inClip[0] : inClip[inClip.length - 1];
    if (!scene) return null;
    return {scene, keyframe: forward ? scene.keyframes[0] : scene.keyframes[scene.keyframes.length - 1]};
}
