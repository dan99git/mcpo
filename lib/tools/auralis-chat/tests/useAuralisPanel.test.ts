// @ts-nocheck
/**
 * Tests for useAuralisPanel Hook
 * 
 * Verifies optimized command execution that prioritizes invoke() handlers
 * over DOM manipulation for speed and accuracy
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useAuralisPanel } from '../hooks/useAuralisPanel';
import type { AuralisViewConfig } from '../hooks/useAuralisPanel';

// Mock WebSocket - must be a proper constructor function for Vitest
const MockWebSocket = vi.fn().mockImplementation(function(this: WebSocket) {
  this.readyState = WebSocket.CONNECTING;
  this.onopen = null;
  this.onclose = null;
  this.onerror = null;
  this.onmessage = null;
  this.send = vi.fn();
  this.close = vi.fn();
  
  setTimeout(() => {
    this.readyState = WebSocket.OPEN;
    if (this.onopen) {
      this.onopen(new Event('open'));
    }
  }, 10);
  
  return this;
}) as unknown;

global.WebSocket = MockWebSocket;

describe('useAuralisPanel Hook', () => {
  beforeEach(() => {
    // Clear window.__AURALIS_VIEW__ before each test (if window exists)
    if (typeof window !== 'undefined') {
      delete (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
    }
    vi.clearAllMocks();
  });

  afterEach(() => {
    // Cleanup (window cleanup handled in setup.ts)
    vi.clearAllMocks();
  });

  describe('Bridge Exposure', () => {
    it('should expose window.__AURALIS_VIEW__ on mount', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0', test: 'context' }),
        getVisibleControls: () => [],
        onCommand: async () => ({ success: true }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
        expect((window as any).__AURALIS_VIEW__.panelName).toBe('test-panel');
      });
    });

    it('should expose context, visibleControls, and executeCommand', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0', test: 'context' }),
        getVisibleControls: () => [{ selector: '#test', label: 'Test', type: 'button' as const, enabled: true }],
        onCommand: async () => ({ success: true }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
        expect(view.context).toMatchObject({ test: 'context' });
        expect(view.visibleControls).toHaveLength(1);
        expect(typeof view.executeCommand).toBe('function');
      });
    });
  });

  describe('Command Execution Optimization', () => {
    it('should PRIORITIZE invoke() handler over DOM clicks when actionId provided', async () => {
      const invokeHandler = vi.fn().mockResolvedValue({ success: true, data: { from: 'invoke' } });
      const clickSpy = vi.spyOn(HTMLElement.prototype, 'click');

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: invokeHandler,
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      // Execute command with actionId (should use invoke handler)
      const result = await view.executeCommand({
        actionId: 'test.action',
        args: { value: 123 },
      });

      // Should call invoke handler, NOT DOM click
      expect(invokeHandler).toHaveBeenCalledWith({
        actionId: 'test.action',
        args: { value: 123 },
      });
      expect(clickSpy).not.toHaveBeenCalled();
      expect(result.success).toBe(true);
      expect(result.data.from).toBe('invoke');
    });

    it('should fallback to DOM click ONLY when no actionId provided', async () => {
      const invokeHandler = vi.fn();
      const clickSpy = vi.spyOn(HTMLElement.prototype, 'click');

      // Create a DOM element inside a container that will be found
      const container = document.createElement('div');
      container.id = 'test-container';
      container.style.display = 'block'; // Make container visible
      const button = document.createElement('button');
      button.id = 'test-button';
      button.setAttribute('data-action', 'test.action');
      button.style.display = 'block'; // Make button visible
      container.appendChild(button);
      document.body.appendChild(container);

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: invokeHandler,
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      // Wait for element to be in DOM
      await waitFor(() => {
        const button = document.querySelector('#test-button');
        expect(button).toBeTruthy();
      });
      
      // Execute command with selector only (no actionId) - should use DOM click
      const result = await view.executeCommand({
        selector: '#test-button',
      });

      // Should NOT call invoke handler when no actionId is provided
      expect(invokeHandler).not.toHaveBeenCalled();
      
      // Should click DOM element
      expect(clickSpy).toHaveBeenCalled();
      expect(result.success).toBe(true);

      document.body.removeChild(container);
    });

    it('should have NO delay when using invoke() handler (optimized path)', async () => {
      const startTime = Date.now();
      const invokeHandler = vi.fn().mockImplementation(async () => {
        return { success: true, data: { timestamp: Date.now() } };
      });

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: invokeHandler,
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      // Execute command
      const result = await view.executeCommand({
        actionId: 'test.action',
      });

      const endTime = Date.now();
      const elapsed = endTime - startTime;

      // Should complete quickly (no artificial delays) - allow some margin for test environment
      expect(elapsed).toBeLessThan(50);
      expect(result.success).toBe(true);
    });

    it('should have NO delay when using DOM click (removed 100ms delay)', async () => {
      const clickSpy = vi.spyOn(HTMLElement.prototype, 'click');

      const container = document.createElement('div');
      container.id = 'test-container';
      container.style.display = 'block'; // Make container visible
      const button = document.createElement('button');
      button.id = 'test-button';
      button.style.display = 'block'; // Make button visible
      container.appendChild(button);
      document.body.appendChild(container);

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: async () => ({ success: false }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      // Wait for element to be in DOM
      await waitFor(() => {
        const button = document.querySelector('#test-button');
        expect(button).toBeTruthy();
      });
      
      const startTime = Date.now();
      const result = await view.executeCommand({
        selector: '#test-button',
      });
      const elapsed = Date.now() - startTime;

      // Should complete immediately (no 100ms delay)
      expect(elapsed).toBeLessThan(100); // More lenient for test environment
      expect(result.success).toBe(true);
      expect(clickSpy).toHaveBeenCalled();

      document.body.removeChild(container);
    });
  });

  describe('Error Handling', () => {
    it('should handle invoke() handler errors gracefully', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: async () => {
          throw new Error('Handler error');
        },
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      const result = await view.executeCommand({
        actionId: 'test.action',
      });

      expect(result.success).toBe(false);
      expect(result.error).toContain('Handler error');
    });

    it('should handle missing selector gracefully', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: async () => ({ success: false }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      const view = (window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__;
      
      const result = await view.executeCommand({
        selector: '#nonexistent',
      });

      expect(result.success).toBe(false);
      expect(result.error).toBeDefined();
      if (result.error) {
        expect(result.error).toContain('not found');
      }
    });
  });

  describe('WebSocket Integration', () => {
    it('should connect to WebSocket on mount', async () => {
      const wsSpy = vi.spyOn(global, 'WebSocket');

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: async () => ({ success: true }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect(wsSpy).toHaveBeenCalled();
      }, { timeout: 2000 });
    });

    it('should send initial panel state to WebSocket', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0', test: 'context' }),
        getVisibleControls: () => [{ selector: '#test', label: 'Test', type: 'button' as const, enabled: true }],
        onCommand: async () => ({ success: true }),
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((MockWebSocket as any).mock.instances?.length).toBeGreaterThan(0);
      }, { timeout: 2000 });

      const ws = (MockWebSocket as any).mock.instances[0];
      expect(ws.send).toHaveBeenCalled();

      const sentMessages = ws.send.mock.calls.map((call: any[]) => JSON.parse(call[0]));
      const stateMessage = sentMessages.find((msg: any) => msg.type === 'panel_state');

      expect(stateMessage).toBeDefined();
      expect(stateMessage.panelName).toBe('test-panel');
      expect(stateMessage.context).toMatchObject({ test: 'context' });
    });

    it('should handle WebSocket command messages', async () => {
      const commandHandler = vi.fn().mockResolvedValue({ success: true });

      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: commandHandler,
      };

      renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((MockWebSocket as any).mock.instances?.length).toBeGreaterThan(0);
      }, { timeout: 2000 });

      const wsInstances = (MockWebSocket as any).mock.instances || [];
      const ws = wsInstances[wsInstances.length - 1];
      ws.onmessage?.({
        data: JSON.stringify({
          type: 'command',
          commandId: 'test-123',
          command: { actionId: 'test.action', args: { value: 123 } },
        }),
      });

      await waitFor(() => {
        expect(commandHandler).toHaveBeenCalledWith({
          actionId: 'test.action',
          args: { value: 123 },
        });
      }, { timeout: 2000 });
    });
  });

  describe('Cleanup', () => {
    it('should cleanup window.__AURALIS_VIEW__ on unmount', async () => {
      const config: AuralisViewConfig = {
        name: 'test-panel',
        version: '1.0.0',
        surfaceType: 'html',
        getContext: () => ({ panelName: 'test-panel', panelVersion: '1.0.0' }),
        getVisibleControls: () => [],
        onCommand: async () => ({ success: true }),
      };

      const { unmount } = renderHook(() => useAuralisPanel(config));

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeDefined();
      });

      unmount();

      await waitFor(() => {
        expect((window as typeof window & { __AURALIS_VIEW__?: unknown }).__AURALIS_VIEW__).toBeUndefined();
      });
    });
  });
});

