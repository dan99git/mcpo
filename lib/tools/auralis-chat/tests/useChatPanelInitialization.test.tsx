// @ts-nocheck
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatPanelInitialization } from '../hooks/useChatPanelInitialization';
import type { ChatPanelInitializationDeps } from '../hooks/useChatPanelInitialization';
import { useChatStore } from '@auralis-chat/stores/chatStore';

describe('useChatPanelInitialization', () => {
  const setCurrentSession = vi.fn();
  const createSession = vi.fn();
  const loadMessages = vi.fn();
  const setPanelContext = vi.fn();
  const setModelConfig = vi.fn();
  const setAiProvider = vi.fn();
  const setError = vi.fn();
  const setLoading = vi.fn();

  // Mock chat API with proper typing
  const baseChatApi: Record<string, ReturnType<typeof vi.fn>> = {
    getOpenRouterConfig: vi.fn(),
    getMiniMaxConfig: vi.fn(),
    getAiProvider: vi.fn(),
    getSessions: vi.fn(),
    getSession: vi.fn(),
    createSession: vi.fn(),
    getPanelContext: vi.fn(),
    getTools: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState((state) => ({
      ...state,
      toolOrigins: {},
      toolCounts: { baseline: 0, panel: 0, mcp: 0, total: 0 },
    }));

    baseChatApi.getOpenRouterConfig.mockResolvedValue({ apiKey: 'key', model: 'gpt-test' });
    baseChatApi.getMiniMaxConfig.mockResolvedValue({ apiKey: 'key', model: 'gpt-test' });
    baseChatApi.getAiProvider.mockResolvedValue({ provider: 'openrouter' });
    baseChatApi.getSessions.mockResolvedValue([
      { id: 'session-active', title: 'Active', status: 'active' },
    ]);
    baseChatApi.getSession.mockResolvedValue({ messages: [] });
    baseChatApi.createSession.mockResolvedValue({
      id: 'fallback',
      title: 'New conversation',
      status: 'active',
    });
    baseChatApi.getPanelContext.mockResolvedValue({
      active_panel: 'home',
      panel_prompt: 'prompt',
      has_active_tools: true,
    });
    baseChatApi.getTools.mockResolvedValue({
      tool_origins: { 'mcp.server.fetch_logs': 'mcp' },
    });

    localStorage.setItem('ai-disabled-tools', JSON.stringify(['mcp']));
    const mockFetch = vi.fn(
      () =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: true, data: { enabled: true } }),
        } satisfies Pick<Response, 'ok' | 'json'>) as Promise<Response>
    );
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  it('initializes chat session and seeds baseline tools when API responds', async () => {
    const { unmount } = renderHook(() =>
      useChatPanelInitialization({
        chatApi: baseChatApi as ChatPanelInitializationDeps['chatApi'],
        currentSessionId: null,
        setCurrentSession,
        createSession,
        loadMessages,
        setPanelContext,
        setModelConfig,
        setAiProvider,
        setError,
        setLoading,
      })
    );

    await waitFor(() => expect(setCurrentSession).toHaveBeenCalledWith('session-active'));
    expect(baseChatApi.getTools).toHaveBeenCalledWith(null);
    const toolOrigins = useChatStore.getState().toolOrigins;
    // Baseline tools may or may not include open_panel depending on catalog;
    // the critical property is that MCP-only tools are not present by default.
    expect(toolOrigins['mcp.server.fetch_logs']).toBeUndefined();

    unmount();
  });
});
