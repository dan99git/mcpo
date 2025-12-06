import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { ToolStatusBar } from '../tool-status';
import { useChatStore } from '../chat-store';

describe('ToolStatusBar', () => {
  beforeEach(() => {
    act(() => {
      useChatStore.setState((state) => ({
        ...state,
        toolCounts: { baseline: 2, panel: 1, mcp: 1, total: 4 },
        toolOrigins: {
          open_panel: 'baseline',
          open_home_screen: 'baseline',
          'panel.inspect_device': 'panel',
          'mcp.fetch_logs': 'mcp',
        },
      }));
    });
  });

  afterEach(() => {
    act(() => {
      useChatStore.setState((state) => ({
        ...state,
        toolCounts: { baseline: 0, panel: 0, mcp: 0, total: 0 },
        toolOrigins: {},
      }));
    });
  });

  it('renders tool counts and opens baseline tooltip', () => {
    render(<ToolStatusBar />);

    expect(screen.getByText('Tools:')).toBeInTheDocument();
    expect(screen.getByText('4')).toHaveClass('ai-chat__tool-count');

    const baselineBadge = screen.getByTitle('Click to view 2 baseline tools');
    fireEvent.click(baselineBadge);

    expect(screen.getByText('Baseline Tools')).toBeInTheDocument();
    expect(screen.getByText('open_panel')).toBeInTheDocument();
    expect(screen.getByText('open_home_screen')).toBeInTheDocument();
  });

  it('closes popup when the close button is pressed', () => {
    render(<ToolStatusBar />);
    const panelBadge = screen.getByTitle('Click to view 1 panel tool');

    fireEvent.click(panelBadge);
    const closeButton = screen.getByRole('button', { name: /close/i });
    fireEvent.click(closeButton);

    expect(screen.queryByText('Panel Tools')).not.toBeInTheDocument();
  });
});
