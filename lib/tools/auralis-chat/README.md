# Auralis Chat - Clean Rewrite

**Status**: ✅ Complete (100%)

This folder contains a clean, from-scratch rewrite of the auralis-chat feature with:
- ✅ No dead code
- ✅ `panelId` consistency (not `panelName`)
- ✅ Heavy annotations and "Architecture Notes" in every file
- ✅ Flat folder structure
- ✅ Consistent **kebab-case** file naming
- ✅ All functionality preserved
- ✅ **Zero visual or behavioral changes**

## File Structure

### Core Infrastructure
- **types.ts** - Type definitions with `panelId` alignment
- **constants.ts** - Active constants only
- **chat-store.ts** - Zustand state management (formerly `store.ts`)
- **index.ts** - Barrel exports
- **env.d.ts** - Environment type definitions

### Utilities
- **formatting.ts** - Text/file/tool formatting utilities
- **workflow-state.ts** - "Thinking..." UI state management (formerly `workflowService.ts`)
- **tool-prefs.ts** - MCP server visibility settings (formerly `toolPreferences.ts`)

### Agent Bridge
- **use-panel-bridge.ts** - Panel bridge hook with `panelId` consistency (formerly `useAuralisPanel.ts`)
- **agent-service.ts** - Backend SSE communication (formerly `auralisAgentService.ts`)

### Voice Services
- **tts-service.ts** - TTS playback (formerly `ttsPlayer.ts`)
- **voice-service.ts** - Speech-to-text transcription (formerly `voiceApi.ts`)

### Message Handling
- **message-handler.ts** - Core message orchestration
- **use-chat-init.ts** - Panel initialization hook (formerly `useChatPanelInitialization.ts`)

### UI Components
- **chat-panel.tsx** - Main chat panel orchestrator (formerly `AiChatPanel.tsx`)
- **chat-input.tsx** - User input component (formerly `AiChatInput.tsx`)
- **chat-stream.tsx** - Message stream renderer (formerly `AiChatStream.tsx`)
- **tool-status.tsx** - Tool counts display (formerly `ToolStatusBar.tsx`)

## Key Changes from Original

1. **Terminology**: `panelName` → `panelId` throughout
2. **Naming Convention**: All files renamed to **kebab-case** for consistency.
3. **Dead Code Removed**:
   - `loadPanelContext` action from store
   - Legacy OpenRouter constants (`API_ENDPOINT`)
   - Quarantined localStorage logic in ttsPlayer
4. **Imports Updated**: All use local `./` imports
5. **Annotations**: Every file heavily documented with "Architecture Notes" headers.
6. **Behavioral Parity**: 100% - no functional or visual changes
