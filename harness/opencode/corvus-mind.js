/**
 * Corvus-Mind bridge plugin for opencode.
 *
 * Symlinked into ~/.config/opencode/plugin/. Translates opencode plugin
 * hooks into the Claude-Code-shaped stdin payloads the existing Python
 * harness scripts expect, so redaction, the work/personal exclusion wall,
 * capsule delivery, and attribution logging stay in ONE implementation
 * (harness/claude-code/*.py) for both harnesses.
 *
 * Mapping:
 *   chat.message (first of session) -> SessionStart  (capsules + roadmap policy)
 *   chat.message (every prompt)     -> UserPromptSubmit (ambient recall)
 *   tool.execute.before             -> PreToolUse (roadmap mutation gate)
 *   tool.execute.after              -> PostToolUse   (episode capture)
 *   event session.idle              -> Stop          (distill-ready marker)
 *
 * opencode has no Claude-style transcript JSONL, so this plugin writes a
 * distiller-compatible one ({"type":"user","message":{"content":...}} per
 * user prompt) under ~/.corvus-mind/transcripts/opencode/ and points the
 * Stop record's transcript_path at it.
 *
 * Same safety contract as the Python hooks: nothing here may ever break
 * a session accidentally. A roadmap denial deliberately throws from
 * tool.execute.before so the mapped mutation does not execute.
 */
import { spawn } from "node:child_process"
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const HARNESS_DIR = join(homedir(), "Projects/corvus/harness/claude-code")
const INJECT_HOOK = join(HARNESS_DIR, "memory_inject_hook.py")
const ROADMAP_HOOK = join(HARNESS_DIR, "roadmap_gate_hook.py")
const EPISODE_HOOK = join(HARNESS_DIR, "episode_hook.py")
const TRANSCRIPT_DIR = join(homedir(), ".corvus-mind/transcripts/opencode")
const HOOK_TIMEOUT_MS = 8000

function runHook(script, payload) {
  return new Promise((resolve) => {
    try {
      const proc = spawn("python3", [script], { stdio: ["pipe", "pipe", "ignore"] })
      let out = ""
      const timer = setTimeout(() => {
        try { proc.kill() } catch {}
        resolve(null)
      }, HOOK_TIMEOUT_MS)
      proc.stdout.on("data", (chunk) => { out += chunk })
      proc.on("close", () => { clearTimeout(timer); resolve(out || null) })
      proc.on("error", () => { clearTimeout(timer); resolve(null) })
      proc.stdin.write(JSON.stringify(payload))
      proc.stdin.end()
    } catch {
      resolve(null)
    }
  })
}

function contextFrom(raw) {
  if (!raw) return null
  try {
    return JSON.parse(raw)?.hookSpecificOutput?.additionalContext || null
  } catch {
    return null
  }
}

function gateFrom(raw) {
  if (!raw) return null
  try {
    return JSON.parse(raw)?.roadmapGate || null
  } catch {
    return null
  }
}

function promptText(parts) {
  return parts
    .filter((p) => p.type === "text" && !p.synthetic)
    .map((p) => p.text || "")
    .join("\n")
    .trim()
}

function transcriptPath(sessionID) {
  return join(TRANSCRIPT_DIR, `${sessionID}.jsonl`)
}

function logUserMessage(sessionID, text) {
  try {
    mkdirSync(TRANSCRIPT_DIR, { recursive: true })
    const line = JSON.stringify({ type: "user", message: { content: text } })
    appendFileSync(transcriptPath(sessionID), line + "\n")
  } catch {}
}

export const CorvusMindPlugin = async ({ directory }) => {
  const startedSessions = new Set()
  const base = (sessionID) => ({
    session_id: sessionID,
    cwd: directory,
    harness: "opencode",
  })

  return {
    "chat.message": async (input, output) => {
      try {
        const sessionID = input.sessionID
        if (!sessionID) return
        const prompt = promptText(output.parts)
        if (prompt) logUserMessage(sessionID, prompt)

        const contexts = []
        if (!startedSessions.has(sessionID)) {
          startedSessions.add(sessionID)
          for (const hook of [INJECT_HOOK, ROADMAP_HOOK]) {
            const raw = await runHook(hook, {
              ...base(sessionID),
              hook_event_name: "SessionStart",
            })
            const ctx = contextFrom(raw)
            if (ctx) contexts.push(ctx)
          }
        }
        if (prompt) {
          const raw = await runHook(INJECT_HOOK, {
            ...base(sessionID),
            hook_event_name: "UserPromptSubmit",
            prompt,
          })
          const ctx = contextFrom(raw)
          if (ctx) contexts.push(ctx)
        }
        if (!contexts.length) return

        output.parts.push({
          id: `prt_mind${Date.now().toString(36)}`,
          sessionID,
          messageID: output.message?.id,
          type: "text",
          synthetic: true,
          text:
            "<system-reminder>\n" + contexts.join("\n\n") + "\n</system-reminder>",
        })
      } catch {}
    },

    "tool.execute.before": async (input, output) => {
      if (!input.sessionID) return
      const raw = await runHook(ROADMAP_HOOK, {
        ...base(input.sessionID),
        hook_event_name: "PreToolUse",
        tool_name: input.tool,
        tool_input: output.args,
      })
      const gate = gateFrom(raw)
      if (gate?.decision === "block") {
        throw new Error(gate.reason)
      }
    },

    "tool.execute.after": async (input, output) => {
      try {
        if (!input.sessionID) return
        await runHook(EPISODE_HOOK, {
          ...base(input.sessionID),
          hook_event_name: "PostToolUse",
          tool_name: input.tool,
          tool_input: input.args,
          // opencode surfaces tool failures as thrown errors that skip this
          // hook, so a call that reaches here is treated as ok.
          tool_response: { output: String(output?.output ?? "").slice(0, 200) },
        })
      } catch {}
    },

    event: async ({ event }) => {
      try {
        if (event?.type !== "session.idle") return
        const sessionID = event.properties?.sessionID
        if (!sessionID) return
        await runHook(EPISODE_HOOK, {
          ...base(sessionID),
          hook_event_name: "Stop",
          transcript_path: transcriptPath(sessionID),
        })
      } catch {}
    },
  }
}
