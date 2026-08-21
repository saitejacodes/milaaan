# Milaan v1.2.2 — BYO-LLM and reviewer onboarding

Version 1.2.2 does not change reconciliation, exception classification, or
functional metrics. It makes the optional explanation layer portable and the
submission runnable by someone who did not build it.

## Added

- Native adapters for Anthropic Messages and Google Gemini `generateContent`.
- OpenAI-compatible Chat Completions adapter with optional authentication for
  local servers.
- `ollama` shortcut with the standard local OpenAI-compatible base URL.
- Provider aliases: `openai`, `claude`, and `google`.
- Dependency-free `.env` loading; existing shell values always win.
- Configurable native/prompt JSON mode, timeouts, and output-token limit.
- Provider-specific request, response, token-usage, and fail-safe tests using
  local fake responses only.
- A complete README covering Track 04 fit, first-time setup on macOS, Linux,
  and Windows, offline use, BYO-key configuration, data files, verification,
  limitations, and troubleshooting.

## Safety retained

- No LLM call exists in the matching engine.
- Provider identity, endpoint, model, JSON mode, and prompt version participate
  in the persistent cache key; API keys do not.
- HTTP, configuration, response-shape, JSON, or content-firewall failure falls
  back to canonical exception language.
- Functional metrics remain byte-identical between mock and live modes.
- No real key, model response, or paid API call is included in the repository.

## Version boundary

The package and CLI are v1.2.2. The named synthetic benchmark intentionally
remains generator v1.2.1 so its golden dataset, denominators, and claims do not
move during this integration-only release.
