# 🗺️ OpenHubUI Development Roadmap

**Vision:** Transform OpenWebUI into a complete AI platform by providing unified access to tools, models, audio services, and AI agents through a single, elegant interface.

---

## 📊 Overview

| Phase | Status | Target | Description |
|-------|--------|--------|-------------|
| Phase 1 | ✅ **Complete** | v1.0 | Core MCP Proxy |
| Phase 2 | 🔄 **In Progress** | v1.5 | Audio Services |
| Phase 3 | 📅 **Planned** | v2.0 | AI Provider Aggregation |
| Phase 4 | 🔮 **Future** | v3.0 | Sandboxed AI Agents |

---

## ✅ Phase 1: Core MCP Proxy (v1.0) - **COMPLETE**

**Goal:** Production-ready MCP tools proxy with comprehensive management capabilities

### Completed Features

#### Core Infrastructure
- [x] Dual proxy architecture (8000 admin + 8001 streamable)
- [x] MCP protocol support (stdio, SSE, streamable-http)
- [x] Multi-server configuration via `mcpo.json`
- [x] Hot-reload configuration changes
- [x] State persistence (`mcpo_state.json`)
- [x] Unified state management (StateManager singleton)

#### Management & Control
- [x] Admin UI at `/ui`
  - Server status dashboard
  - Tool management interface
  - Configuration viewer
  - Health monitoring
- [x] Granular enable/disable (server & tool level)
- [x] API key authentication
- [x] CORS support

#### Developer Experience
- [x] Auto-generated OpenAPI documentation
- [x] Per-server API docs (`/{server}/docs`)
- [x] Comprehensive test suite (30+ tests)
- [x] Error handling & logging
- [x] Metrics endpoint

#### Recent Improvements
- [x] Fixed Starlette 0.48+ compatibility
- [x] Unified config files (mcpo.json everywhere)
- [x] Removed duplicate state systems
- [x] Cleaned up project structure

### Release Criteria (v1.0)
- [ ] Pass all tests
- [ ] Documentation complete
- [ ] Security audit
- [ ] Performance benchmarks
- [ ] Docker deployment tested
- [ ] OpenWebUI integration guide

---

## 🔄 Phase 2: Audio Services (v1.5) - **IN PROGRESS**

**Goal:** Add speech-to-text and text-to-speech capabilities for voice-enabled AI interactions

**Status:** Experimental - Framework exists, needs testing & completion

### Current State

#### Existing Components
- `audio/whisper-server/` - Whisper model server
  - WIN implementation (Python)
  - MLX implementation (Mac)
- `audio/TTS-chrome-ext/` - Browser extension for TTS
- `src/mcpo/api/routers/transcribe.py` - WebSocket endpoint
- `src/mcpo/services/transcribe_tokens.py` - Session management
- `start.bat` - Whisper server startup (port 8002)

### Roadmap

#### 2.1: Speech-to-Text (Whisper)
- [ ] Complete Whisper server integration
  - [ ] Test WIN implementation
  - [ ] Test MLX implementation (Mac)
  - [ ] Add model selection UI
  - [ ] Performance optimization
- [ ] File upload endpoint
  - [ ] `POST /audio/transcribe` (file → text)
  - [ ] Support multiple audio formats
  - [ ] Batch processing
- [ ] Configuration
  - [ ] Model size selection (tiny, base, small, medium, large)
  - [ ] Language selection
  - [ ] Device selection (CPU/GPU)

#### 2.2: Real-Time Transcription
- [ ] WebSocket streaming
  - [ ] `WS /ws/transcribe` endpoint
  - [ ] Streaming audio chunks
  - [ ] Partial results
  - [ ] Final transcription
- [ ] Session management
  - [ ] Token-based sessions
  - [ ] Concurrent session limits
  - [ ] Session cleanup
- [ ] Latency optimization
  - [ ] Buffer management
  - [ ] Model warm-up
  - [ ] GPU acceleration

#### 2.3: Text-to-Speech
- [ ] Server implementation
  - [ ] `POST /audio/synthesize` (text → audio)
  - [ ] Voice selection
  - [ ] Format selection (mp3, wav, ogg)
- [ ] Provider options
  - [ ] Local: pyttsx3, Coqui TTS
  - [ ] Cloud: ElevenLabs, OpenAI TTS
  - [ ] Configuration per provider
- [ ] Streaming TTS
  - [ ] `WS /ws/tts` for real-time
  - [ ] Chunk-based streaming
  - [ ] Low-latency mode

