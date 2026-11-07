// Original, quiet bell-like cues. Kept short and synthesized locally, offline.
// Complete: rising pair (0.72 s); attention: two taps (0.60 s); error: falling pair (0.64 s).
const cues = {
  complete: { duration: 0.72, notes: [[0, 784], [0.14, 1046.5]] },
  attention: { duration: 0.60, notes: [[0, 880], [0.22, 880]] },
  error: { duration: 0.64, notes: [[0, 659.25], [0.18, 440]] },
};
let context: AudioContext | undefined;
let cancel: (() => void) | undefined;
let generation = 0;
Object.assign(window, {
  stopNotification() { generation++; cancel?.(); },
  async playNotification(kind: keyof typeof cues) {
    const cue = cues[kind];
    if (!cue) return;
    const current = ++generation;
    cancel?.();
    if (!context || context.state === "closed") context = new AudioContext();
    await context.resume();
    if (current !== generation) return;
    if (context.state !== "running") throw new Error("Audio unavailable");
    const audio = context;
    const start = audio.currentTime + 0.015;
    const oscillators: OscillatorNode[] = [];
    const gains: GainNode[] = [];
    await new Promise<void>(resolve => {
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        for (const oscillator of oscillators) {
          try { oscillator.stop(); } catch {}
          oscillator.disconnect();
        }
        for (const gain of gains) gain.disconnect();
        cancel = undefined;
        resolve();
      };
      const timer = setTimeout(finish, (cue.duration + 0.3) * 1000);
      cancel = finish;
      for (const [offset, frequency] of cue.notes) {
        const at = start + offset, end = start + cue.duration;
        for (const [ratio, volume] of [[1, 0.13], [2.76, 0.025], [4.07, 0.008]]) {
          const oscillator = audio.createOscillator();
          const gain = audio.createGain();
          oscillator.frequency.value = frequency * ratio;
          gain.gain.setValueAtTime(0, at);
          gain.gain.linearRampToValueAtTime(volume, at + 0.006);
          gain.gain.exponentialRampToValueAtTime(0.0001, end - 0.015);
          gain.gain.linearRampToValueAtTime(0, end);
          oscillator.connect(gain).connect(audio.destination);
          oscillators.push(oscillator); gains.push(gain);
          oscillator.start(at); oscillator.stop(end);
        }
      }
      oscillators[oscillators.length - 1].onended = finish;
    });
  },
});
