/* ==========================================================================
   Automatic model failover

   Tries the preferred model for the task first, then walks the canonical chain:
   Claude -> OpenAI -> Gemini -> DeepSeek -> Qwen.  The caller supplies the
   transport (default: Puter.js) so the chain is easy to test without a network.

   Users only ever see a friendly recovery note.  Provider error text stays in
   the console and in the returned `attempts` metadata.
   ========================================================================== */

import { askAI, AIError, classifyError, friendlyAIError, modelLabel } from "./ai.js";
import {
  buildModelOrder,
  classifyTask,
  isSupportedModel,
  selectModel,
} from "./router.js";

export class AllModelsUnavailableError extends Error {
  constructor(attempts, options = {}) {
    super(
      options.message ||
        "The online models are unavailable right now. AQACT can still answer from local analysis."
    );
    this.name = "AllModelsUnavailableError";
    this.kind = "all_models_unavailable";
    this.attempts = attempts || [];
    this.task = options.task || "chat";
    this.recoveryNote =
      "No online model responded. Your conversation was kept and local analysis is still available.";
  }
}

/** Ordered model plan for a task, with the preferred model first. */
export function fallbackPlan(task, preferred) {
  return buildModelOrder(preferred || selectModel(task));
}

export function recoveryNote(preferred, used) {
  if (!used || used === preferred) return "";
  return `Answered with ${modelLabel(used)} as a backup model to keep you moving. Your conversation and dataset context were kept.`;
}

/**
 * Ask the model chain until one answers.
 *
 * @param {string} prompt
 * @param {{
 *   task?: string, model?: string, system?: string, history?: Array,
 *   image?: string, caller?: Function, onAttempt?: Function,
 *   timeoutMs?: number, temperature?: number
 * }} [options]
 * @returns {Promise<{text:string, model:string, task:string, attempts:Array,
 *   fallbackUsed:boolean, recoveryNote:string}>}
 */
export async function askWithFallback(prompt, options = {}) {
  const task = classifyTask(prompt, options.task);
  const preferred = isSupportedModel(options.model) ? String(options.model) : selectModel(task);
  const order = buildModelOrder(preferred);
  const caller = options.caller || askAI;
  const attempts = [];
  const history = Array.isArray(options.history) ? options.history.slice(-8) : [];

  for (let index = 0; index < order.length; index += 1) {
    const model = order[index];
    try {
      const result = await caller(prompt, model, {
        system: options.system,
        history,
        image: options.image,
        timeoutMs: options.timeoutMs,
        temperature: options.temperature,
      });
      const text = typeof result === "string" ? result : String(result || "");
      if (!text.trim()) {
        throw new AIError("The model returned an empty answer.", "empty", { model });
      }
      const used = model;
      return {
        text: text.trim(),
        model: used,
        task,
        attempts,
        fallbackUsed: used !== preferred,
        recoveryNote: recoveryNote(preferred, used),
      };
    } catch (error) {
      const kind = classifyError(error);
      attempts.push({ model, kind });
      if (typeof options.onAttempt === "function") {
        try {
          options.onAttempt({ model, kind, index, total: order.length });
        } catch (_) {
          /* an observer must not break failover */
        }
      }
      if (typeof console !== "undefined" && typeof console.warn === "function") {
        // Deliberately terse: no provider payloads or credentials in the log.
        console.warn(`[AQACT AI] ${model} unavailable (${kind})`);
      }
    }
  }

  throw new AllModelsUnavailableError(attempts, { task });
}

export { friendlyAIError };