#### 2.4: Integration
- [ ] OpenWebUI integration
  - [ ] Voice input button
  - [ ] Audio playback controls
  - [ ] Transcription history
- [ ] UI enhancements
  - [ ] Audio settings page
  - [ ] Active session monitoring
  - [ ] Model management
- [ ] Testing
  - [ ] End-to-end audio pipeline
  - [ ] Latency measurements
  - [ ] Quality benchmarks

### Release Criteria (v1.5)
- [ ] Whisper working on WIN & Mac
- [ ] Real-time transcription < 500ms latency
- [ ] TTS with multiple voices
- [ ] OpenWebUI integration tested
- [ ] Audio documentation complete

---

## 📅 Phase 3: AI Provider Aggregation (v2.0) - **PLANNED**

**Goal:** Unified interface to multiple AI providers through OpenAI-compatible API

**Why:** OpenWebUI requires custom pipes or LiteLLM for non-OpenAI providers. OpenHubUI will provide native access to all major providers through one endpoint.

### Architecture

```
POST /v1/chat/completions
├── model=gpt-4         → OpenAI API
├── model=claude-3.5    → Anthropic API (via Vercel SDK)
├── model=gemini-2.0    → Google AI (via Vercel SDK)
└── model=*             → 50+ providers via Vercel SDK
```

### Components

#### 3.1: Foundation
- [ ] Vercel AI SDK integration
  - [ ] Resurrect/refactor `src/ai_service/`
  - [ ] Node.js service alongside Python
  - [ ] Provider adapters
- [ ] OpenAI-compatible endpoint
  - [ ] `POST /v1/chat/completions`
  - [ ] `GET /v1/models`
  - [ ] Request/response normalization
- [ ] Streaming support
  - [ ] SSE format
  - [ ] Token-by-token streaming
  - [ ] Function call streaming

#### 3.2: Provider Integration
- [ ] **Tier 1: Direct Python** (Simple)
  - [ ] OpenAI
  - [ ] OpenAI-compatible APIs
  - [ ] Local models (Ollama)
- [ ] **Tier 2: Via Vercel SDK** (Complex)
  - [ ] Anthropic (Claude)
  - [ ] Google (Gemini)
  - [ ] Mistral
  - [ ] Cohere
  - [ ] Groq
  - [ ] 40+ additional providers

#### 3.3: Management
- [ ] Model registry
  - [ ] Available models list
  - [ ] Provider credentials
  - [ ] Default parameters
- [ ] UI enhancements
  - [ ] Models page
  - [ ] Provider configuration
  - [ ] Usage statistics
  - [ ] Cost tracking
- [ ] Configuration
  ```json
  {
    "providers": {
      "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-4", "gpt-3.5-turbo"]
      },
      "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-3.5-sonnet"]
      }
    }
  }
  ```

#### 3.4: Advanced Features
- [ ] Function calling normalization
  - [ ] Convert between provider formats
  - [ ] MCP tools as functions
  - [ ] Nested tool calls
- [ ] Response caching
  - [ ] Semantic cache
  - [ ] Prompt cache
  - [ ] Cost optimization
- [ ] Load balancing
  - [ ] Provider failover
  - [ ] Rate limit handling
  - [ ] Cost-based routing

### Technical Approach

#### Option A: Embedded Node Service (Recommended)
```python
# Start Node service as subprocess
node_service = subprocess.Popen(["node", "ai_service/dist/server.js"])

# Proxy complex providers to Node
async def handle_claude(request):
    return await httpx.post("http://localhost:8003/anthropic", json=request)
```

#### Option B: Pure Python
- Manually implement provider APIs
- More work, but single runtime

### Release Criteria (v2.0)
- [ ] OpenAI-compatible endpoint working
- [ ] 5+ providers integrated
- [ ] Streaming support complete
- [ ] OpenWebUI tested with all providers
- [ ] Performance benchmarks (< 100ms overhead)
- [ ] Provider documentation

---

## 🔮 Phase 4: Sandboxed AI Agents (v3.0) - **FUTURE**

**Goal:** Create specialized AI agents with constrained outputs, accessible as MCP tools

**Vision:** Allow other AI systems to call specialized agents for complex tasks, with guaranteed structured outputs and sandboxed execution.

### Concept

