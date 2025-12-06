// @ts-nocheck
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuralisPanel, type AuralisViewConfig } from '../hooks/useAuralisPanel';
import type {
  AgentCommand,
  AuralisView,
  CommandResult,
  Control,
} from '../types/auralis-bridge';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: MockWebSocket[] = [];

  public readyState = MockWebSocket.CONNECTING;
  public onopen: ((event: Event) => void) | null = null;
  public onmessage: ((event: MessageEvent) => void) | null = null;
  public onclose: ((event: Event) => void) | null = null;
  public onerror: ((event: Event) => void) | null = null;
  public send = vi.fn();
  public close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new Event('close'));
  });

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN;
      this.onopen?.(new Event('open'));
    }, 0);
  }
}

declare global {
  interface Window {
    __AURALIS_VIEW__?: AuralisView;
  }
}

describe('useAuralisPanel', () => {
  const originalWebSocket = globalThis.WebSocket;

  const createConfig = (
    overrides: Partial<AuralisViewConfig> = {}
  ): AuralisViewConfig => {
    const defaultControls: Control[] = [
      {
        selector: '#btn-primary',
        label: 'Primary',
        type: 'button',
        actionId: 'primary.action',
        enabled: true,
      },
    ];

    return {
      name: 'test-panel',
      version: '1.0.0',
      surfaceType: 'html',
      getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0', test: 'context' }),
      getVisibleControls: () => defaultControls,
      onCommand: async (_command: AgentCommand): Promise<CommandResult> => ({ success: true }),
      ...overrides,
    };
  };

  const flushBridge = async (): Promise<AuralisView> => {
    await act(async () => {
      vi.runAllTimers();
    });

    await waitFor(() => {
      expect(window.__AURALIS_VIEW__).toBeDefined();
    });

    return window.__AURALIS_VIEW__!;
  };

  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    delete window.__AURALIS_VIEW__;
    document.body.innerHTML = '';
  });

  afterEach(() => {
    vi.runAllTimers();
    vi.useRealTimers();
    globalThis.WebSocket = originalWebSocket;
    delete window.__AURALIS_VIEW__;
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('exposes the Auralis bridge on window with context and controls', async () => {
    const { unmount } = renderHook(() => useAuralisPanel(createConfig()));

    const view = await flushBridge();

    expect(view.panelName).toBe('test-panel');
    expect(view.panelVersion).toBe('1.0.0');
    expect(view.context).toMatchObject({ test: 'context' });
    expect(Array.isArray(view.visibleControls)).toBe(true);
    expect(typeof view.executeCommand).toBe('function');

    unmount();
  });

  it('prioritizes the invoke handler when an actionId is provided', async () => {
    const invokeHandler = vi.fn(async (command: AgentCommand): Promise<CommandResult> => ({
      success: true,
      data: { via: 'invoke', command },
    }));

    const clickSpy = vi.spyOn(HTMLElement.prototype, 'click');

    const { unmount } = renderHook(() =>
      useAuralisPanel(
        createConfig({
          onCommand: invokeHandler,
        })
      )
    );

    const view = await flushBridge();

    const result = await view.executeCommand({ actionId: 'test.action', args: { value: 42 } });

    expect(invokeHandler).toHaveBeenCalledTimes(1);
    expect(invokeHandler).toHaveBeenCalledWith({ actionId: 'test.action', args: { value: 42 } });
    expect(result.success).toBe(true);
    expect(result.data).toMatchObject({ via: 'invoke' });
    expect(clickSpy).not.toHaveBeenCalled();

    clickSpy.mockRestore();
    unmount();
  });

  it('falls back to DOM click execution when only a selector is provided', async () => {
    const triggerButton = document.createElement('button');
    triggerButton.id = 'command-button';
    triggerButton.textContent = 'Trigger';
    document.body.appendChild(triggerButton);

    const domClickSpy = vi.spyOn(triggerButton, 'click');

    const suppressInvoke = vi.fn(async (): Promise<CommandResult> => ({
      success: false,
      error: 'should not be called for selector-only commands',
    }));

    const { unmount } = renderHook(() =>
      useAuralisPanel(
        createConfig({
          onCommand: suppressInvoke,
        })
      )
    );

    const view = await flushBridge();

    const result = await view.executeCommand({ selector: '#command-button' });

    expect(domClickSpy).toHaveBeenCalledTimes(1);
    expect(result.success).toBe(true);
    expect(result.data).toMatchObject({ action: 'clicked', selector: '#command-button' });
    expect(suppressInvoke).not.toHaveBeenCalled();

    domClickSpy.mockRestore();
    triggerButton.remove();
    unmount();
  });
});
