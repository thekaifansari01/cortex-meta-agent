# 🧠 Cortex: The Autonomous Desktop Agent (Meta Llama 3 Powered)

> **An Open-Source AI Operating System that controls your PC, executes terminal commands, manages files, and remembers your life — powered by Meta Llama 3.**

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-3772FF?style=for-the-badge&logo=python&logoColor=white)]()
[![Meta Llama 3](https://img.shields.io/badge/Meta%20Llama%203-0467DF?style=for-the-badge&logo=meta&logoColor=white)]()
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white)]()
[![Runs On](https://img.shields.io/badge/Runs%20On-4GB%20RAM%20%2F%20i5--3210M-critical?style=for-the-badge)]()

---

## 💡 Why Cortex? (Impact)

Current AI assistants (ChatGPT, Claude web) are trapped in browsers. They can't open your apps, organize your files, or send an actual WhatsApp message. 

**Cortex bridges the gap between LLM intelligence and OS-level execution.**

*   **For Developers:** Automates tedious workflows like renaming files, running terminal commands, and writing boilerplate code directly in your workspace.
*   **For Power Users:** "Send an email to my boss with the report I just generated, then WhatsApp my team that it's done." Cortex actually executes this, asking for your approval before sending.
*   **Zero-Cloud Latency:** Runs entirely on your local machine as a background service, waking up via a global hotkey (`Ctrl+Shift+J`).

---

## 🚀 The Meta AI Integration (Meta AI Tooling)

Cortex uses **Meta Llama 3 (via Meta's API)** as its primary reasoning engine. We didn't just build a basic wrapper; we built a provider-agnostic agentic loop specifically optimized for Llama's function-calling and reasoning capabilities.

1.  **Native Tool Calling:** Llama 3 parses complex user intents and maps them to 15+ native OS tools (GUI, Terminal, Email, WhatsApp).
2.  **Dynamic Prompt Injection:** Meta's models receive a highly optimized system prompt with "Live Overrides" and "Budget Tracking" (preventing infinite loops).
3.  **Semantic Deduplication:** We use Meta's embedding capabilities (or fallback to `bge-small`) to build a **Lifetime Memory Graph**, ensuring Llama remembers your preferences across sessions without hallucinating.
4.  **Provider Architecture:** Cortex uses a custom `BaseLLMProvider` class. Adding Meta Llama 3 required only a few lines of code, proving the extensibility of the system.

---

## 🛠️ Technical Depth & Execution

Cortex is engineered with a focus on **reliability, speed, and safety**. Here is what happens under the hood:

### 1. The Agentic Brain (Self-Correction Loop)
*   **Truncation Detection:** LLMs frequently truncate large JSON tool calls. Cortex has a custom parser that detects broken JSON mid-stream, repairs it, or forces the LLM to self-correct on the next step.
*   **Quota Fallback:** If the primary Meta API hits a rate limit, it seamlessly hot-swaps to a fallback provider (e.g., OpenRouter, Ollama, or Gemini) to ensure the task completes.
*   **Live Mid-Execution Correction:** User feedback injected mid-run is absorbed, the plan is revised in-place, and completed steps stay intact. No full restart.

### 2. Advanced GUI Grounding (OCR + UIA)
*   Cortex uses **Set-of-Mark (SoM) prompting**. When you say "Click the submit button," Cortex takes a screenshot, runs **RapidOCR**, extracts **UI Automation (UIA)** trees, and draws numbered red bounding boxes over interactive elements.
*   The LLM clicks by `element_id` and provides an `expected_text` verification. If the text doesn't match, the click is *rejected* to prevent catastrophic misclicks.

### 3. Hybrid Memory System (LTM + Behavioral)
*   **Graph Memory:** Uses `NetworkX` to store entities and relations (`User -> LIVES_IN -> Mumbai`). It semantically deduplicates relations so it doesn't store "likes" and "prefers" as two different things.
*   **Behavioral Engine:** Silently learns your preferences over time. If you tell it "I prefer dark mode UI," it stores this as a behavioral pattern and injects it into future coding tasks automatically.

### 4. ZeroMQ IPC & PyQt5 UI
*   The Agent loop runs in the background, completely decoupled from the UI.
*   A **ZeroMQ** pub/sub architecture streams real-time thoughts, token counts, and tool execution statuses to a sleek, glassmorphism **PyQt5 "Island" UI** (similar to Apple's Dynamic Island).

---

## 📸 Screenshots
*(Yahan tu apne Agent Panel, Input Popup aur Typing Popup ki screenshots daal de)*

---

## 📦 Architecture & Tech Stack

*   **LLM Engine:** Meta Llama 3 (API/Instruct)
*   **UI:** PyQt5, ZeroMQ
*   **Computer Vision:** RapidOCR, PyAutoGUI
*   **Memory:** NetworkX, SentenceTransformers
*   **Voice:** Edge-TTS, Sarvam AI
*   **Comms:** Google API, Telethon, Baileys (Node.js)

---

## 🎬 Demo Video
*(Yahan apna 2-3 min ka Loom/YouTube video link daal de jo maine bola tha)*

---

## ⚙️ Getting Started (Local Setup)

Cortex is heavily optimized for **Windows 10/11**.

### 1. Requirements
*   Python 3.10+
*   Node.js (for WhatsApp bridge)
*   Meta API Key (or any OpenAI-compatible endpoint)

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/thekaifansari01/cortex-meta-agent.git
cd cortex-meta-agent

# Setup virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup CLI & Protocol (Run as Administrator)
python SetupRegistry.py
```

### 3. Environment Variables
Create a `.env` file in the root directory:
```env
# Meta AI (Llama 3) Configuration
AGENT_PRIMARY_PROVIDER="meta"
META_API_KEY="your_meta_api_key_here"
META_MODEL="llama-3-70b-instruct"

# Optional Fallbacks
GEMINI_API_KEY=""
OPENROUTER_API_KEY=""

# TTS & Image Generation
TTS_API_KEY=""
IMAGE_GEN_API_KEY=""
```

### 4. Run Cortex
```bash
cortex
```
*Press `Ctrl+Shift+J` anywhere in Windows to summon the input prompt.*

---

## 🛡️ Security & Safety

*   **Command Blocklist:** Cortex actively parses terminal commands and blocks dangerous operations (e.g., `rm -rf /`, `format C:`).
*   **Approval Gates:** Before sending emails, WhatsApp messages, or making API calls that cost money, Cortex pauses and asks for explicit user confirmation in the UI.
*   **Encrypted Tokens:** All OAuth tokens (Gmail, Calendar) are encrypted at rest using `cryptography.fernet`.

---

<div align="center">
  <i>Built with ❤️ for the Meta AI Developer Hackathon by Kaif Ansari.</i>
</div>