#### Agent Definition
```json
{
  "agents": {
    "legal-analyzer": {
      "model": "gpt-4",
      "system_prompt": "You are a legal document analyzer...",
      "knowledge_base": ["./legal-kb/*.pdf"],
      "tools": ["search_precedents", "extract_clauses"],
      "output_schema": {
        "type": "object",
        "properties": {
          "risk_level": {"enum": ["low", "medium", "high"]},
          "concerns": {"type": "array"},
          "recommendations": {"type": "array"}
        },
        "required": ["risk_level", "concerns"]
      },
      "sandbox": {
        "max_tokens": 4000,
        "timeout": 30,
        "rate_limit": "10/minute",
        "allowed_tools": ["search_precedents"]
      }
    }
  }
}
```

#### Usage
```python
# Automatically exposed as MCP tool
POST /mcp/legal-analyzer/analyze
{
  "document": "Contract text..."
}

# Returns structured output
{
  "risk_level": "medium",
  "concerns": ["Clause 3.2 ambiguous"],
  "recommendations": ["Add force majeure"]
}
```

### Roadmap

#### 4.1: Foundation
- [ ] Agent configuration schema
- [ ] Agent runner service
- [ ] Structured output validation
- [ ] Sandbox enforcement

#### 4.2: Knowledge Base Integration
- [ ] Document ingestion
- [ ] Vector database (ChromaDB/Qdrant)
- [ ] RAG pipeline
- [ ] Document management UI

#### 4.3: Agent Management
- [ ] Agent creation UI
- [ ] System prompt editor
- [ ] Output schema designer
- [ ] Testing interface

#### 4.4: Advanced Features
- [ ] Multi-agent orchestration
- [ ] Agent chaining
- [ ] Stateful conversations
- [ ] Learning & feedback

### Use Cases

#### Example Agents
1. **Legal Document Analyzer**
   - Input: Contract text
   - Output: Risk assessment + recommendations
   
2. **Code Reviewer**
   - Input: Git diff
   - Output: Review checklist + security concerns

3. **Medical Triage**
   - Input: Symptoms description
   - Output: Urgency level + specialist recommendation

4. **Financial Advisor**
   - Input: Portfolio data
   - Output: Risk analysis + rebalancing suggestions

### Release Criteria (v3.0)
- [ ] Agent configuration working
- [ ] 3+ example agents
- [ ] RAG pipeline tested
- [ ] Sandbox enforcement verified
- [ ] Agent management UI
- [ ] Security audit

---

## 🎯 Milestones

### Q1 2025: Stable Core
- ✅ Phase 1 complete (v1.0)
- 📦 Docker Hub release
- 📚 Complete documentation
- 🌐 OpenWebUI partnership

### Q2 2025: Audio Integration
- 🔄 Phase 2 complete (v1.5)
- 🎤 Whisper server production-ready
- 🔊 TTS integration
- 📱 Mobile-friendly audio UI

### Q3 2025: Provider Aggregation
- 📅 Phase 3 complete (v2.0)
- 🤖 5+ AI providers
- 💰 Cost tracking
- ⚡ Sub-100ms latency

### Q4 2025: AI Agents
- 🔮 Phase 4 alpha
- 🧪 Agent framework
- 📖 Agent examples
- 🔐 Sandbox testing

---

## 🚀 Contributing

Want to help build the future? We welcome contributions to all phases!

### How to Contribute

1. **Phase 1 (Polish)**
   - Bug fixes
   - Performance improvements
   - Documentation

2. **Phase 2 (Audio)**
   - Test Whisper on different platforms
   - TTS provider integration
   - Latency optimization

3. **Phase 3 (Providers)**
   - Provider adapters
   - Response normalization
   - Cost tracking

4. **Phase 4 (Agents)**
   - Agent templates
   - RAG improvements
   - Security review

### Development Setup

```bash
# Clone and setup
git clone https://github.com/open-webui/mcpo.git
cd mcpo
uv sync --dev

# Run tests
uv run pytest

# Start development server
uv run mcpo serve --config mcpo.json --hot-reload
```

### Branches

- `main` - Stable releases only
- `develop` - Integration branch
- `feature/*` - Feature development
- `experimental/*` - Experimental features (audio, agents)

---

## 📞 Contact & Community

- **GitHub Issues**: Bug reports & feature requests
- **Discussions**: Architecture & roadmap discussion
- **OpenWebUI Discord**: Integration support

---

**Last Updated:** January 2025  
**Next Review:** After Phase 2 completion

---

*Built with ❤️ for the OpenWebUI community*
