/**
 * AI Chat Input Component
 * 
 * ARCHITECTURE NOTES:
 * - User input interface with text, voice, and mode selection
 * - Supports voice recording and transcription
 * - TTS toggle integration
 * - MCP server configuration UI
 * - Auto-resizing textarea
 * - Keyboard shortcuts (Enter to send, Shift+Enter for newline)
 * 
 * FEATURES:
 * - Text input with auto-resize
 * - Voice recording with visual feedback
 * - Speech-to-text transcription
 * - TTS enable/disable toggle
 * - MCP server management
 * - Mode switching (ask/agent)
 * - Send button with loading states
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useChatStore } from './chat-store';
import { transcribeAudio } from './voice-service';
import ttsPlayer from './tts-service';
import type { Attachment } from './types';
import { chatApi } from '../api';
import { getDisabledMcpServers, setDisabledMcpServers } from './tool-prefs';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export type InteractionMode = 'ask' | 'agent';

export interface AiChatInputProps {
    onSend: (content: string, attachments?: Attachment[], mode?: InteractionMode) => void;
    disabled?: boolean;
    isStreaming?: boolean;
    mode?: InteractionMode;
    onClearHistory?: () => void;
    onExportChat?: () => void;
    onStopStreaming?: () => void;
}

interface McpServer {
    name: string;
    toolCount: number;
    enabled: boolean;
    connected?: boolean;
    persistedDisabled?: boolean;
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export function AiChatInput({
    onSend,
    disabled = false,
    isStreaming = false,
    mode = 'ask',
    onClearHistory,
    onExportChat,
    onStopStreaming,
}: AiChatInputProps) {
    // ========================================================================
    // STATE
    // ========================================================================

    const [input, setInput] = useState('');
    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const [pastedImages, setPastedImages] = useState<File[]>([]);

    // UI State
    const [showSettings, setShowSettings] = useState(false);
    const [showTools, setShowTools] = useState(false);

    const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [opacity, setOpacity] = useState(0);

    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const audioChunksRef = useRef<Blob[]>([]);
    const rafRef = useRef<number | null>(null);

    const ttsEnabled = useChatStore((state) => state.ttsEnabled);
    const setTtsEnabled = useChatStore((state) => state.setTtsEnabled);

    // ========================================================================
    // SPOTLIGHT EFFECT
    // ========================================================================

    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!wrapperRef.current) return;
        if (rafRef.current) return;

        const clientX = e.clientX;
        const clientY = e.clientY;
        const div = wrapperRef.current;

        rafRef.current = requestAnimationFrame(() => {
            const rect = div.getBoundingClientRect();
            setPosition({ x: clientX - rect.left, y: clientY - rect.top });
            rafRef.current = null;
        });
    }, []);

    const handleMouseEnter = () => {
        setOpacity(1);
    };

    const handleMouseLeave = () => {
        setOpacity(0);
    };

    // ========================================================================
    // AUTO-RESIZE TEXTAREA
    // ========================================================================

    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 240)}px`;
        }
    }, [input]);

    // ========================================================================
    // IMAGE PASTE HANDLER
    // ========================================================================

    const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        const imageFiles: File[] = [];
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            if (item.type.startsWith('image/')) {
                const file = item.getAsFile();
                if (file) {
                    imageFiles.push(file);
                }
            }
        }

        if (imageFiles.length > 0) {
            e.preventDefault(); // Prevent default paste if we have images
            setPastedImages(prev => [...prev, ...imageFiles]);
        }
    }, []);

    const removeImage = useCallback((index: number) => {
        setPastedImages(prev => prev.filter((_, i) => i !== index));
    }, []);

    // ========================================================================
    // MCP SERVER MANAGEMENT
    // ========================================================================

    useEffect(() => {
        if (showTools && mcpServers.length === 0) {
            loadMcpServers();
        }
    }, [showTools, mcpServers.length]);

    useEffect(() => {
        const handleMcpConfigChanged = () => {
            loadMcpServers();
        };

        window.addEventListener('mcp-config-changed', handleMcpConfigChanged);

        return () => {
            window.removeEventListener('mcp-config-changed', handleMcpConfigChanged);
        };
    }, []);

    const loadMcpServers = async () => {
        try {
            const response = await chatApi.getMcpServers();
            const localDisabled = getDisabledMcpServers();
            // Map the response to ensure toolCount is a number and include enabled state
            const validServers: McpServer[] = (response.servers || []).map((server: any) => ({
                name: server.name,
                toolCount: server.toolCount ?? 0,
                // Determine enabled state by combining server persisted/connected status with local override
                enabled: Boolean(!server.persistedDisabled && !localDisabled.includes(server.name) && (server.enabled || server.connected))
            }));
            setMcpServers(validServers);
        } catch (error) {
            console.error('[AiChatInput] Failed to load MCP servers:', error);
        }
    };

    const handleServerToggle = async (serverName: string, enabled: boolean) => {
        try {
            // Update localStorage first for immediate UI feedback
            const disabledServers = getDisabledMcpServers();
            if (enabled) {
                // Remove from disabled list
                setDisabledMcpServers(disabledServers.filter(s => s !== serverName));
            } else {
                // Add to disabled list
                if (!disabledServers.includes(serverName)) {
                    setDisabledMcpServers([...disabledServers, serverName]);
                }
            }

            // Update local state immediately
            setMcpServers(prev => prev.map(s =>
                s.name === serverName ? { ...s, enabled } : s
            ));

            // Sync with backend
            await chatApi.toggleMcpServer(serverName, enabled);

            // Dispatch event to notify other components
            window.dispatchEvent(new CustomEvent('mcp-config-changed'));
        } catch (error) {
            console.error('[AiChatInput] Failed to toggle MCP server:', error);
            // Revert on error by reloading
            await loadMcpServers();
        }
    };

    // ========================================================================
    // VOICE RECORDING
    // ========================================================================

    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const mediaRecorder = new MediaRecorder(stream);
            mediaRecorderRef.current = mediaRecorder;
            audioChunksRef.current = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunksRef.current.push(event.data);
                }
            };

            mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop());
            };

            mediaRecorder.start();
            setIsRecording(true);
        } catch (error) {
            console.error('[AiChatInput] Failed to start recording:', error);
        }
    };

    const stopRecording = () => {
        if (mediaRecorderRef.current && isRecording) {
            mediaRecorderRef.current.stop();
            setIsRecording(false);
            transcribeRecording();
        }
    };

    const transcribeRecording = async () => {
        if (audioChunksRef.current.length === 0) {
            return;
        }

        setIsTranscribing(true);
        try {
            const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
            const result = await transcribeAudio(audioBlob);

            if (result.transcript) {
                setInput(prev => prev + (prev ? ' ' : '') + result.transcript);
            } else {
                console.warn('[AiChatInput] Transcription returned empty result');
            }
        } catch (error) {
            console.error('[AiChatInput] Transcription failed:', error);
            // Show error feedback to user
            const errorMessage = error instanceof Error ? error.message : 'Transcription failed';
            if (errorMessage.includes('API key')) {
                alert('Speech-to-text service not configured. Please set DEEPGRAM_API_KEY.');
            }
        } finally {
            setIsTranscribing(false);
            audioChunksRef.current = [];
        }
    };

    const handleMicClick = () => {
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    };

    const handleTTSClick = async () => {
        const newValue = !ttsEnabled;

        // Optimistically update UI
        setTtsEnabled(newValue);
        ttsPlayer.setEnabled(newValue);

        try {
            const response = await fetch('/api/config/tts-enabled', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: newValue })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            console.log(`[AiChatInput] TTS ${newValue ? 'enabled' : 'disabled'}`);
        } catch (error) {
            console.error('[AiChatInput] Failed to save TTS preference:', error);
            // Revert on failure
            setTtsEnabled(!newValue);
            ttsPlayer.setEnabled(!newValue);
        }
    };

    // ========================================================================
    // MESSAGE SENDING
    // ========================================================================

    const handleSend = () => {
        const trimmed = input.trim();
        const hasImages = pastedImages.length > 0;
        
        if ((!trimmed && !hasImages) || disabled) {
            return;
        }

        // Convert pasted images to attachments with File reference
        const imageAttachments: Attachment[] = pastedImages.map(file => ({
            filename: file.name || `image-${Date.now()}.png`,
            mimetype: file.type,
            size: file.size,
            file, // Include File object for upload
        } as Attachment & { file: File }));

        onSend(trimmed || 'Image attached', imageAttachments.length > 0 ? imageAttachments : undefined, mode);
        setInput('');
        setPastedImages([]);

        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    // ========================================================================
    // RENDER
    // ========================================================================

    return (
        <div className="ai-chat__input-area">
            {/* Typing indicator - shown when streaming starts before content arrives */}
            {isStreaming && (
                <div className="ai-chat__typing-indicator">
                    <span className="ai-chat__typing-dot"></span>
                    <span className="ai-chat__typing-dot"></span>
                    <span className="ai-chat__typing-dot"></span>
                    <span className="ai-chat__typing-label">Auralis is thinking</span>
                </div>
            )}
            
            <div className="ai-chat__input-shell relative">
                {/* Image Preview Area */}
                {pastedImages.length > 0 && (
                    <div className="ai-chat__image-previews">
                        {pastedImages.map((file, index) => (
                            <div key={index} className="ai-chat__image-preview">
                                <img
                                    src={URL.createObjectURL(file)}
                                    alt={`Pasted image ${index + 1}`}
                                    onLoad={(e) => URL.revokeObjectURL((e.target as HTMLImageElement).src)}
                                />
                                <button
                                    type="button"
                                    className="ai-chat__image-remove"
                                    onClick={() => removeImage(index)}
                                    title="Remove image"
                                >
                                    ×
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <div
                    className="ai-chat__input-wrapper relative"
                    ref={wrapperRef}
                    onMouseMove={handleMouseMove}
                    onMouseEnter={handleMouseEnter}
                    onMouseLeave={handleMouseLeave}
                >
                    {/* Border Glow Effect */}
                    <div
                        className="pointer-events-none absolute -inset-px z-50 transition duration-300"
                        style={{
                            opacity,
                            background: `radial-gradient(300px circle at ${position.x}px ${position.y}px, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 0.5) 20%, transparent 50%)`,
                            maskImage: 'linear-gradient(black, black), linear-gradient(black, black)',
                            maskClip: 'content-box, border-box',
                            padding: '1px',
                            maskComposite: 'exclude',
                            WebkitMaskComposite: 'xor',
                        }}
                    />

                    <textarea
                        ref={textareaRef}
                        id="ai-input"
                        className="ai-chat__input"
                        placeholder={disabled ? 'Waiting for response...' : 'Type your message or paste an image...'}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        onPaste={handlePaste}
                        disabled={disabled}
                        rows={1}
                    />

                    <div className="ai-chat__input-actions">
                        <div className="ai-chat__input-left">
                            {/* Plus/Settings button */}
                            <button
                                type="button"
                                className="ai-chat__control ai-chat__control--plus"
                                id="ai-settings-button"
                                onClick={() => setShowSettings(!showSettings)}
                                aria-expanded={showSettings}
                                title="Chat options"
                            >
                                <span className="ai-chat__control-icon">+</span>
                            </button>

                            {/* Tools button */}
                            <button
                                type="button"
                                className="ai-chat__control ai-chat__control--tools"
                                id="ai-tools-button"
                                onClick={() => setShowTools(!showTools)}
                                aria-expanded={showTools}
                                title="Manage tools"
                            >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
                                </svg>
                            </button>
                        </div>

                        <div className="ai-chat__input-right">
                            {isStreaming && (
                                <button
                                    type="button"
                                    className="ai-chat__control ai-chat__control--stop"
                                    onClick={onStopStreaming}
                                    title="Stop response"
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <rect x="6" y="6" width="12" height="12" />
                                    </svg>
                                </button>
                            )}

                            {/* Mic button */}
                            <button
                                type="button"
                                className={`ai-chat__control ai-chat__control--mic ${isRecording ? 'ai-chat__control--active' : ''}`}
                                onClick={handleMicClick}
                                disabled={disabled || isTranscribing}
                                title={isRecording ? 'Stop recording' : 'Voice input'}
                            >
                                {isTranscribing ? (
                                    <span className="step-spinner" style={{ width: '16px', height: '16px', border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', display: 'inline-block', animation: 'spin 1s linear infinite' }} />
                                ) : (
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                                        <line x1="12" y1="19" x2="12" y2="23"></line>
                                        <line x1="8" y1="23" x2="16" y2="23"></line>
                                    </svg>
                                )}
                            </button>

                            {/* TTS button */}
                            <button
                                type="button"
                                className={`ai-chat__control ai-chat__control--tts ${ttsEnabled ? 'ai-chat__control--active' : ''}`}
                                id="ai-tts"
                                aria-pressed={ttsEnabled}
                                onClick={handleTTSClick}
                                title={ttsEnabled ? 'Disable text-to-speech' : 'Speak responses'}
                            >
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M3 10v4a2 2 0 0 0 2 2h3l4 3V7l-4 3H5a2 2 0 0 1-2-2z" />
                                    <path d="M16.5 9.4a5 5 0 0 1 0 5.2" />
                                    <path d="M19 7a8 8 0 0 1 0 10" />
                                </svg>
                            </button>

                            {/* Send button */}
                            <button
                                type="button"
                                className="ai-chat__control ai-chat__control--send"
                                id="ai-send"
                                onClick={handleSend}
                                disabled={!input.trim() || disabled}
                                title="Send message"
                            >
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <line x1="5" y1="12" x2="19" y2="12"></line>
                                    <polyline points="12 5 19 12 12 19"></polyline>
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>

                {/* Settings Popover */}
                <div
                    className={`ai-chat__settings-popover ${showSettings ? '' : 'hidden'}`}
                    id="ai-settings-popover"
                    role="menu"
                    style={{ display: showSettings ? 'flex' : 'none' }}
                >
                    <button
                        className="button button--small ai-chat__settings-action"
                        type="button"
                        onClick={() => {
                            onClearHistory?.();
                            setShowSettings(false);
                        }}
                    >
                        Clear history
                    </button>
                    <button
                        className="button button--small ai-chat__settings-action"
                        type="button"
                        onClick={() => {
                            onExportChat?.();
                            setShowSettings(false);
                        }}
                    >
                        Export chat
                    </button>
                </div>

                {/* MCP Tools Dropdown */}
                <div
                    className={`ai-chat__tools-dropdown ${showTools ? '' : 'hidden'}`}
                    id="ai-tools-dropdown"
                    style={{ display: showTools ? 'flex' : 'none' }}
                >
                    <div className="ai-chat__tools-dropdown-header">MCP Servers</div>
                    <div className="ai-chat__tools-list" id="ai-tools-list">
                        {mcpServers.length === 0 ? (
                            <div className="ai-chat__tools-loading">
                                {showTools ? 'Loading...' : 'No MCP servers available'}
                            </div>
                        ) : (
                            mcpServers.map((server) => (
                                <div key={server.name} className="ai-chat__tool-item">
                                    <div className="ai-chat__tool-info">
                                        <div className="ai-chat__tool-name">{server.name}</div>
                                        <div className="ai-chat__tool-meta">
                                            {!server.connected && (
                                                <span className="ai-chat__tool-meta-badge">Disconnected</span>
                                            )}
                                            {server.persistedDisabled && (
                                                <span className="ai-chat__tool-meta-badge">Persistently disabled</span>
                                            )}
                                        </div>
                                        <div className="ai-chat__tool-desc">
                                            {server.toolCount} tool{server.toolCount !== 1 ? 's' : ''}
                                        </div>
                                    </div>
                                    <label className="ai-chat__tool-toggle">
                                        <input
                                            type="checkbox"
                                            checked={server.enabled}
                                            onChange={(e) => handleServerToggle(server.name, e.target.checked)}
                                        />
                                        <span className="ai-chat__tool-toggle-slider"></span>
                                    </label>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
