/**
 * Voice API Service
 * 
 * ARCHITECTURE NOTES:
 * - Handles speech-to-text transcription via Deepgram
 * - POSTs audio blob to backend `/api/voice/transcribe`
 * - Supports multiple audio formats and transcription options
 * - Used by AiChatInput for voice input feature
 */

const API_BASE = '/api';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

/**
 * Transcription options
 * Configuration for Deepgram transcription service
 */
export interface TranscriptionOptions {
    language?: string;
    model?: string;
    mimeType?: string;
    smartFormat?: boolean;
    punctuate?: boolean;
}

/**
 * Transcription response
 * Result from backend transcription endpoint
 */
export interface TranscriptionResponse {
    status: string;
    transcript?: string;
    message?: string;
}

// ============================================================================
// TRANSCRIPTION FUNCTION
// ============================================================================

/**
 * Transcribe audio blob to text
 * 
 * FLOW:
 * 1. Validate audio blob
 * 2. Build FormData with audio + options
 * 3. POST to backend transcription endpoint
 * 4. Return transcript or throw error
 * 
 * @param blob - Audio blob to transcribe
 * @param options - Transcription configuration
 * @returns Transcription result with transcript text
 */
export async function transcribeAudio(
    blob: Blob,
    options: TranscriptionOptions = {}
): Promise<TranscriptionResponse> {
    if (!blob) {
        throw new Error('Audio blob is required for transcription');
    }

    const {
        language,
        model,
        mimeType,
        smartFormat,
        punctuate,
    } = options;

    const type = mimeType || blob.type || 'audio/webm';
    const extension = getExtensionFromMimeType(type);

    const formData = new FormData();
    formData.append('audio', blob, `recording.${extension}`);

    if (language) {
        formData.append('language', language);
    }
    if (model) {
        formData.append('model', model);
    }
    if (type) {
        formData.append('mimeType', type);
    }
    if (smartFormat !== undefined) {
        formData.append('smartFormat', String(smartFormat));
    }
    if (punctuate !== undefined) {
        formData.append('punctuate', String(punctuate));
    }

    // Debug: Log FormData contents
    console.log('[voiceApi] Sending transcription request:', {
        blobSize: blob.size,
        blobType: blob.type,
        extension,
        hasAudioField: formData.has('audio'),
    });

    // CRITICAL: Do NOT set Content-Type header - let fetch handle it automatically
    // Setting it manually breaks the multipart boundary
    const response = await fetch(`${API_BASE}/voice/transcribe`, {
        method: 'POST',
        body: formData,
        // Do NOT include headers here - fetch will auto-set multipart/form-data with boundary
    });

    if (!response.ok) {
        const errorText = await response.text().catch(() => 'Transcription failed');
        // Parse JSON error if possible
        let errorMessage = errorText;
        try {
            const errorJson = JSON.parse(errorText);
            errorMessage = errorJson.message || errorJson.detail || errorText;
        } catch {
            // Keep raw text
        }
        console.error('[voiceApi] Transcription failed:', response.status, errorMessage);
        throw new Error(errorMessage);
    }

    const result = await response.json();
    console.log('[voiceApi] Transcription successful:', result.transcript?.substring(0, 50) + '...');
    return result;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Get file extension from MIME type
 * Maps audio MIME types to file extensions
 */
function getExtensionFromMimeType(mimeType: string): string {
    const map: Record<string, string> = {
        'audio/webm': 'webm',
        'audio/mp4': 'm4a',
        'audio/mpeg': 'mp3',
        'audio/ogg': 'ogg',
        'audio/wav': 'wav',
        'audio/flac': 'flac',
    };
    return map[mimeType] || 'webm';
}
