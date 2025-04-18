# BlenderMCP x CSM.ai x LLM Agents (e.g. Cursor / Claude)

Connect Blender, CSM.ai and LLMs via the Model Context Protocol (MCP). This integration enables text and image based editing capabilities in Blender.

```
      ╭───────────╮       ╭───────────╮       ╭───────────╮
      │  CSM.ai   │◄━━━━━►│  Blender  │◄━━━━━►│  MCP UI   │
      │ 3D Assets │       │    MCP    │       │e.g.Cursor │
      ╰─────┬─────╯       ╰─────┬─────╯       ╰─────┬─────╯
            │                   │                   │
            ▼                   ▼                   ▼
      ╭─────────────────────────────────────────────────╮
      │                                                 │
      │               BLENDER ENGINE                    │
      │        Creative 3D Modeling Environment         │
      │                                                 │
      ╰─────────────────────────────────────────────────╯
```

https://github.com/user-attachments/assets/496c2c69-4779-4569-9be0-80b767702c4c

## Overview

BlenderMCP creates a bridge between Blender, CSM.ai and language models, allowing for powerful AI-assisted 3D modeling workflows.

## MCP Feature List ✨

- 🔍 Ability to quickly search from public or private CSM.ai sessions. This allows interactive MCP based development.

- 🏃 For any humanoid like mesh in the scene, ability to animate given a text prompt (from a fixed library set)

## Installation

1. Clone the repository:
   ```