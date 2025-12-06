# 🧪 Development & Experimental Features

This directory contains experimental features and reference implementations that are **not part of the stable release**.

---

## 📂 Contents

### `audio/` - Audio Services (Phase 2 - Experimental)
Speech-to-text and text-to-speech capabilities.

**Status:** Framework exists, needs testing

**Components:**
- `whisper-server/WIN/` - Windows Whisper implementation
- `whisper-server/MLX/` - Mac MLX Whisper implementation
- `TTS-chrome-ext/` - Text-to-speech browser extension

**Documentation:**
- `VIABILITY_WHISPER_STREAMING.md` - Implementation viability analysis
- `REAL_TIME_TRANSCRIPTION_CHANGES.md` - Real-time streaming requirements

**To use:** See Phase 2 in [ROADMAP.md](../ROADMAP.md)

---

### `ai_service/` - AI Provider Service (Phase 3 - Planned)
Node.js service for Vercel AI SDK integration.

**Status:** Deprecated stub from earlier iteration

**Purpose:** 
- Proxy AI providers (OpenAI, Claude, Gemini)
- Use Vercel AI SDK for provider abstraction
- Serve via OpenAI-compatible API

**Next Steps:**
- Refactor or rebuild for Phase 3
- See [ROADMAP.md](../ROADMAP.md) Phase 3 for architecture

---

### `vercel-sdk-full-git-repo/` - Reference Implementation
Complete Vercel AI SDK repository for reference.

**Status:** Reference material only

**Usage:**
- Study provider implementations
- Understand Vercel SDK patterns
- Copy patterns for Phase 3

**Note:** Do not integrate directly, use as reference

---

## 🚫 Not for Production

**Nothing in this directory is included in stable releases.**

### Stable Code Location
```
src/mcpo/          - Core proxy (Production)
static/ui/         - Admin UI (Production)
tests/             - Test suite (Production)
```

### Experimental Code Location
```
dev/audio/         - Audio services (Experimental)
dev/ai_service/    - Provider aggregation (Planned)
vercel-sdk-full-git-repo/  - Reference (Do not run)
```

---

## 🔄 Feature Lifecycle

1. **Experimental** (in `dev/`)
   - Proof of concept
   - Active development
   - May break or change

2. **Beta** (feature branch)
   - Stable API
   - Testing phase
   - Documentation draft

3. **Stable** (main branch)
   - Production ready
   - Full documentation
   - Moved out of `dev/`

---

## 📋 Branch Strategy

### Working on Experimental Features

```bash
# Create feature branch
git checkout -b feature/transcription

# Work in dev/ directory
# ... make changes in dev/audio/ ...

# Commit experimental work
git add dev/audio/
git commit -m "feat(audio): add streaming transcription"

# Keep main branch clean
git checkout main  # No audio/ here!
```

### Promoting to Stable

```bash
# When feature is production-ready:
git checkout main
git merge feature/transcription

# Move out of dev/ to appropriate location
mv dev/audio src/mcpo/audio
git add src/mcpo/audio
git commit -m "feat: promote audio to stable"
```

---

## 🧪 Testing Experimental Features

### Audio Services (Phase 2)

```bash
# Start Whisper server (Windows)
cd dev/audio/whisper-server/WIN
python api_server.py

# Or Mac
cd dev/audio/whisper-server/MLX
python api_server.py

# Start main app with transcription
cd ../../..
mcpo serve --config mcpo.json

# Transcription available at:
# - WS: ws://localhost:8000/ws/transcribe
# - HTTP: POST http://localhost:8000/audio/transcribe
```

### AI Service (Phase 3)

```bash
# Install dependencies
cd dev/ai_service
npm install

# Run standalone
npm start

# Or integrate with MCPO
# (Architecture TBD in Phase 3)
```

---

## 📖 Documentation

- [Main README](../README.md) - Project overview
- [ROADMAP](../ROADMAP.md) - Development timeline
- [CHANGELOG](../CHANGELOG.md) - Release history

---

## 🤝 Contributing to Experimental Features

We welcome contributions to experimental features!

### Guidelines

1. **Work in `dev/`** - Keep experimental code isolated
2. **Document thoroughly** - Help others understand your work
3. **Write tests** - Even for experimental features
4. **Update ROADMAP** - Track progress

### Pull Requests

```
feat(audio): add real-time transcription
- Implemented WebSocket streaming
- Added buffer management
- Tests for latency < 500ms

Status: Experimental (Phase 2)
```

---

**Questions?** Open an issue or discussion on GitHub.

---

*Last Updated: January 2025*
