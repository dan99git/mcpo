/**
 * TTS Player Service
 * 
 * ARCHITECTURE NOTES:
 * - Manages text-to-speech playback via ElevenLabs
 * - POSTs text to backend `/api/tts/speak`
 * - Plays returned audio blob via HTML5 Audio API
 * - Singleton instance exported as default
 * 
 * STATE MANAGEMENT:
 * - TTS enabled state managed by backend (not localStorage)
 * - Default voice ID can be configured
 * - Only one audio playback at a time (stops previous)
 */

const API_BASE = '/api';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

/**
 * TTS speak options
 * Configuration for text-to-speech request
 */
export interface SpeakOptions {
    voiceId?: string;
    modelId?: string;
}

// ============================================================================
// TTS PLAYER CLASS
// ============================================================================

class TtsPlayer {
    private audio: HTMLAudioElement | null = null;
    private objectUrl: string | null = null;
    private enabled: boolean;
    private defaultVoiceId: string | null = null;

    constructor() {
        this.enabled = this.loadEnabled();
    }

    /**
     * Load TTS enabled state
     * NOTE: Backend owns TTS state, defaults to false until set
     */
    private loadEnabled(): boolean {
        return false;
    }

    /**
     * Save TTS enabled state
     * NOTE: Persistence handled by backend, this is a no-op
     */
    private saveEnabled(_value: boolean): void {
        // Backend handles persistence
    }

    /**
     * Set TTS enabled state
     * Stops playback if disabling
     */
    setEnabled(value: boolean): void {
        this.enabled = Boolean(value);
        this.saveEnabled(this.enabled);
        if (!this.enabled) {
            this.stop();
        }
    }

    /**
     * Get TTS enabled state
     */
    getEnabled(): boolean {
        return this.enabled;
    }

    /**
     * Set default voice ID
     * Used when no voiceId specified in speak options
     */
    setDefaultVoice(voiceId: string | null): void {
        this.defaultVoiceId = typeof voiceId === 'string' ? voiceId : null;
    }

    /**
     * Speak text using TTS
     * 
     * FLOW:
     * 1. Check if TTS enabled
     * 2. Stop any existing playback
     * 3. POST text to backend TTS endpoint
     * 4. Create audio blob from response
     * 5. Play audio via HTML5 Audio API
     * 
     * ERROR HANDLING:
     * - Silently fails on network errors
     * - Warns on backend errors
     * - Handles autoplay restrictions
     * 
     * @param text - Text to speak
     * @param options - Voice and model configuration
     */
    async speak(text: string, options: SpeakOptions = {}): Promise<void> {
        if (!this.enabled) return;
        const content = (text || '').trim();
        if (!content) return;

        // Stop existing playback
        this.stop();

        const voiceId = options.voiceId || this.defaultVoiceId || undefined;
        const modelId = options.modelId || undefined;

        let response: Response;
        try {
            response = await fetch(`${API_BASE}/tts/speak`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: content, voiceId, modelId }),
            });
        } catch (err) {
            console.warn('[ttsPlayer] Request failed:', err);
            return;
        }

        if (!response.ok) {
            const detail = await response.text().catch(() => '');
            console.warn('[ttsPlayer] TTS error:', response.status, detail);
            // Log specific error types for debugging
            if (response.status === 503) {
                console.error('[ttsPlayer] ElevenLabs API key not configured');
            } else if (response.status === 400) {
                console.error('[ttsPlayer] Voice ID not configured');
            }
            return;
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);

        const audio = new Audio();
        audio.src = url;
        audio.preload = 'auto';
        audio.onended = () => {
            this.revokeUrl();
        };
        audio.onerror = () => {
            this.revokeUrl();
        };

        this.audio = audio;
        this.objectUrl = url;

        try {
            await audio.play();
        } catch (err) {
            console.warn('[ttsPlayer] Playback failed (autoplay restrictions?):', err);
            this.revokeUrl();
        }
    }

    /**
     * Stop current playback
     * Cleans up audio element and object URL
     */
    stop(): void {
        if (this.audio) {
            this.audio.pause();
            this.audio.src = '';
            this.audio = null;
        }
        this.revokeUrl();
    }

    /**
     * Revoke object URL to free memory
     */
    private revokeUrl(): void {
        if (this.objectUrl) {
            URL.revokeObjectURL(this.objectUrl);
            this.objectUrl = null;
        }
    }
}

// ============================================================================
// SINGLETON EXPORT
// ============================================================================

const ttsPlayer = new TtsPlayer();
export default ttsPlayer;
